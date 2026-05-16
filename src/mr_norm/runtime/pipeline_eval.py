from __future__ import annotations

from typing import Any

FALLBACK_WARNING_MARKERS = (
    "used deterministic routing",
    "used passthrough ordering",
    "used evidence-only answer",
    "prompt planner failed",
    "prompt reranker failed",
    "prompt final answer failed",
    "provider not configured",
    "returned no valid tools",
    "returned no valid items",
    "returned no valid citations",
)


def is_fallback_warning(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in FALLBACK_WARNING_MARKERS)


def evaluate_pipeline_result(result: dict[str, Any]) -> dict[str, Any]:
    runtime = result.get("runtime") or {}
    final_answer = result.get("final_answer") or {}
    rerank = result.get("rerank") or {}
    warnings = list(result.get("warnings") or [])
    citations = list(final_answer.get("citations") or [])
    answer_text = str(final_answer.get("answer") or "").strip()

    citation_warnings = sum(1 for warning in warnings if warning.startswith("citation["))
    valid_citations = len(citations)

    return {
        "items_returned": int((runtime.get("metrics") or {}).get("items_returned", len(runtime.get("items") or []))),
        "reranked_items": len(rerank.get("items") or []),
        "citations_count": len(citations),
        "valid_citations": valid_citations,
        "citation_warnings": citation_warnings,
        "warnings_count": len(warnings),
        "fallback_count": sum(1 for warning in warnings if is_fallback_warning(warning)),
        "empty_answer": not answer_text,
        "backend_trace": dict(result.get("trace") or {}),
    }


def summarize_pipeline_batch(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {
            "questions_total": 0,
            "items_returned_total": 0,
            "citations_total": 0,
            "citation_warnings_total": 0,
            "warnings_total": 0,
            "fallback_total": 0,
            "empty_answer_count": 0,
            "empty_answer_rate": 0.0,
        }

    evals = [entry["evaluation"] for entry in entries]
    questions_total = len(evals)
    empty_answer_count = sum(1 for item in evals if item.get("empty_answer"))
    warnings_total = sum(int(item.get("warnings_count", 0)) for item in evals)
    fallback_total = sum(int(item.get("fallback_count", 0)) for item in evals)

    return {
        "questions_total": questions_total,
        "items_returned_total": sum(int(item.get("items_returned", 0)) for item in evals),
        "citations_total": sum(int(item.get("citations_count", 0)) for item in evals),
        "citation_warnings_total": sum(int(item.get("citation_warnings", 0)) for item in evals),
        "warnings_total": warnings_total,
        "fallback_total": fallback_total,
        "empty_answer_count": empty_answer_count,
        "empty_answer_rate": round(empty_answer_count / questions_total, 4),
        "avg_warnings_per_question": round(warnings_total / questions_total, 4),
        "avg_fallbacks_per_question": round(fallback_total / questions_total, 4),
    }
