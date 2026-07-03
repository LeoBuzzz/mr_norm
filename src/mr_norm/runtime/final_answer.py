from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.citations import validate_citations
from mr_norm.runtime.contracts import Citation, FinalAnswerResult, RuntimeRequest
from mr_norm.runtime.prompts import load_prompt_pack_by_role

REFUSAL_MARKERS = (
    "отсутствует",
    "отсутствуют",
    "нет информации",
    "не содержат",
    "не содержит",
    "не найден",
)


def _looks_like_refusal_answer(answer: str) -> bool:
    text = (answer or "").strip().lower()
    if not text:
        return False
    return "фрагмент" in text and any(marker in text for marker in REFUSAL_MARKERS)


def _anti_refusal_answer(
    request: RuntimeRequest,
    answer: str,
    evidence: Sequence[RetrievedItem],
    citations: list[Citation],
) -> tuple[str, list[Citation], list[str]]:
    if not _looks_like_refusal_answer(answer):
        return answer, citations, []
    if not evidence:
        return answer, citations, ["anti_refusal_guard:refusal_not_repaired_without_evidence"]

    cited_ids = {citation.chunk_id for citation in citations if citation.chunk_id}
    if cited_ids:
        repair_evidence = [item for item in evidence if item.chunk_id in cited_ids]
    else:
        repair_evidence = list(evidence[:3])

    fallback = EvidenceOnlyFinalAnswer().answer(
        request,
        repair_evidence,
        limit=min(3, len(repair_evidence)),
    )
    if fallback.citations:
        return (
            fallback.answer,
            fallback.citations,
            ["anti_refusal_guard:refusal_repaired_with_citation", *fallback.warnings],
        )
    return answer, citations, ["anti_refusal_guard:refusal_not_repaired_without_citation"]

FinalAnswerProvider = Callable[[RuntimeRequest, Sequence[RetrievedItem], dict[str, Any]], Mapping[str, Any]]


class FinalAnswer(Protocol):
    backend_name: str

    def answer(
        self,
        request: RuntimeRequest,
        evidence: Sequence[RetrievedItem],
        *,
        limit: int | None = None,
    ) -> FinalAnswerResult: ...


def _format_evidence_summary(evidence: Sequence[RetrievedItem], *, limit: int) -> str:
    lines: list[str] = []
    for index, item in enumerate(evidence[:limit], start=1):
        header = f"{index}. {item.doc_name} {item.point_number}".strip()
        text = (item.text or "").strip()
        if text:
            lines.append(f"{header}\n{text}")
        else:
            lines.append(header)
    return "\n\n".join(lines)


class EvidenceOnlyFinalAnswer:
    backend_name = "evidence"

    def answer(
        self,
        request: RuntimeRequest,
        evidence: Sequence[RetrievedItem],
        *,
        limit: int | None = None,
    ) -> FinalAnswerResult:
        effective_limit = limit if limit is not None else request.limit
        if not evidence:
            return FinalAnswerResult(
                answer="No evidence found for the request.",
                citations=[],
                warnings=["no evidence items available for final answer"],
            )

        top_items = list(evidence[:effective_limit])
        raw_citations = [
            {
                "chunk_id": item.chunk_id,
                "doc_name": item.doc_name,
                "point_number": item.point_number,
            }
            for item in top_items
            if item.chunk_id
        ]
        citations, warnings = validate_citations(top_items, raw_citations)
        query = request.query.strip() or "request"
        summary = _format_evidence_summary(top_items, limit=effective_limit)
        answer = f"Evidence summary for query: {query}\n\n{summary}".strip()
        return FinalAnswerResult(answer=answer, citations=citations, warnings=list(warnings))


def _parse_final_answer_payload(
    payload: Mapping[str, Any],
    evidence: Sequence[RetrievedItem],
) -> tuple[str, list[Citation], list[str]]:
    from mr_norm.runtime.llm_payloads import normalize_final_answer_payload

    normalized, normalize_warnings = normalize_final_answer_payload(payload)
    warnings = list(normalize_warnings)
    citations, citation_warnings = validate_citations(evidence, normalized["citations"])
    warnings.extend(citation_warnings)
    answer = normalized["answer"]
    if not citations:
        warnings = list(warnings) + ["final answer returned no valid citations"]
    return answer, citations, warnings


class PromptPackFinalAnswer:
    backend_name = "prompt"

    def __init__(self, *, provider: FinalAnswerProvider | None = None) -> None:
        self._pack = load_prompt_pack_by_role("final_answer")
        self._provider = provider

    def answer(
        self,
        request: RuntimeRequest,
        evidence: Sequence[RetrievedItem],
        *,
        limit: int | None = None,
    ) -> FinalAnswerResult:
        effective_limit = limit if limit is not None else request.limit
        if self._provider is None:
            fallback = EvidenceOnlyFinalAnswer().answer(request, evidence, limit=effective_limit)
            return FinalAnswerResult(
                answer=fallback.answer,
                citations=fallback.citations,
                warnings=fallback.warnings
                + ["prompt final answer provider not configured; used evidence-only answer"],
            )

        try:
            payload = self._provider(request, evidence[:effective_limit], self._pack)
            answer, citations, warnings = _parse_final_answer_payload(payload, evidence)
            answer, citations, repair_warnings = _anti_refusal_answer(
                request,
                answer,
                evidence[:effective_limit],
                citations,
            )
            warnings = list(warnings) + repair_warnings
        except Exception as exc:
            fallback = EvidenceOnlyFinalAnswer().answer(request, evidence, limit=effective_limit)
            return FinalAnswerResult(
                answer=fallback.answer,
                citations=fallback.citations,
                warnings=fallback.warnings + [f"prompt final answer failed: {type(exc).__name__}: {exc}"],
            )

        return FinalAnswerResult(answer=answer, citations=citations, warnings=warnings)


def build_final_answer(backend: str, *, provider: FinalAnswerProvider | None = None) -> FinalAnswer:
    if backend == "evidence":
        return EvidenceOnlyFinalAnswer()
    if backend == "prompt":
        return PromptPackFinalAnswer(provider=provider)
    raise ValueError(f"unsupported final answer backend: {backend}")
