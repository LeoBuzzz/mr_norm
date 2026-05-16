from __future__ import annotations

from pathlib import Path
from typing import Any

from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCandidate,
    load_default_document_catalog,
)
from mr_norm.runtime.contracts import QueryUnderstandingResult
from mr_norm.runtime.query_planner import (
    _normalize_llm_payload,
    load_default_knowledge,
    plan_query,
    prepared_plan_to_understanding,
)

MIN_DOC_CONFIDENCE = 0.55


def understand_query(
    query: str,
    *,
    catalog: DocumentCatalog,
    filters: dict[str, Any] | None = None,
    mode: str = "auto",
    llm_provider: str = "none",
    keys_path: Path | None = None,
) -> QueryUnderstandingResult:
    plan = plan_query(
        query,
        catalog=catalog,
        knowledge=load_default_knowledge(),
        filters=filters,
        mode=mode,
        llm_provider=llm_provider,
        keys_path=keys_path,
    )
    return prepared_plan_to_understanding(plan)


def apply_query_understanding(
    query: str,
    filters: dict[str, Any] | None,
    understanding: QueryUnderstandingResult,
) -> tuple[str, dict[str, Any]]:
    merged_filters = dict(filters or {})
    if understanding.resolved_doc_names and not understanding.ambiguous:
        if len(understanding.resolved_doc_names) == 1:
            merged_filters["doc_name"] = understanding.resolved_doc_names[0]
    if understanding.point_number_hints and "point_number" not in merged_filters:
        merged_filters["point_number"] = understanding.point_number_hints[0]
    return understanding.original_query or query, merged_filters


def _normalize_llm_understanding_payload(
    payload: dict[str, Any],
    candidates: list[DocumentCandidate],
    catalog_by_id: dict[str, DocumentCandidate],
) -> tuple[dict[str, Any], list[str]]:
    dict_candidates = [
        {
            "catalog_id": candidate.catalog_id,
            "doc_name": candidate.doc_name,
            "score": candidate.score,
        }
        for candidate in candidates
    ]
    normalized, warnings = _normalize_llm_payload(payload, dict_candidates)
    document_hints_raw = payload.get("document_hints", [])
    if isinstance(document_hints_raw, str):
        document_hints = [part.strip() for part in document_hints_raw.split(",") if part.strip()]
    elif isinstance(document_hints_raw, list):
        document_hints = [str(item).strip() for item in document_hints_raw if str(item).strip()]
    else:
        document_hints = []
    return {
        "search_query": str(payload.get("search_query") or ""),
        "document_hints": document_hints,
        "resolved_doc_names": normalized["resolved_doc_names"],
        "point_number_hints": normalized["point_number_hints"],
        "confidence": normalized["confidence"],
    }, warnings


def _llm_resolve(
    query: str,
    candidates: list[DocumentCandidate],
    *,
    llm_provider: str,
    keys_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    dict_candidates = [
        {
            "catalog_id": candidate.catalog_id,
            "doc_name": candidate.doc_name,
            "score": candidate.score,
            "annotation": "",
        }
        for candidate in candidates
    ]
    from mr_norm.runtime.query_planner import _llm_plan

    normalized, warnings = _llm_plan(
        query,
        dict_candidates,
        [],
        llm_provider=llm_provider,
        keys_path=keys_path,
    )
    return {
        "search_query": query,
        "document_hints": normalized.get("significant_words", []),
        "resolved_doc_names": normalized.get("resolved_doc_names", []),
        "point_number_hints": normalized.get("point_number_hints", []),
        "confidence": normalized.get("confidence", 0.0),
    }, warnings


