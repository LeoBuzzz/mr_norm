from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.final_answer import EvidenceOnlyFinalAnswer, PromptPackFinalAnswer, build_final_answer


def make_evidence() -> list[RetrievedItem]:
    return [
        RetrievedItem(
            chunk_id="chunk_1",
            doc_name="ПУЭ",
            point_number="1.7.1",
            text="Требования к заземлению.",
            source_tool="payload",
        )
    ]


def test_evidence_only_final_answer_builds_summary_and_citations() -> None:
    request = RuntimeRequest(query="заземление", limit=1)
    result = EvidenceOnlyFinalAnswer().answer(request, make_evidence())

    assert "заземление" in result.answer
    assert result.citations[0].chunk_id == "chunk_1"
    assert result.citations[0].doc_name == "ПУЭ"


def test_evidence_only_final_answer_handles_empty_evidence() -> None:
    request = RuntimeRequest(query="заземление")
    result = EvidenceOnlyFinalAnswer().answer(request, [])

    assert "No evidence found" in result.answer
    assert result.citations == []
    assert result.warnings


def test_prompt_pack_final_answer_uses_provider_payload() -> None:
    request = RuntimeRequest(query="заземление", limit=1)
    evidence = make_evidence()

    def provider(_request, _evidence, _pack):
        return {
            "answer": "Ответ по норме.",
            "citations": [{"chunk_id": "chunk_1", "doc_name": "ПУЭ", "point_number": "1.7.1"}],
        }

    result = PromptPackFinalAnswer(provider=provider).answer(request, evidence)

    assert result.answer == "Ответ по норме."
    assert result.citations[0].chunk_id == "chunk_1"


def test_prompt_pack_final_answer_rejects_invalid_citations() -> None:
    request = RuntimeRequest(query="заземление", limit=1)
    evidence = make_evidence()

    def provider(_request, _evidence, _pack):
        return {
            "answer": "Ответ.",
            "citations": [{"chunk_id": "missing", "doc_name": "ПУЭ", "point_number": "1.7.1"}],
        }

    result = PromptPackFinalAnswer(provider=provider).answer(request, evidence)

    assert result.citations == []
    assert any("no valid citations" in warning for warning in result.warnings)


def test_build_final_answer_rejects_unknown_backend() -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported final answer backend"):
        build_final_answer("unknown")
