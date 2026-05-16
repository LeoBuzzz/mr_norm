from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mr_norm.retrieval.document_catalog import load_catalog_snapshot
from mr_norm.retrieval.document_knowledge import load_document_knowledge
from mr_norm.runtime.query_planner import (
    apply_prepared_plan,
    plan_query,
    prepare_query,
)


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def test_prepare_query_preserves_original_query_for_induced_voltage() -> None:
    plan = prepare_query(
        "Какое наведенное напряжение безопасно?",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )

    assert plan.original_query == "Какое наведенное напряжение безопасно?"
    assert "наведенное напряжение" in plan.original_query
    assert not plan.resolved_doc_names or plan.confidence < 0.55
    vector_queries = next(
        (entry.queries for entry in plan.tool_queries if entry.tool_name == "vector"),
        (),
    )
    assert any("наведенное" in query for query in vector_queries)


def test_prepare_query_point_lookup_prefers_likvidation_rules_not_investigation_form() -> None:
    plan = prepare_query(
        "Покажи пункт 9 инструкции по ликвидации аварий",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )

    assert list(plan.point_number_hints) == ["9"]
    assert plan.resolved_doc_names
    assert "ликвидации нарушений" in plan.resolved_doc_names[0].lower()
    assert "расследования" not in plan.resolved_doc_names[0].lower()
    assert "point" in plan.selected_tools


def test_prepare_query_resolves_federal_energy_law_from_intent() -> None:
    plan = prepare_query(
        "Какой документ устанавливает общие правила работы электроэнергетической системы РФ?",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )

    assert plan.question_type == "document_lookup"
    assert plan.resolved_doc_names == ("Об электроэнергетике",)
    assert not plan.document_resolution.ambiguous


def test_apply_prepared_plan_keeps_original_query() -> None:
    plan = prepare_query(
        "Какое наведенное напряжение безопасно?",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )
    query, filters = apply_prepared_plan("Какое наведенное напряжение безопасно?", {}, plan)
    assert query == "Какое наведенное напряжение безопасно?"
    assert "doc_name" not in filters or not plan.resolved_doc_names


def test_plan_query_llm_mode_uses_provider(monkeypatch) -> None:
    def fake_llm_plan(query, candidates, matched_terms, *, llm_provider, keys_path=None):
        return {
            "question_type": "factual",
            "answer_shape": "narrow",
            "concepts": ["заземление"],
            "significant_words": ["пуэ"],
            "resolved_doc_names": ["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
            "point_number_hints": [],
            "confidence": 0.91,
            "tool_queries": {
                "payload": ["заземление"],
                "vector": ["заземление пуэ"],
                "point": [],
            },
        }, []

    monkeypatch.setattr("mr_norm.runtime.query_planner._llm_plan", fake_llm_plan)
    plan = plan_query(
        "расскажи про ПУЭ по заземлению",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="llm",
        llm_provider="ollama",
    )

    assert plan.resolved_doc_names == ("ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК",)
    assert plan.trace.resolver == "llm"
