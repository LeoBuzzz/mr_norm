from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.document_knowledge import (
    load_document_knowledge,
    match_query_terms,
    phrase_matches_query,
    phrase_required_tokens,
)
from mr_norm.retrieval.filters import build_payload_filter_spec
from mr_norm.runtime.query_planner import prepare_query
from mr_norm.retrieval.document_catalog import load_catalog_snapshot


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_phrase_matches_query_requires_composite_term() -> None:
    assert phrase_matches_query("наведенное напряжение", "Какое наведенное напряжение безопасно?")
    assert not phrase_matches_query("наведенное напряжение", "Какое безопасное напряжение допустимо?")


def test_match_query_terms_splits_exact_and_loose() -> None:
    knowledge = load_sample_knowledge()
    matches = match_query_terms("Какое наведенное напряжение безопасно?", knowledge)

    assert "наведенное напряжение" in matches.exact_phrase_terms
    assert "пуэ" not in matches.document_hints
    assert all("напряжение" != term for term in matches.exact_phrase_terms)


def test_prepare_query_payload_starts_with_full_question_and_required_tokens() -> None:
    plan = prepare_query(
        "Какое наведенное напряжение безопасно?",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )
    payload_entry = next(entry for entry in plan.tool_queries if entry.tool_name == "payload")
    assert payload_entry.queries[0] == "Какое наведенное напряжение безопасно?"
    assert "наведенное" in payload_entry.required_tokens
    assert "напряжение" in payload_entry.required_tokens


def test_build_payload_filter_spec_adds_required_tokens_to_must() -> None:
    spec = build_payload_filter_spec(
        "наведенное напряжение",
        {},
        required_tokens=phrase_required_tokens("наведенное напряжение"),
    )
    must_values = [item["value"] for item in spec.get("must") or []]
    assert "наведенное" in must_values
    assert "напряжение" in must_values


def test_llm_sanitizer_strips_pue_without_explicit_mention(monkeypatch) -> None:
    def fake_llm_plan(query, candidates, matched_terms, *, llm_provider, keys_path=None):
        return {
            "question_type": "factual",
            "answer_shape": "narrow",
            "concepts": ["заземление"],
            "significant_words": ["пуэ", "напряжение"],
            "resolved_doc_names": ["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
            "point_number_hints": [],
            "confidence": 0.95,
            "tool_queries": {
                "payload": ["безопасное напряжение", "пуэ"],
                "vector": ["напряжение"],
                "point": [],
            },
        }, []

    monkeypatch.setattr("mr_norm.runtime.query_planner._llm_plan", fake_llm_plan)
    plan = prepare_query(
        "Какое наведенное напряжение безопасно?",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="llm",
        llm_provider="ollama",
        enable_pue_aliases=False,
    )

    assert "пуэ" not in plan.significant_words
    assert not plan.resolved_doc_names
    payload_queries = next(entry.queries for entry in plan.tool_queries if entry.tool_name == "payload")
    assert payload_queries[0] == "Какое наведенное напряжение безопасно?"
    assert "наведенное напряжение" in payload_queries
