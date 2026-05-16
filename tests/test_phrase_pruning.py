from __future__ import annotations

from mr_norm.retrieval.document_knowledge import (
    match_query_terms,
    phrase_required_tokens,
    primary_exact_phrase,
    prune_exact_phrases,
    load_document_knowledge,
)
from pathlib import Path


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def test_prune_exact_phrases_prefers_longest_composite() -> None:
    pruned = prune_exact_phrases(["наведенное", "наведенное напряжение", "напряжение"])
    assert pruned[0] == "наведенное напряжение"
    assert "наведенное" not in pruned


def test_primary_exact_phrase_for_induced_voltage_query() -> None:
    knowledge = load_sample_knowledge()
    matches = match_query_terms("Какое наведенное напряжение безопасно?", knowledge)
    primary = primary_exact_phrase(matches.exact_phrase_terms)
    assert primary == "наведенное напряжение"
    assert phrase_required_tokens(primary) == ("наведенное", "напряжение")
