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
    answer: str,
    evidence: Sequence[RetrievedItem],
    citations: list[Citation],
) -> tuple[str, bool]:
    if not _looks_like_refusal_answer(answer):
        return answer, False
    by_id = {item.chunk_id: item for item in evidence if item.chunk_id}
    if citations:
        parts: list[str] = []
        for citation in citations[:2]:
            item = by_id.get(citation.chunk_id)
            if item and item.text:
                header = f"{item.doc_name} {item.point_number}".strip()
                excerpt = item.text.strip()
                parts.append(f"{header}: {excerpt}" if header else excerpt)
        if parts:
            return " ".join(parts)[:1200], True
    for item in evidence[:3]:
        if item.text and len(item.text.strip()) >= 80:
            header = f"{item.doc_name} {item.point_number}".strip()
            excerpt = item.text.strip()[:600]
            return (f"{header}: {excerpt}" if header else excerpt), True
    return answer, False

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
            answer, replaced = _anti_refusal_answer(answer, evidence, citations)
            if replaced:
                warnings = list(warnings) + ["anti_refusal_guard:replaced_refusal_with_evidence_excerpt"]
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
