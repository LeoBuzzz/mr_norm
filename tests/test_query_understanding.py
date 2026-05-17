from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mr_norm.runtime.query_understanding import (
    _normalize_llm_understanding_payload,
    apply_query_understanding,
    understand_query,
)
from mr_norm.retrieval.document_catalog import DocumentCandidate, load_catalog_snapshot


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_understand_query_auto_skips_pue_alias_by_default() -> None:
    result = understand_query(
        "расскажи про ПУЭ по заземлению",
        catalog=load_sample_catalog(),
        mode="auto",
    )

    assert not result.resolved_doc_names
    assert result.search_query


def test_understand_query_auto_resolves_pue_when_enabled() -> None:
    result = understand_query(
        "расскажи про ПУЭ по заземлению",
        catalog=load_sample_catalog(),
        mode="auto",
        enable_pue_aliases=True,
    )

    assert result.resolved_doc_names == ["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"]
    assert result.confidence >= 0.55
    assert result.search_query


def test_understand_query_low_confidence_skips_doc_filter() -> None:
    result = understand_query(
        "общие требования без указания документа",
        catalog=load_sample_catalog(),
        mode="auto",
    )

    assert not result.resolved_doc_names
    assert result.warnings


def test_apply_query_understanding_sets_doc_name_when_confident() -> None:
    result = understand_query(
        "приказ 796 оперативный персонал",
        catalog=load_sample_catalog(),
        mode="auto",
    )
    query, filters = apply_query_understanding("приказ 796 оперативный персонал", {}, result)

    assert query
    if result.resolved_doc_names:
        assert filters.get("doc_name") == result.resolved_doc_names[0]


def test_normalize_llm_understanding_rejects_unknown_catalog_ids() -> None:
    candidates = [
        DocumentCandidate(catalog_id="doc_pue", doc_name="ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", score=0.9),
    ]
    catalog_by_id = {candidate.catalog_id: candidate for candidate in candidates}
    normalized, warnings = _normalize_llm_understanding_payload(
        {
            "search_query": "заземление",
            "document_hints": ["пуэ"],
            "selected_catalog_ids": ["doc_unknown", "doc_pue"],
            "point_number_hints": [],
            "confidence": 0.9,
            "warnings": [],
        },
        candidates,
        catalog_by_id,
    )

    assert normalized["resolved_doc_names"] == ["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"]
    assert any("unknown catalog_id" in warning for warning in warnings)


def test_understand_query_llm_mode_uses_provider(monkeypatch) -> None:
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
                "vector": ["заземление"],
                "point": [],
            },
        }, []

    monkeypatch.setattr(
        "mr_norm.runtime.query_planner._llm_plan",
        fake_llm_plan,
    )
    result = understand_query(
        "расскажи про ПУЭ по заземлению",
        catalog=load_sample_catalog(),
        mode="llm",
        llm_provider="ollama",
    )

    assert result.resolved_doc_names == ["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"]
    assert result.trace.resolver == "llm"
