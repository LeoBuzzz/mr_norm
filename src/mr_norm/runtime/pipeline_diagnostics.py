from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import PreparedQueryPlan, PreparedToolQuery, RuntimeRequest
from mr_norm.runtime.pipeline_features import (
    intent_routing_enabled,
    normalize_routing_question_type,
)

RETRIEVAL_POOL_MULTIPLIER = 2
MAX_RETRIEVAL_LIMIT = 100
DEFAULT_FINAL_ANSWER_LIMIT = 25
MIN_RESOLVED_DOC_FINAL_SLOTS = 8

DOC_POINT_BOOST_EXACT = 0
DOC_POINT_BOOST_DOC = 1
DOC_POINT_BOOST_POINT = 2
DOC_POINT_BOOST_OTHER = 3


@dataclass
class PipelineDiagnostics:
    retrieval_limit: int = 0
    final_answer_limit: int = 0
    wide_pool_enabled: bool = False
    early_doc_resolver_applied: bool = False
    early_doc_resolver_reason: str = ""
    intent_routing_applied: bool = False
    intent_routing_mode: str = ""
    intent_routing_tools: list[str] = field(default_factory=list)
    doc_point_boost_applied: bool = False
    doc_point_boost_moves: int = 0
    retry_triggered: bool = False
    retry_reason: str = ""
    retry_items_added: int = 0
    source_ranks_summary: dict[str, Any] = field(default_factory=dict)
    gold_doc_in_top_n: bool | None = None
    resolved_doc_id: str = ""
    resolved_point: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_retrieval_limits(limit: int) -> tuple[int, int, bool]:
    if limit <= 0:
        return limit, limit, False
    retrieval_limit = min(max(limit, limit * RETRIEVAL_POOL_MULTIPLIER), MAX_RETRIEVAL_LIMIT)
    final_answer_limit = min(
        max(DEFAULT_FINAL_ANSWER_LIMIT, limit // 2),
        retrieval_limit,
        limit,
    )
    wide_pool = retrieval_limit > limit
    return retrieval_limit, final_answer_limit, wide_pool


def resolved_doc_point_filters(request: RuntimeRequest) -> tuple[str, str, str]:
    filters = dict(request.filters or {})
    doc_id = str(filters.get("doc_id") or "")
    doc_name = str(filters.get("doc_name") or "")
    point_number = str(filters.get("point_number") or "")
    if request.prepared_plan:
        if request.prepared_plan.resolved_doc_names and not doc_name:
            doc_name = request.prepared_plan.resolved_doc_names[0]
        if request.prepared_plan.point_number_hints and not point_number:
            point_number = str(request.prepared_plan.point_number_hints[0])
        catalog_id = str(request.prepared_plan.document_resolution.catalog_id or "")
        if catalog_id and not catalog_id.startswith("knowledge:") and not doc_id:
            doc_id = catalog_id
    return doc_id, doc_name, point_number


def select_items_for_final_answer(
    ranked_items: list[RetrievedItem],
    *,
    request: RuntimeRequest,
    limit: int,
    min_resolved_doc_slots: int = MIN_RESOLVED_DOC_FINAL_SLOTS,
) -> list[RetrievedItem]:
    """Pick final-answer evidence from the wide pool, keeping resolved-doc coverage."""
    if limit <= 0 or not ranked_items:
        return []

    doc_id, doc_name, point_number = resolved_doc_point_filters(request)
    if not (doc_id or doc_name) and request.prepared_plan and request.prepared_plan.resolved_doc_names:
        doc_name = request.prepared_plan.resolved_doc_names[0]
        catalog_id = str(request.prepared_plan.document_resolution.catalog_id or "")
        if catalog_id and not catalog_id.startswith("knowledge:"):
            doc_id = catalog_id

    if not (doc_id or doc_name):
        return ranked_items[:limit]

    doc_items = [
        item
        for item in ranked_items
        if item.chunk_id
        and _doc_point_boost_tier(item, doc_id=doc_id, doc_name=doc_name, point_number=point_number)
        <= DOC_POINT_BOOST_DOC
    ]
    if not doc_items:
        return ranked_items[:limit]

    doc_items.sort(
        key=lambda item: (
            _doc_point_boost_tier(
                item,
                doc_id=doc_id,
                doc_name=doc_name,
                point_number=point_number,
            ),
            -float(item.score or 0.0),
            item.chunk_id,
        )
    )
    doc_slots = min(len(doc_items), max(min_resolved_doc_slots, limit // 3))
    selected: list[RetrievedItem] = []
    seen: set[str] = set()

    for item in doc_items[:doc_slots]:
        if item.chunk_id:
            seen.add(item.chunk_id)
        selected.append(item)

    for item in ranked_items:
        if len(selected) >= limit:
            break
        key = item.chunk_id
        if key and key in seen:
            continue
        selected.append(item)
        if key:
            seen.add(key)

    return selected[:limit]


def normalize_doc_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalize_point_key(value: str) -> str:
    text = (value or "").strip().lower().replace("_", ".")
    return re.sub(r"\s+", "", text)


def doc_names_match(expected: str, actual: str) -> bool:
    left = normalize_doc_key(expected)
    right = normalize_doc_key(actual)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def points_match(expected: str, actual: str) -> bool:
    left = normalize_point_key(expected)
    right = normalize_point_key(actual)
    if not left or not right:
        return False
    return left == right or left.startswith(f"{right}.") or right.startswith(f"{left}.")


def try_early_deterministic_doc_resolution(
    candidates: list[dict[str, Any]],
    *,
    original_query: str,
    explicit_doc_name: str = "",
    min_confidence: float = 0.55,
    ambiguity_gap: float = 0.08,
) -> tuple[list[str], str, float, bool, bool, str]:
    """Return (doc_names, catalog_id, confidence, ambiguous, applied, reason)."""
    if explicit_doc_name.strip():
        return [], "", 0.0, False, False, "explicit_doc_name_filter_present"

    if not candidates:
        return [], "", 0.0, False, False, "no_candidates"

    top = candidates[0]
    top_name = str(top.get("doc_name") or "")
    top_id = str(top.get("catalog_id") or "")
    top_score = float(top.get("score") or 0.0)
    second_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    gap = top_score - second_score
    reasons = [str(item) for item in top.get("reasons") or []]

    if any(reason.startswith("order_number:") for reason in reasons) and top_score >= min_confidence:
        return (
            [top_name],
            top_id,
            top_score,
            False,
            True,
            f"early_resolver:order_number:score={top_score:.2f}",
        )

    if re.search(r"(?:№|n[oº\.]\s*)\s*\d+", original_query, flags=re.IGNORECASE) and top_score >= min_confidence and gap >= ambiguity_gap:
        return (
            [top_name],
            top_id,
            top_score,
            gap < ambiguity_gap,
            True,
            f"early_resolver:act_number_in_query:score={top_score:.2f},gap={gap:.2f}",
        )

    if top_score >= 0.72 and gap >= ambiguity_gap:
        return (
            [top_name],
            top_id,
            top_score,
            False,
            True,
            f"early_resolver:high_confidence:score={top_score:.2f},gap={gap:.2f}",
        )

    return [], "", top_score, gap < ambiguity_gap, False, "early_resolver:not_confident_enough"


def apply_intent_tool_routing(
    prepared_tool_queries: tuple[PreparedToolQuery, ...],
    *,
    question_type: str,
    doc_scoped: bool,
    point_number_hints: tuple[str, ...] | list[str],
    original_query: str = "",
) -> tuple[tuple[str, ...], tuple[PreparedToolQuery, ...], str]:
    by_tool = {entry.tool_name: entry for entry in prepared_tool_queries}
    hints = list(point_number_hints or [])
    if not intent_routing_enabled():
        order = ["point", "payload", "vector"]
        mode_label = "default_muted"
    else:
        mode = normalize_routing_question_type(question_type, original_query)

        if mode == "point_lookup" or (doc_scoped and hints):
            order = ["point", "payload", "vector"]
            mode_label = "point_lookup_path" if mode == "point_lookup" else "doc_scoped_point_path"
        elif mode == "requirement" and not doc_scoped:
            from mr_norm.runtime.pipeline_features import query_has_explicit_doc_reference

            if query_has_explicit_doc_reference(original_query):
                order = ["point", "payload", "vector"]
            else:
                order = ["vector", "payload", "point"]
            mode_label = "requirement_broad"
        elif mode in {"requirement", "procedure", "factual"} and doc_scoped:
            order = ["point", "payload", "vector"] if hints else ["payload", "vector"]
            mode_label = f"{mode}_doc_scoped"
        elif mode in {"document_lookup", "regulation_scope"}:
            order = ["payload", "vector", "point"]
            mode_label = f"{mode}_hybrid"
        elif mode == "definition":
            order = ["vector", "payload", "point"]
            mode_label = "definition"
        else:
            order = ["point", "payload", "vector"]
            mode_label = "default"

    selected: list[PreparedToolQuery] = []
    fallback_query = (original_query or "").strip()
    if not fallback_query and prepared_tool_queries:
        for entry in prepared_tool_queries:
            if entry.queries:
                fallback_query = str(entry.queries[0]).strip()
                break
    for tool_name in order:
        entry = by_tool.get(tool_name)
        if entry is None:
            if tool_name == "point" and (
                (hints and doc_scoped) or mode_label == "requirement_broad"
            ):
                point_queries = tuple(hints) if hints else ((fallback_query,) if fallback_query else ())
                if point_queries:
                    selected.append(PreparedToolQuery(tool_name="point", queries=point_queries))
            continue
        selected.append(entry)

    if not selected:
        selected = list(prepared_tool_queries)
        mode_label = "fallback_unchanged"

    tools = tuple(dict.fromkeys(entry.tool_name for entry in selected))
    return tools, tuple(selected), mode_label


def _doc_point_boost_tier(
    item: RetrievedItem,
    *,
    doc_id: str,
    doc_name: str,
    point_number: str,
) -> int:
    same_doc = bool(doc_id and item.doc_id == doc_id) or (
        bool(doc_name) and doc_names_match(doc_name, item.doc_name)
    )
    same_point = bool(point_number) and points_match(point_number, item.point_number)
    if same_doc and same_point:
        return DOC_POINT_BOOST_EXACT
    if same_doc:
        return DOC_POINT_BOOST_DOC
    if same_point:
        return DOC_POINT_BOOST_POINT
    return DOC_POINT_BOOST_OTHER


def rerank_items_for_doc_point(
    items: list[RetrievedItem],
    *,
    doc_id: str = "",
    doc_name: str = "",
    point_number: str = "",
    limit: int | None = None,
) -> tuple[list[RetrievedItem], int]:
    if not items or not (doc_id or doc_name or point_number):
        return items, 0

    original_order = [item.chunk_id for item in items if item.chunk_id]
    ranked = sorted(
        items,
        key=lambda item: (
            _doc_point_boost_tier(
                item,
                doc_id=doc_id,
                doc_name=doc_name,
                point_number=point_number,
            ),
            -float(item.score or 0.0),
            item.chunk_id,
        ),
    )
    if limit is not None:
        ranked = ranked[:limit]
    new_order = [item.chunk_id for item in ranked if item.chunk_id]
    moves = sum(1 for idx, chunk_id in enumerate(new_order) if idx < len(original_order) and original_order[idx] != chunk_id)
    return ranked, moves


def should_apply_doc_point_boost(request: RuntimeRequest) -> bool:
    """Apply boost only when a confirmed doc_id filter is active."""
    from mr_norm.runtime.pipeline_features import doc_point_boost_enabled

    if not doc_point_boost_enabled():
        return False
    filters = dict(request.filters or {})
    doc_id = str(filters.get("doc_id") or "").strip()
    if not doc_id:
        return False
    if request.prepared_plan and request.prepared_plan.ambiguous:
        return False
    return True


def summarize_source_ranks(items: list[RetrievedItem], *, top_n: int = 15) -> dict[str, Any]:
    hybrid_items = [item for item in items[:top_n] if item.source_tool == "hybrid_rrf"]
    vector_only = payload_only = both = 0
    vector_ranks: list[int] = []
    payload_ranks: list[int] = []

    for item in hybrid_items:
        matched = item.matched if isinstance(item.matched, dict) else {}
        source_ranks = matched.get("source_ranks") if isinstance(matched.get("source_ranks"), dict) else {}
        vector_rank = source_ranks.get("vector")
        payload_rank = source_ranks.get("payload")
        if vector_rank is not None:
            vector_ranks.append(int(vector_rank))
        if payload_rank is not None:
            payload_ranks.append(int(payload_rank))
        if vector_rank is not None and payload_rank is not None:
            both += 1
        elif vector_rank is not None:
            vector_only += 1
        elif payload_rank is not None:
            payload_only += 1

    return {
        "top_n": top_n,
        "hybrid_items": len(hybrid_items),
        "vector_only": vector_only,
        "payload_only": payload_only,
        "both_vector_and_payload": both,
        "mean_vector_rank": round(sum(vector_ranks) / len(vector_ranks), 2) if vector_ranks else None,
        "mean_payload_rank": round(sum(payload_ranks) / len(payload_ranks), 2) if payload_ranks else None,
        "items": [
            {
                "rank": index,
                "chunk_id": item.chunk_id,
                "source_tool": item.source_tool,
                "source_ranks": (item.matched or {}).get("source_ranks") if isinstance(item.matched, dict) else None,
            }
            for index, item in enumerate(items[:top_n], start=1)
        ],
    }


def should_retry_doc_point_lookup(
    *,
    filters: dict[str, Any],
    prepared_plan: PreparedQueryPlan | None,
    ranked_items: list[RetrievedItem],
    tool_results: dict[str, Any],
    top_n: int = 15,
) -> tuple[bool, str]:
    doc_id = str(filters.get("doc_id") or "").strip()
    doc_name = str(filters.get("doc_name") or "").strip()
    point_number = str(filters.get("point_number") or "").strip()
    if prepared_plan and prepared_plan.point_number_hints and not point_number:
        point_number = str(prepared_plan.point_number_hints[0])

    if not (doc_id or doc_name):
        return False, "retry_skip:no_doc_filter"
    if not point_number:
        return False, "retry_skip:no_point_hint"

    point_result = tool_results.get("point") if isinstance(tool_results, dict) else None
    point_items = []
    if point_result is not None:
        point_items = getattr(point_result, "items", None) or (point_result.get("items") if isinstance(point_result, dict) else []) or []

    top_slice = ranked_items[:top_n]
    has_exact = any(
        _doc_point_boost_tier(item, doc_id=doc_id, doc_name=doc_name, point_number=point_number)
        == DOC_POINT_BOOST_EXACT
        for item in top_slice
    )
    if has_exact:
        return False, "retry_skip:exact_match_in_top_n"

    if not point_items:
        return True, "retry:point_tool_empty_with_doc_and_point"

    if doc_id or doc_name:
        has_doc_in_top = any(
            _doc_point_boost_tier(item, doc_id=doc_id, doc_name=doc_name, point_number=point_number)
            <= DOC_POINT_BOOST_DOC
            for item in top_slice
        )
        if has_doc_in_top and not has_exact:
            return True, "retry:doc_in_top_but_point_missing"

    return False, "retry_skip:not_needed"


def should_retry_doc_scoped_lookup(
    *,
    filters: dict[str, Any],
    prepared_plan: PreparedQueryPlan | None,
    ranked_items: list[RetrievedItem],
    tool_results: dict[str, Any],
    top_n: int = 25,
    question_type: str = "",
) -> tuple[bool, str]:
    qt = (question_type or "").strip()
    if qt not in {"requirement", "procedure", "factual", "document_scope"}:
        return False, "retry_skip:not_doc_scoped_retry_type"

    doc_id = str(filters.get("doc_id") or "").strip()
    doc_name = str(filters.get("doc_name") or "").strip()
    point_number = ""
    if prepared_plan:
        if prepared_plan.resolved_doc_names and not doc_name:
            doc_name = prepared_plan.resolved_doc_names[0]
        if prepared_plan.point_number_hints and not point_number:
            point_number = str(prepared_plan.point_number_hints[0])
        catalog_id = str(prepared_plan.document_resolution.catalog_id or "")
        if catalog_id and not catalog_id.startswith("knowledge:") and not doc_id:
            doc_id = catalog_id

    if not (doc_id or doc_name):
        return False, "retry_skip:no_doc_context"

    top_slice = ranked_items[:top_n]
    has_doc_in_top = any(
        _doc_point_boost_tier(item, doc_id=doc_id, doc_name=doc_name, point_number=point_number)
        <= DOC_POINT_BOOST_DOC
        for item in top_slice
    )
    if has_doc_in_top:
        return False, "retry_skip:resolved_doc_in_final_top_n"

    payload_result = tool_results.get("payload") if isinstance(tool_results, dict) else None
    payload_items = []
    if payload_result is not None:
        payload_items = (
            getattr(payload_result, "items", None)
            or (payload_result.get("items") if isinstance(payload_result, dict) else [])
            or []
        )

    if not payload_items:
        return True, "retry:payload_empty_with_resolved_doc"
    return True, "retry:resolved_doc_missing_from_final_top_n"


def _match_vector_top_to_candidate(
    ranked_items: list[RetrievedItem],
    candidates: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    min_score: float,
    top_k: int = 15,
) -> dict[str, Any] | None:
    if not ranked_items or not candidates:
        return None
    top_doc_ids = {
        str(item.doc_id or "")
        for item in ranked_items[:top_k]
        if str(item.doc_id or "").strip()
    }
    for candidate in candidates:
        score = float(candidate.get("score") or 0.0)
        catalog_id = str(candidate.get("catalog_id") or "")
        if score < min_score or not catalog_id or catalog_id.startswith("knowledge:"):
            continue
        if catalog_id in top_doc_ids:
            return candidate
    return None


def should_retry_soft_catalog_lookup(
    *,
    filters: dict[str, Any],
    prepared_plan: PreparedQueryPlan | None,
    ranked_items: list[RetrievedItem],
    top_n: int = 25,
) -> tuple[bool, str, dict[str, Any]]:
    from mr_norm.runtime.pipeline_features import effective_doc_confidence_threshold

    doc_id = str(filters.get("doc_id") or "").strip()
    doc_name = str(filters.get("doc_name") or "").strip()
    if doc_id or doc_name:
        return False, "retry_skip:doc_filter_active", {}

    if prepared_plan is None:
        return False, "retry_skip:no_plan", {}

    original_query = prepared_plan.original_query or ""
    min_conf = effective_doc_confidence_threshold(original_query)
    candidates = list(prepared_plan.candidates or ())
    candidate: dict[str, Any] | None = candidates[0] if candidates else None

    vector_match = _match_vector_top_to_candidate(
        ranked_items,
        candidates,
        min_score=min_conf,
    )
    if vector_match is not None:
        candidate = vector_match
    elif candidate is None:
        resolution = prepared_plan.document_resolution
        if resolution.catalog_id and not str(resolution.catalog_id).startswith("knowledge:"):
            candidate = {
                "catalog_id": resolution.catalog_id,
                "doc_name": resolution.doc_name,
                "score": resolution.confidence or prepared_plan.confidence,
            }

    if candidate is None:
        return False, "retry_skip:no_catalog_candidate", {}

    score = float(candidate.get("score") or prepared_plan.confidence or 0.0)
    if score < min_conf:
        return False, "retry_skip:catalog_below_threshold", {}

    if prepared_plan.ambiguous and len(candidates) > 1:
        top_score = float(candidates[0].get("score") or 0.0)
        second_score = float(candidates[1].get("score") or 0.0)
        if top_score - second_score < AMBIGUITY_SCORE_GAP:
            return False, "retry_skip:ambiguous_catalog", {}

    catalog_id = str(candidate.get("catalog_id") or "").strip()
    catalog_name = str(candidate.get("doc_name") or "").strip()
    if catalog_id.startswith("knowledge:"):
        return False, "retry_skip:knowledge_only", {}

    top_slice = ranked_items[:top_n]
    has_doc_in_top = any(
        _doc_point_boost_tier(
            item,
            doc_id=catalog_id,
            doc_name=catalog_name,
            point_number="",
        )
        <= DOC_POINT_BOOST_DOC
        for item in top_slice
    )
    if has_doc_in_top:
        return False, "retry_skip:catalog_doc_already_in_top_n", {}

    retry_filters: dict[str, Any] = {}
    if catalog_id:
        retry_filters["doc_id"] = catalog_id
    elif catalog_name:
        retry_filters["doc_name"] = catalog_name
    else:
        return False, "retry_skip:no_retry_filters", {}

    return True, f"retry:soft_catalog_candidate:score={score:.2f}", retry_filters


def merge_retry_items(
    primary: list[RetrievedItem],
    retry_items: list[RetrievedItem],
    *,
    limit: int,
) -> tuple[list[RetrievedItem], int]:
    seen: set[str] = set()
    merged: list[RetrievedItem] = []
    added = 0
    for item in retry_items + primary:
        key = item.chunk_id or f"{item.doc_name}:{item.point_number}:{item.text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    added = sum(
        1
        for item in retry_items
        if item.chunk_id and item.chunk_id not in {existing.chunk_id for existing in primary if existing.chunk_id}
    )
    return merged, added


def build_pipeline_diagnostics(
    *,
    request: RuntimeRequest,
    ranked_items: list[RetrievedItem],
    diagnostics: PipelineDiagnostics,
) -> dict[str, Any]:
    payload = diagnostics.to_dict()
    filters = dict(request.filters or {})
    doc_id = str(filters.get("doc_id") or "")
    doc_name = str(filters.get("doc_name") or "")
    if request.prepared_plan and request.prepared_plan.resolved_doc_names and not doc_name:
        doc_name = request.prepared_plan.resolved_doc_names[0]
    if request.prepared_plan and request.prepared_plan.document_resolution.catalog_id and not doc_id:
        catalog_id = str(request.prepared_plan.document_resolution.catalog_id)
        if not catalog_id.startswith("knowledge:"):
            doc_id = catalog_id

    payload["source_ranks_summary"] = summarize_source_ranks(
        ranked_items,
        top_n=min(diagnostics.final_answer_limit or 15, len(ranked_items) or 15),
    )
    payload["resolved_doc_id"] = doc_id
    payload["resolved_point"] = str(filters.get("point_number") or "")
    if doc_id or doc_name:
        payload["gold_doc_in_top_n"] = any(
            _doc_point_boost_tier(item, doc_id=doc_id, doc_name=doc_name, point_number=payload["resolved_point"])
            <= DOC_POINT_BOOST_DOC
            for item in ranked_items[: diagnostics.final_answer_limit or 15]
        )
    return payload
