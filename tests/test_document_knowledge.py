from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.document_knowledge import find_knowledge_candidates, load_document_knowledge


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def test_find_knowledge_candidates_for_induced_voltage() -> None:
    knowledge = load_sample_knowledge()
    candidates = find_knowledge_candidates("Какое наведенное напряжение безопасно?", knowledge, limit=5)

    assert candidates
    doc_names = " ".join(candidate.doc_name.lower() for candidate in candidates)
    assert "электроустанов" in doc_names or "переключ" in doc_names


def test_find_knowledge_candidates_for_accident_liquidation_instruction() -> None:
    knowledge = load_sample_knowledge()
    candidates = find_knowledge_candidates(
        "Покажи пункт 9 инструкции по ликвидации аварий",
        knowledge,
        limit=5,
    )

    assert candidates
    top = candidates[0].doc_name.lower()
    assert "ликвидации нарушений" in top
    assert "расследования" not in top
