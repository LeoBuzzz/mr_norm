from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.compare import reciprocal_rank_fusion
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult, ToolTrace, ToolMetrics
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.runtime.citations import validate_citations
from mr_norm.runtime.contracts import Citation, RuntimeRequest
from mr_norm.runtime.deep_research_memory import PendingDeepResearchSession
from mr_norm.runtime.llm_profiles import resolve_role_models, resolve_role_profile
from mr_norm.runtime.llm_providers import chat_json_with_model_fallback
from mr_norm.runtime.prompts import load_prompt_pack_by_role
from mr_norm.runtime.tool_runner import default_runtime_tool_runners

MAX_CANDIDATE_DOCS = 8
MAX_SEARCH_QUERIES = 6
DEFAULT_BROAD_LIMIT = 40
DEFAULT_DEEP_DIVE_LIMIT_PER_DOC = 15
DEFAULT_MEMO_EVIDENCE_LIMIT = 60
DEFAULT_EVIDENCE_PER_DOC = 20

RuntimeRunner = Callable[[RuntimeRequest, IndexingConfig], Any]


@dataclass(frozen=True)
class ResearchAnalysis:
    normalized_question: str
    aspects: tuple[str, ...]
    terms: tuple[str, ...]
    search_queries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_question": self.normalized_question,
            "aspects": list(self.aspects),
            "terms": list(self.terms),
            "search_queries": list(self.search_queries),
        }


@dataclass(frozen=True)
class ResearchDocumentCandidate:
    index: int
    doc_id: str
    doc_name: str
    point_numbers: tuple[str, ...]
    fragment_count: int
    reason: str
    max_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "point_numbers": list(self.point_numbers),
            "fragment_count": self.fragment_count,
            "reason": self.reason,
            "max_score": self.max_score,
        }


@dataclass(frozen=True)
class DeepResearchRequest:
    query: str
    llm_provider: str = "polza"
    profile: str = "deep"
    broad_limit: int = DEFAULT_BROAD_LIMIT
    deep_dive_limit_per_doc: int = DEFAULT_DEEP_DIVE_LIMIT_PER_DOC
    memo_evidence_limit: int = DEFAULT_MEMO_EVIDENCE_LIMIT


@dataclass(frozen=True)
class DeepResearchResult:
    stage: str
    question: str
    answer: str = ""
    analysis: ResearchAnalysis | None = None
    candidates: tuple[ResearchDocumentCandidate, ...] = ()
    citations: tuple[Citation, ...] = ()
    evidence: tuple[RetrievedItem, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "question": self.question,
            "answer": self.answer,
            "analysis": self.analysis.to_dict() if self.analysis else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "citations": [
                {
                    "chunk_id": citation.chunk_id,
                    "doc_name": citation.doc_name,
                    "point_number": citation.point_number,
                }
                for citation in self.citations
            ],
            "evidence_count": len(self.evidence),
            "warnings": list(self.warnings),
            "diagnostics": dict(self.diagnostics),
        }


def _coerce_string_list(value: Any, *, field_name: str, max_items: int | None = None) -> tuple[tuple[str, ...], list[str]]:
    warnings: list[str] = []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    elif isinstance(value, list):
        parts = [str(entry).strip() for entry in value if str(entry).strip()]
    else:
        raise ValueError(f"{field_name} must be a list or string")

    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(part)
    if max_items is not None and len(unique) > max_items:
        warnings.append(f"{field_name}: truncated to {max_items} items")
        unique = unique[:max_items]
    if not unique:
        raise ValueError(f"{field_name} must be non-empty")
    return tuple(unique), warnings


def normalize_research_analysis_payload(payload: dict[str, Any]) -> tuple[ResearchAnalysis, list[str]]:
    warnings: list[str] = []
    normalized_question = str(payload.get("normalized_question") or payload.get("question") or "").strip()
    if not normalized_question:
        raise ValueError("normalized_question must be non-empty")

    aspects, aspect_warnings = _coerce_string_list(payload.get("aspects", []), field_name="aspects")
    warnings.extend(aspect_warnings)
    terms, term_warnings = _coerce_string_list(payload.get("terms", []), field_name="terms")
    warnings.extend(term_warnings)
    search_queries, query_warnings = _coerce_string_list(
        payload.get("search_queries", []),
        field_name="search_queries",
        max_items=MAX_SEARCH_QUERIES,
    )
    warnings.extend(query_warnings)

    return ResearchAnalysis(
        normalized_question=normalized_question,
        aspects=aspects,
        terms=terms,
        search_queries=search_queries,
    ), warnings


def analyze_research_question(
    query: str,
    *,
    llm_provider: str,
    keys_path: Path | None = None,
) -> tuple[ResearchAnalysis, list[str]]:
    pack = load_prompt_pack_by_role("deep_research_analysis")
    profile = resolve_role_profile(llm_provider, "deep_research_analysis")
    payload = chat_json_with_model_fallback(
        llm_provider,
        resolve_role_models(llm_provider, "deep_research_analysis"),
        keys_path=keys_path,
        system_prompt=str(pack.get("prompt") or ""),
        user_payload={
            "query": query.strip(),
            "output_contract": pack.get("output_contract"),
        },
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
    )
    return normalize_research_analysis_payload(payload)


def _dedupe_items(items: list[RetrievedItem]) -> list[RetrievedItem]:
    seen: set[str] = set()
    unique: list[RetrievedItem] = []
    for item in items:
        key = item.chunk_id or f"{item.doc_id}:{item.point_number}:{item.text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _merge_tool_results(results: list[ToolResult], *, tool_name: str, trace_id: str) -> ToolResult:
    if not results:
        return ToolResult(
            items=[],
            trace=ToolTrace(tool_name=tool_name, trace_id=trace_id),
            metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=0, qdrant_calls=0),
            warnings=[],
        )
    if len(results) == 1:
        return results[0]

    seen: set[str] = set()
    merged: list[RetrievedItem] = []
    warnings: list[str] = []
    qdrant_calls = 0
    elapsed = 0.0
    for result in results:
        qdrant_calls += result.metrics.qdrant_calls
        elapsed += result.metrics.elapsed_sec
        warnings.extend(result.warnings)
        for item in result.items:
            key = item.chunk_id or f"{item.doc_id}:{item.point_number}:{item.text[:80]}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    base = results[0]
    return ToolResult(
        items=merged,
        trace=base.trace,
        metrics=ToolMetrics(
            elapsed_sec=round(elapsed, 6),
            candidates_returned=len(merged),
            qdrant_calls=qdrant_calls,
        ),
        warnings=list(dict.fromkeys(warnings)),
    )


def _run_hybrid_query(
    query: str,
    *,
    config: IndexingConfig,
    profile: str,
    limit: int,
    filters: dict[str, Any],
    trace_id: str,
    tool_runners: dict[str, Any] | None = None,
) -> tuple[list[RetrievedItem], list[str]]:
    runners = tool_runners or default_runtime_tool_runners()
    per_tool: dict[str, ToolResult] = {}
    warnings: list[str] = []

    for tool_name in ("payload", "vector"):
        runner = runners.get(tool_name)
        if runner is None:
            warnings.append(f"deep_research: missing tool runner {tool_name}")
            continue
        try:
            result = runner(
                ToolRequest(
                    query=query,
                    filters=dict(filters),
                    limit=limit,
                    profile=profile,
                    trace_id=trace_id,
                ),
                config,
            )
            per_tool[tool_name] = result
            warnings.extend(result.warnings)
        except Exception as exc:
            warnings.append(f"{tool_name} failed: {type(exc).__name__}: {exc}")

    if len(per_tool) > 1:
        fused = reciprocal_rank_fusion(per_tool, limit=limit)
        return fused, warnings
    if per_tool:
        first = next(iter(per_tool.values()))
        return first.items[:limit], warnings
    return [], warnings


def run_broad_research_search(
    analysis: ResearchAnalysis,
    *,
    config: IndexingConfig,
    profile: str = "deep",
    broad_limit: int = DEFAULT_BROAD_LIMIT,
    runtime_runner: RuntimeRunner | None = None,
    tool_runners: dict[str, Any] | None = None,
) -> tuple[list[RetrievedItem], list[str], dict[str, Any]]:
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {"search_queries": list(analysis.search_queries), "query_results": []}
    merged_items: list[RetrievedItem] = []

    queries = list(analysis.search_queries)
    if analysis.normalized_question and analysis.normalized_question not in queries:
        queries.insert(0, analysis.normalized_question)
    queries = queries[:MAX_SEARCH_QUERIES]

    if runtime_runner is not None:
        for index, search_query in enumerate(queries, start=1):
            runtime = runtime_runner(
                RuntimeRequest(
                    query=search_query,
                    filters={},
                    limit=broad_limit,
                    retrieval_limit=broad_limit,
                    profile=profile,
                    trace_id=f"deep_research_broad_{index}",
                ),
                config,
            )
            items = list(runtime.items)
            warnings.extend(runtime.warnings)
            diagnostics["query_results"].append(
                {"query": search_query, "items": len(items), "fusion": runtime.trace.fusion}
            )
            merged_items.extend(items)
    else:
        for index, search_query in enumerate(queries, start=1):
            items, query_warnings = _run_hybrid_query(
                search_query,
                config=config,
                profile=profile,
                limit=broad_limit,
                filters={},
                trace_id=f"deep_research_broad_{index}",
                tool_runners=tool_runners,
            )
            warnings.extend(query_warnings)
            diagnostics["query_results"].append({"query": search_query, "items": len(items), "fusion": "hybrid_rrf"})
            merged_items.extend(items)

    deduped = _dedupe_items(merged_items)
    return deduped, warnings, diagnostics


def group_evidence_by_doc_id(
    items: list[RetrievedItem],
) -> tuple[dict[str, list[RetrievedItem]], int]:
    groups: dict[str, list[RetrievedItem]] = defaultdict(list)
    rejected_without_doc_id = 0
    for item in items:
        doc_id = (item.doc_id or "").strip()
        if not doc_id:
            rejected_without_doc_id += 1
            continue
        groups[doc_id].append(item)
    return dict(groups), rejected_without_doc_id


def _aspect_coverage(aspects: tuple[str, ...], items: list[RetrievedItem]) -> int:
    if not aspects:
        return 0
    corpus = " ".join((item.text or "").casefold() for item in items)
    covered = 0
    for aspect in aspects:
        tokens = [token for token in re.findall(r"\w+", aspect, flags=re.UNICODE) if len(token) >= 4]
        if not tokens:
            continue
        if any(token.casefold() in corpus for token in tokens):
            covered += 1
    return covered


def _sorted_point_numbers(items: list[RetrievedItem]) -> tuple[str, ...]:
    points: list[str] = []
    seen: set[str] = set()
    for item in items:
        point = (item.point_number or "").strip()
        if not point or point in seen:
            continue
        seen.add(point)
        points.append(point)

    def sort_key(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for part in re.split(r"[._]", value):
            if part.isdigit():
                parts.append(int(part))
            else:
                parts.append(10_000)
        return tuple(parts) if parts else (10_000,)

    return tuple(sorted(points, key=sort_key))


def rank_document_groups(
    groups: dict[str, list[RetrievedItem]],
    *,
    aspects: tuple[str, ...],
) -> list[tuple[str, list[RetrievedItem], float, str]]:
    ranked: list[tuple[str, list[RetrievedItem], float, str]] = []
    for doc_id, items in groups.items():
        max_score = max((item.score or 0.0) for item in items)
        coverage = _aspect_coverage(aspects, items)
        composite = max_score + coverage * 0.15 + min(len(items), 10) * 0.02
        doc_name = next((item.doc_name for item in items if item.doc_name), doc_id)
        points = _sorted_point_numbers(items)
        point_hint = f", пункты {', '.join(f'п.{point}' for point in points[:3])}" if points else ""
        reason = f"релевантность {max_score:.3f}, фрагментов {len(items)}, аспектов {coverage}{point_hint}"
        ranked.append((doc_id, items, composite, reason if doc_name else reason))
    ranked.sort(key=lambda row: (-row[2], row[0]))
    return ranked


def build_document_candidates(
    ranked_groups: list[tuple[str, list[RetrievedItem], float, str]],
    *,
    max_candidates: int = MAX_CANDIDATE_DOCS,
) -> tuple[ResearchDocumentCandidate, ...]:
    candidates: list[ResearchDocumentCandidate] = []
    for index, (doc_id, items, composite, reason) in enumerate(ranked_groups[:max_candidates], start=1):
        doc_name = next((item.doc_name for item in items if item.doc_name), doc_id)
        candidates.append(
            ResearchDocumentCandidate(
                index=index,
                doc_id=doc_id,
                doc_name=doc_name,
                point_numbers=_sorted_point_numbers(items),
                fragment_count=len(items),
                reason=reason,
                max_score=round(composite, 6),
            )
        )
    return tuple(candidates)


def run_deep_research_broad(
    request: DeepResearchRequest,
    config: IndexingConfig,
    *,
    keys_path: Path | None = None,
    runtime_runner: RuntimeRunner | None = None,
    tool_runners: dict[str, Any] | None = None,
) -> DeepResearchResult:
    query = (request.query or "").strip()
    if not query:
        return DeepResearchResult(stage="error", question="", answer="Пустой запрос.", warnings=("empty_query",))

    warnings: list[str] = []
    diagnostics: dict[str, Any] = {"llm_provider": request.llm_provider}

    try:
        analysis, analysis_warnings = analyze_research_question(
            query,
            llm_provider=request.llm_provider,
            keys_path=keys_path,
        )
    except Exception as exc:
        return DeepResearchResult(
            stage="error",
            question=query,
            answer=f"Не удалось проанализировать вопрос: {type(exc).__name__}: {exc}",
            warnings=(f"analysis_failed:{exc}",),
            diagnostics=diagnostics,
        )
    warnings.extend(analysis_warnings)
    diagnostics["analysis"] = analysis.to_dict()

    evidence, search_warnings, search_diagnostics = run_broad_research_search(
        analysis,
        config=config,
        profile=request.profile,
        broad_limit=request.broad_limit,
        runtime_runner=runtime_runner,
        tool_runners=tool_runners,
    )
    warnings.extend(search_warnings)
    diagnostics.update(search_diagnostics)

    groups, rejected = group_evidence_by_doc_id(evidence)
    diagnostics["rejected_without_doc_id"] = rejected
    diagnostics["documents_found"] = len(groups)

    if not groups:
        return DeepResearchResult(
            stage="no_evidence",
            question=query,
            answer="По теме не найдено релевантных фрагментов в базе знаний. Попробуйте переформулировать вопрос.",
            analysis=analysis,
            evidence=tuple(evidence),
            warnings=tuple(dict.fromkeys(warnings)),
            diagnostics=diagnostics,
        )

    ranked = rank_document_groups(groups, aspects=analysis.aspects)
    candidates = build_document_candidates(ranked)
    diagnostics["candidates"] = [candidate.to_dict() for candidate in candidates]

    return DeepResearchResult(
        stage="awaiting_selection",
        question=query,
        analysis=analysis,
        candidates=candidates,
        evidence=tuple(evidence),
        warnings=tuple(dict.fromkeys(warnings)),
        diagnostics=diagnostics,
    )


def _run_doc_scoped_search(
    query: str,
    *,
    doc_id: str,
    config: IndexingConfig,
    profile: str,
    limit: int,
    trace_id: str,
    tool_runners: dict[str, Any] | None = None,
) -> tuple[list[RetrievedItem], list[str]]:
    return _run_hybrid_query(
        query,
        config=config,
        profile=profile,
        limit=limit,
        filters={"doc_id": doc_id},
        trace_id=trace_id,
        tool_runners=tool_runners,
    )


def _select_memo_evidence(
    items: list[RetrievedItem],
    *,
    selected_doc_ids: tuple[str, ...],
    per_doc_limit: int,
    total_limit: int,
) -> list[RetrievedItem]:
    by_doc: dict[str, list[RetrievedItem]] = defaultdict(list)
    for item in items:
        doc_id = (item.doc_id or "").strip()
        if doc_id in selected_doc_ids:
            by_doc[doc_id].append(item)

    selected: list[RetrievedItem] = []
    seen: set[str] = set()
    for doc_id in selected_doc_ids:
        doc_items = sorted(by_doc.get(doc_id, []), key=lambda row: (-(row.score or 0.0), row.chunk_id))
        for item in doc_items[:per_doc_limit]:
            if item.chunk_id and item.chunk_id in seen:
                continue
            if item.chunk_id:
                seen.add(item.chunk_id)
            selected.append(item)
            if len(selected) >= total_limit:
                return selected
    return selected


def _serialize_evidence(items: list[RetrievedItem]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items:
        payload.append(
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "doc_name": item.doc_name,
                "point_number": item.point_number,
                "heading_path_text": item.heading_path_text,
                "text": item.text,
                "score": item.score,
                "source_tool": item.source_tool,
            }
        )
    return payload


def generate_research_memo(
    *,
    query: str,
    analysis: ResearchAnalysis,
    selected_candidates: tuple[ResearchDocumentCandidate, ...],
    evidence: list[RetrievedItem],
    llm_provider: str,
    keys_path: Path | None = None,
) -> tuple[str, list[Citation], list[str]]:
    if not evidence:
        return "", [], ["deep_research:no_evidence_for_memo"]

    pack = load_prompt_pack_by_role("deep_research_memo")
    profile = resolve_role_profile(llm_provider, "deep_research_memo")
    payload = chat_json_with_model_fallback(
        llm_provider,
        resolve_role_models(llm_provider, "deep_research_memo"),
        keys_path=keys_path,
        system_prompt=str(pack.get("prompt") or ""),
        user_payload={
            "query": query,
            "analysis": analysis.to_dict(),
            "selected_documents": [candidate.to_dict() for candidate in selected_candidates],
            "evidence": _serialize_evidence(evidence),
            "output_contract": pack.get("output_contract"),
        },
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
    )

    answer = str(payload.get("answer") or "").strip()
    if not answer:
        raise ValueError("memo answer must be non-empty")

    raw_citations = payload.get("citations", [])
    if not isinstance(raw_citations, list):
        raw_citations = []

    citations, citation_warnings = validate_citations(evidence, raw_citations)
    warnings = list(citation_warnings)
    if not citations:
        warnings.append("deep_research:no_valid_citations")
    return answer, citations, warnings


def run_deep_research_deep_dive(
    session: PendingDeepResearchSession,
    selected_indices: tuple[int, ...],
    config: IndexingConfig,
    *,
    llm_provider: str = "polza",
    profile: str = "deep",
    deep_dive_limit_per_doc: int = DEFAULT_DEEP_DIVE_LIMIT_PER_DOC,
    memo_evidence_limit: int = DEFAULT_MEMO_EVIDENCE_LIMIT,
    keys_path: Path | None = None,
    tool_runners: dict[str, Any] | None = None,
) -> DeepResearchResult:
    index_map = {candidate.index: candidate for candidate in session.candidates}
    selected_candidates = tuple(
        index_map[index] for index in selected_indices if index in index_map
    )
    if not selected_candidates:
        return DeepResearchResult(
            stage="error",
            question=session.original_query,
            answer="Не выбран ни один документ.",
            analysis=session.analysis,
            candidates=session.candidates,
            warnings=("invalid_selection",),
        )

    warnings = list(session.warnings)
    diagnostics: dict[str, Any] = {"selected_indices": list(selected_indices)}
    merged_items: list[RetrievedItem] = list(session.broad_evidence)

    search_queries = list(session.analysis.search_queries) or [session.original_query]
    aspects = list(session.analysis.aspects)

    for candidate in selected_candidates:
        queries_for_doc = [session.analysis.normalized_question, *search_queries[:3], *aspects[:2]]
        unique_queries: list[str] = []
        seen_queries: set[str] = set()
        for item in queries_for_doc:
            text = item.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            unique_queries.append(text)

        for q_index, search_query in enumerate(unique_queries, start=1):
            items, query_warnings = _run_doc_scoped_search(
                search_query,
                doc_id=candidate.doc_id,
                config=config,
                profile=profile,
                limit=deep_dive_limit_per_doc,
                trace_id=f"deep_research_dive_{candidate.index}_{q_index}",
                tool_runners=tool_runners,
            )
            warnings.extend(query_warnings)
            merged_items.extend(items)
            diagnostics.setdefault("deep_dive_queries", []).append(
                {"doc_id": candidate.doc_id, "query": search_query, "items": len(items)}
            )

    deduped = _dedupe_items(merged_items)
    selected_doc_ids = tuple(candidate.doc_id for candidate in selected_candidates)
    memo_evidence = _select_memo_evidence(
        deduped,
        selected_doc_ids=selected_doc_ids,
        per_doc_limit=DEFAULT_EVIDENCE_PER_DOC,
        total_limit=memo_evidence_limit,
    )

    if not memo_evidence:
        return DeepResearchResult(
            stage="no_evidence",
            question=session.original_query,
            answer="По выбранным документам не найдено достаточно фрагментов для аналитической записки.",
            analysis=session.analysis,
            candidates=session.candidates,
            evidence=tuple(deduped),
            warnings=tuple(dict.fromkeys(warnings)),
            diagnostics=diagnostics,
        )

    try:
        answer, citations, memo_warnings = generate_research_memo(
            query=session.original_query,
            analysis=session.analysis,
            selected_candidates=selected_candidates,
            evidence=memo_evidence,
            llm_provider=llm_provider,
            keys_path=keys_path,
        )
    except Exception as exc:
        return DeepResearchResult(
            stage="error",
            question=session.original_query,
            answer=f"Не удалось составить аналитическую записку: {type(exc).__name__}: {exc}",
            analysis=session.analysis,
            candidates=session.candidates,
            evidence=tuple(memo_evidence),
            warnings=tuple(dict.fromkeys([*warnings, f"memo_failed:{exc}"])),
            diagnostics=diagnostics,
        )

    warnings.extend(memo_warnings)
    if not citations:
        return DeepResearchResult(
            stage="error",
            question=session.original_query,
            answer=(
                "Аналитическая записка не прошла проверку ссылок на источники. "
                "Попробуйте выбрать другие документы или переформулировать вопрос."
            ),
            analysis=session.analysis,
            candidates=session.candidates,
            evidence=tuple(memo_evidence),
            warnings=tuple(dict.fromkeys(warnings)),
            diagnostics=diagnostics,
        )

    return DeepResearchResult(
        stage="completed",
        question=session.original_query,
        answer=answer,
        analysis=session.analysis,
        candidates=session.candidates,
        citations=tuple(citations),
        evidence=tuple(memo_evidence),
        warnings=tuple(dict.fromkeys(warnings)),
        diagnostics=diagnostics,
    )


def build_pending_session(
    result: DeepResearchResult,
    *,
    session_key: str,
) -> PendingDeepResearchSession | None:
    if result.stage != "awaiting_selection" or result.analysis is None:
        return None
    return PendingDeepResearchSession(
        session_key=session_key,
        original_query=result.question,
        analysis=result.analysis,
        candidates=result.candidates,
        broad_evidence=result.evidence,
        search_queries=result.analysis.search_queries,
        warnings=result.warnings,
        diagnostics=dict(result.diagnostics),
    )
