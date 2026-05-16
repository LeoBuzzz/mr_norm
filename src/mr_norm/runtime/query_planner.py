from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCandidate,
    extract_point_number_hint,
    find_catalog_candidates,
    load_default_document_catalog,
    normalize_catalog_text,
)
from mr_norm.retrieval.document_knowledge import (
    DocumentKnowledgeIndex,
    KnowledgeCandidate,
    find_knowledge_candidates,
    load_document_knowledge,
    match_terms_in_query,
)
from mr_norm.runtime.contracts import (
    DocumentResolution,
    PreparedQueryPlan,
    PreparedToolQuery,
    QueryPlannerTrace,
    QueryUnderstandingResult,
    QueryUnderstandingTrace,
)
from mr_norm.runtime.llm_providers import chat_json_with_model_fallback
from mr_norm.runtime.llm_profiles import resolve_role_models, resolve_role_profile
from mr_norm.runtime.prompts import load_prompt_pack_by_role

MIN_DOC_CONFIDENCE = 0.55
AMBIGUITY_SCORE_GAP = 0.08
ALLOWED_TOOLS = frozenset({"point", "payload", "vector"})
MAX_QUERIES_PER_TOOL = 4


def _merge_candidates(
    catalog: DocumentCatalog,
    catalog_candidates: list[DocumentCandidate],
    knowledge_candidates: list[KnowledgeCandidate],
) -> list[dict[str, Any]]:
    by_doc_name = catalog.by_doc_name()
    merged: dict[str, dict[str, Any]] = {}

    for candidate in catalog_candidates:
        merged[candidate.doc_name] = {
            "catalog_id": candidate.catalog_id,
            "doc_name": candidate.doc_name,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "annotation": "",
            "source": "catalog",
        }

    for candidate in knowledge_candidates:
        entry = catalog.by_doc_name().get(candidate.doc_name)
        catalog_id = entry.catalog_id if entry else f"knowledge:{candidate.doc_id}"
        current = merged.get(candidate.doc_name)
        if current is None:
            merged[candidate.doc_name] = {
                "catalog_id": catalog_id,
                "doc_name": candidate.doc_name,
                "score": candidate.score,
                "reasons": list(candidate.reasons),
                "annotation": candidate.annotation,
                "source": "knowledge",
            }
            continue
        current["score"] = max(float(current["score"]), candidate.score)
        current["reasons"] = list(dict.fromkeys([*current["reasons"], *candidate.reasons]))
        if candidate.annotation:
            current["annotation"] = candidate.annotation
        current["source"] = "catalog+knowledge" if current["source"] == "catalog" else current["source"]

    ranked = sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)
    return ranked


def _deterministic_resolve(candidates: list[dict[str, Any]]) -> tuple[list[str], float, bool, list[str]]:
    warnings: list[str] = []
    if not candidates:
        return [], 0.0, False, ["no document candidates matched the query"]

    top = candidates[0]
    second_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    top_score = float(top["score"])
    ambiguous = len(candidates) > 1 and (top_score - second_score) < AMBIGUITY_SCORE_GAP
    confidence = min(1.0, top_score)

    if ambiguous:
        warnings.append("document resolution ambiguous; search will run without doc_name filter")
        return [], confidence, True, warnings

    if confidence < MIN_DOC_CONFIDENCE:
        warnings.append(
            f"document resolution confidence {confidence:.2f} below threshold; "
            "search will run without doc_name filter"
        )
        return [], confidence, False, warnings

    return [str(top["doc_name"])], confidence, False, warnings


def _normalize_tool_queries(raw: Any, original_query: str, significant_words: list[str]) -> dict[str, list[str]]:
    queries: dict[str, list[str]] = {tool: [] for tool in ALLOWED_TOOLS}
    if not isinstance(raw, dict):
        raw = {}

    for tool_name in ALLOWED_TOOLS:
        values = raw.get(tool_name, [])
        if isinstance(values, str):
            values = [part.strip() for part in values.split(",") if part.strip()]
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for value in values:
            text = re.sub(r"\s+", " ", str(value).strip())
            if text and text not in cleaned:
                cleaned.append(text)
        queries[tool_name] = cleaned[:MAX_QUERIES_PER_TOOL]

    core_terms = [original_query.strip(), *significant_words]
    for tool_name in ("payload", "vector"):
        if not queries[tool_name]:
            queries[tool_name] = [term for term in core_terms if term][:MAX_QUERIES_PER_TOOL]
        elif original_query.strip() and original_query.strip() not in queries[tool_name]:
            queries[tool_name] = [original_query.strip(), *queries[tool_name]][:MAX_QUERIES_PER_TOOL]

    return queries


def _build_tool_query_objects(
    tool_queries: dict[str, list[str]],
    *,
    point_number_hints: list[str],
    resolved_doc_names: list[str],
) -> tuple[tuple[str, ...], tuple[PreparedToolQuery, ...]]:
    selected: list[str] = []
    prepared: list[PreparedToolQuery] = []

    if point_number_hints and resolved_doc_names:
        selected.append("point")
        prepared.append(PreparedToolQuery(tool_name="point", queries=tuple(point_number_hints)))

    for tool_name in ("payload", "vector"):
        queries = [query for query in tool_queries.get(tool_name, []) if query.strip()]
        if not queries:
            continue
        selected.append(tool_name)
        prepared.append(PreparedToolQuery(tool_name=tool_name, queries=tuple(queries)))

    if not selected:
        fallback_query = tool_queries.get("vector", []) or tool_queries.get("payload", [])
        query = fallback_query[0] if fallback_query else ""
        if query:
            selected = ["payload", "vector"]
            prepared = [
                PreparedToolQuery(tool_name="payload", queries=(query,)),
                PreparedToolQuery(tool_name="vector", queries=(query,)),
            ]

    return tuple(dict.fromkeys(selected)), tuple(prepared)


def _normalize_llm_payload(
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    allowed_ids = {str(item["catalog_id"]) for item in candidates}
    catalog_by_id = {str(item["catalog_id"]): item for item in candidates}

    raw_ids = payload.get("selected_catalog_ids", [])
    if isinstance(raw_ids, str):
        raw_ids = [part.strip() for part in raw_ids.split(",") if part.strip()]
    if not isinstance(raw_ids, list):
        raise ValueError("selected_catalog_ids must be a list or string")

    selected_ids: list[str] = []
    for entry in raw_ids:
        catalog_id = str(entry).strip()
        if catalog_id in allowed_ids and catalog_id not in selected_ids:
            selected_ids.append(catalog_id)
        elif catalog_id:
            warnings.append(f"ignored unknown catalog_id: {catalog_id!r}")

    concepts_raw = payload.get("concepts", [])
    if isinstance(concepts_raw, str):
        concepts = [part.strip() for part in concepts_raw.split(",") if part.strip()]
    elif isinstance(concepts_raw, list):
        concepts = [str(item).strip() for item in concepts_raw if str(item).strip()]
    else:
        concepts = []

    significant_raw = payload.get("significant_words", [])
    if isinstance(significant_raw, str):
        significant_words = [part.strip() for part in significant_raw.split(",") if part.strip()]
    elif isinstance(significant_raw, list):
        significant_words = [str(item).strip() for item in significant_raw if str(item).strip()]
    else:
        significant_words = []

    point_hints_raw = payload.get("point_number_hints", [])
    if isinstance(point_hints_raw, str):
        point_number_hints = [part.strip() for part in point_hints_raw.split(",") if part.strip()]
    elif isinstance(point_hints_raw, list):
        point_number_hints = [str(item).strip() for item in point_hints_raw if str(item).strip()]
    else:
        point_number_hints = []

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
        warnings.append("invalid confidence value; defaulted to 0.0")

    llm_warnings = payload.get("warnings", [])
    if isinstance(llm_warnings, list):
        warnings.extend(str(item) for item in llm_warnings)
    elif isinstance(llm_warnings, str) and llm_warnings.strip():
        warnings.append(llm_warnings.strip())

    resolved_doc_names = [
        str(catalog_by_id[catalog_id]["doc_name"])
        for catalog_id in selected_ids
        if catalog_id in catalog_by_id
    ]
    return {
        "question_type": str(payload.get("question_type") or "factual"),
        "answer_shape": str(payload.get("answer_shape") or "narrow"),
        "concepts": concepts,
        "significant_words": significant_words,
        "resolved_doc_names": resolved_doc_names,
        "point_number_hints": point_number_hints,
        "confidence": max(0.0, min(1.0, confidence)),
        "tool_queries": payload.get("tool_queries", {}),
    }, warnings


def _llm_plan(
    query: str,
    candidates: list[dict[str, Any]],
    matched_terms: list[str],
    *,
    llm_provider: str,
    keys_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    pack = load_prompt_pack_by_role("query_planning")
    profile = resolve_role_profile(llm_provider, "query_planning")
    payload = chat_json_with_model_fallback(
        llm_provider,
        resolve_role_models(llm_provider, "query_planning"),
        keys_path=keys_path,
        system_prompt=str(pack.get("prompt") or ""),
        user_payload={
            "query": query,
            "candidates": candidates,
            "matched_terms": matched_terms,
            "output_contract": pack.get("output_contract"),
        },
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
    )
    return _normalize_llm_payload(payload, candidates)


def prepare_query(
    query: str,
    *,
    catalog: DocumentCatalog,
    knowledge: DocumentKnowledgeIndex,
    filters: dict[str, Any] | None = None,
    mode: str = "auto",
    llm_provider: str = "none",
    keys_path: Path | None = None,
) -> PreparedQueryPlan:
    original_query = (query or "").strip()
    if mode == "off" or not original_query:
        return PreparedQueryPlan(
            original_query=original_query,
            trace=QueryPlannerTrace(mode="off", resolver="none"),
        )

    explicit_doc_name = str((filters or {}).get("doc_name") or "").strip()
    point_hint = extract_point_number_hint(original_query)
    point_number_hints = [point_hint] if point_hint else []
    matched_terms = match_terms_in_query(original_query, knowledge)

    catalog_candidates = find_catalog_candidates(
        original_query,
        catalog,
        explicit_doc_name=explicit_doc_name,
        limit=8,
    )
    knowledge_candidates = find_knowledge_candidates(original_query, knowledge, limit=12)
    candidates = _merge_candidates(catalog, catalog_candidates, knowledge_candidates)

    resolver = "deterministic"
    warnings: list[str] = []
    resolved_doc_names: list[str] = []
    confidence = 0.0
    ambiguous = False
    question_type = "point_lookup" if point_number_hints else "factual"
    answer_shape = "narrow"
    concepts: list[str] = list(matched_terms[:8])
    significant_words: list[str] = list(matched_terms[:12])
    tool_queries: dict[str, list[str]] = {tool: [] for tool in ALLOWED_TOOLS}

    if mode == "llm" and llm_provider != "none" and candidates:
        resolver = "llm"
        try:
            llm_data, llm_warnings = _llm_plan(
                original_query,
                candidates,
                matched_terms,
                llm_provider=llm_provider,
                keys_path=keys_path,
            )
            warnings.extend(llm_warnings)
            question_type = llm_data.get("question_type", question_type)
            answer_shape = llm_data.get("answer_shape", answer_shape)
            concepts = list(dict.fromkeys([*concepts, *llm_data.get("concepts", [])]))
            significant_words = list(
                dict.fromkeys([*significant_words, *llm_data.get("significant_words", [])])
            )
            point_number_hints = list(
                dict.fromkeys(point_number_hints + llm_data.get("point_number_hints", []))
            )
            confidence = float(llm_data.get("confidence", 0.0))
            candidate_names = llm_data.get("resolved_doc_names", [])
            if confidence >= MIN_DOC_CONFIDENCE and len(candidate_names) == 1:
                resolved_doc_names = candidate_names
            elif candidate_names:
                ambiguous = len(candidate_names) > 1
                warnings.append(
                    "llm document resolution below threshold or ambiguous; doc_name filter not applied"
                )
            else:
                warnings.append("llm returned no verified document; doc_name filter not applied")
            tool_queries = _normalize_tool_queries(
                llm_data.get("tool_queries"),
                original_query,
                significant_words,
            )
        except Exception as exc:
            warnings.append(f"llm query planning failed: {type(exc).__name__}: {exc}")
            resolved_doc_names, confidence, ambiguous, det_warnings = _deterministic_resolve(candidates)
            warnings.extend(det_warnings)
            resolver = "deterministic_fallback"
            tool_queries = _normalize_tool_queries({}, original_query, significant_words)
    else:
        resolved_doc_names, confidence, ambiguous, det_warnings = _deterministic_resolve(candidates)
        warnings.extend(det_warnings)
        tool_queries = _normalize_tool_queries({}, original_query, significant_words)

    selected_tools, prepared_tool_queries = _build_tool_query_objects(
        tool_queries,
        point_number_hints=point_number_hints,
        resolved_doc_names=resolved_doc_names,
    )

    top_candidate = candidates[0] if candidates else {}
    document_resolution = DocumentResolution(
        catalog_id=str(top_candidate.get("catalog_id") or ""),
        doc_name=str(resolved_doc_names[0]) if resolved_doc_names else str(top_candidate.get("doc_name") or ""),
        confidence=confidence,
        ambiguous=ambiguous,
    )

    return PreparedQueryPlan(
        original_query=original_query,
        question_type=question_type,
        answer_shape=answer_shape,
        concepts=tuple(concepts),
        significant_words=tuple(significant_words),
        document_resolution=document_resolution,
        resolved_doc_names=tuple(resolved_doc_names),
        point_number_hints=tuple(point_number_hints),
        selected_tools=selected_tools,
        tool_queries=prepared_tool_queries,
        confidence=confidence,
        ambiguous=ambiguous,
        warnings=tuple(warnings),
        trace=QueryPlannerTrace(
            mode=mode,
            resolver=resolver,
            knowledge_source=knowledge.source_path,
            catalog_source=catalog.source_path,
            candidates_total=len(candidates),
        ),
        candidates=tuple(candidates),
    )


def apply_prepared_plan(
    query: str,
    filters: dict[str, Any] | None,
    plan: PreparedQueryPlan,
) -> tuple[str, dict[str, Any]]:
    merged_filters = dict(filters or {})
    if plan.resolved_doc_names and not plan.ambiguous and len(plan.resolved_doc_names) == 1:
        merged_filters["doc_name"] = plan.resolved_doc_names[0]
    if plan.point_number_hints and "point_number" not in merged_filters:
        merged_filters["point_number"] = plan.point_number_hints[0]
    effective_query = plan.original_query or query
    return effective_query, merged_filters


def prepared_plan_to_understanding(plan: PreparedQueryPlan) -> QueryUnderstandingResult:
    search_query = plan.original_query
    if plan.tool_queries:
        for entry in plan.tool_queries:
            if entry.tool_name == "vector" and entry.queries:
                search_query = entry.queries[0]
                break
            if entry.tool_name == "payload" and entry.queries:
                search_query = entry.queries[0]
                break

    return QueryUnderstandingResult(
        original_query=plan.original_query,
        search_query=search_query,
        document_hints=list(plan.significant_words),
        resolved_doc_names=list(plan.resolved_doc_names),
        point_number_hints=list(plan.point_number_hints),
        tool_hints=list(plan.selected_tools),
        confidence=plan.confidence,
        ambiguous=plan.ambiguous,
        warnings=list(plan.warnings),
        trace=QueryUnderstandingTrace(
            mode=plan.trace.mode,
            resolver=plan.trace.resolver,
            catalog_source=plan.trace.catalog_source,
            candidates_total=plan.trace.candidates_total,
        ),
        candidates=list(plan.candidates),
    )


def load_default_knowledge() -> DocumentKnowledgeIndex:
    return load_document_knowledge()


def plan_query(
    query: str,
    *,
    catalog: DocumentCatalog | None = None,
    knowledge: DocumentKnowledgeIndex | None = None,
    filters: dict[str, Any] | None = None,
    mode: str = "auto",
    llm_provider: str = "none",
    keys_path: Path | None = None,
    project_paths: ProjectPaths | None = None,
) -> PreparedQueryPlan:
    paths = project_paths or ProjectPaths.from_root(None)
    return prepare_query(
        query,
        catalog=catalog or load_default_document_catalog(paths),
        knowledge=knowledge or load_default_knowledge(),
        filters=filters,
        mode=mode,
        llm_provider=llm_provider,
        keys_path=keys_path,
    )
