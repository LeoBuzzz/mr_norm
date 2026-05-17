from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.document_knowledge import load_document_knowledge, match_query_terms
from mr_norm.retrieval.text_normalize import morphology_phrase_matches_query


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def test_morphology_phrase_matches_inflected_form() -> None:
    assert morphology_phrase_matches_query(
        "наведенное напряжение",
        "Какое значение наведенным напряжением считается безопасным?",
    )


def test_morphology_does_not_match_unrelated_phrase() -> None:
    assert not morphology_phrase_matches_query(
        "наведенное напряжение",
        "Какое безопасное напряжение допустимо?",
    )


def test_match_query_terms_uses_morphology_bucket() -> None:
    knowledge = load_sample_knowledge()
    matches = match_query_terms(
        "Какое значение наведенным напряжением считается безопасным?",
        knowledge,
    )
    assert "наведенное напряжение" in matches.morphology_terms
    assert "наведенное напряжение" not in matches.exact_phrase_terms


def test_abbreviation_token_stays_exact() -> None:
    knowledge = load_sample_knowledge()
    matches = match_query_terms("требования пуэ к заземлению", knowledge, enable_pue_aliases=True)
    assert "пуэ" in matches.document_hints
    matches_typo = match_query_terms("требования пу к заземлению", knowledge, enable_pue_aliases=True)
    assert "пуэ" not in matches_typo.document_hints
