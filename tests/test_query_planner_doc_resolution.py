from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.document_catalog import load_catalog_snapshot
from mr_norm.retrieval.document_knowledge import load_document_knowledge
from mr_norm.runtime.query_planner import (
    _boost_semantic_catalog_candidates,
    _conditional_deterministic_fallback,
    _demote_generic_fz_candidates,
    plan_query,
    prepare_query,
)


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def load_sample_knowledge():
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    return load_document_knowledge(path)


def test_demote_generic_fz_for_energy_query() -> None:
    candidates = [
        {
            "catalog_id": "doc_tech",
            "doc_name": "О техническом регулировании",
            "score": 0.35,
            "reasons": ["fuzzy_doc_name:0.60"],
        },
        {
            "catalog_id": "doc_796",
            "doc_name": "Об утверждении Правил работы с персоналом",
            "score": 0.32,
            "reasons": ["order_number:796"],
        },
    ]
    adjusted = _demote_generic_fz_candidates(
        candidates,
        "какие требования к персоналу на электроустановках",
    )
    assert adjusted[0]["doc_name"].startswith("Об утверждении Правил")
    assert float(adjusted[1]["score"]) == 0.2


def test_conditional_fallback_skips_generic_fz_top() -> None:
    names, catalog_id, _confidence, _ambiguous, warnings = _conditional_deterministic_fallback(
        [
            {
                "catalog_id": "doc_tech",
                "doc_name": "О техническом регулировании",
                "score": 0.35,
                "reasons": [],
            }
        ],
        original_query="какие требования к персоналу",
    )
    assert names == []
    assert catalog_id == ""
    assert any("generic tech-reg FZ" in item for item in warnings)


def test_conditional_fallback_uses_soft_threshold_for_natural_query() -> None:
    names, catalog_id, confidence, ambiguous, warnings = _conditional_deterministic_fallback(
        [
            {
                "catalog_id": "doc_tek",
                "doc_name": "О показателях технико-экономического состояния",
                "score": 0.52,
                "reasons": ["topic_alias:tek"],
            },
            {
                "catalog_id": "doc_other",
                "doc_name": "Другой документ",
                "score": 0.20,
                "reasons": [],
            },
        ],
        original_query="Когда нужно рассчитать показатели технико-экономического состояния?",
    )
    assert names == ["О показателях технико-экономического состояния"]
    assert catalog_id == "doc_tek"
    assert confidence == 0.52
    assert ambiguous is False
    assert any("high-confidence top candidate" in item for item in warnings)


def test_semantic_anchor_boosts_matching_catalog_candidate() -> None:
    adjusted = _boost_semantic_catalog_candidates(
        [
            {
                "catalog_id": "doc_kii",
                "doc_name": "Об утверждении требований безопасности значимых объектов критической информационной инфраструктуры",
                "score": 0.32,
                "reasons": [],
            },
            {"catalog_id": "doc_other", "doc_name": "О розничных рынках электрической энергии", "score": 0.35, "reasons": []},
        ],
        "Разрешен ли доступ из интернета в технологические сети связи для дистанционного управления?",
    )

    assert adjusted[0]["catalog_id"] == "doc_kii"
    assert any(str(reason).startswith("semantic_anchor:") for reason in adjusted[0]["reasons"])


def test_semantic_anchor_skips_explicit_document_reference() -> None:
    candidates = [
        {
            "catalog_id": "doc_kii",
            "doc_name": "Об утверждении требований безопасности значимых объектов критической информационной инфраструктуры",
            "score": 0.32,
            "reasons": [],
        }
    ]

    adjusted = _boost_semantic_catalog_candidates(
        candidates,
        "Что сказано в приказе Минэнерго №548 про интернет технологии?",
    )

    assert adjusted == candidates


def test_semantic_anchor_boosts_voltage_quality_responsibility_doc() -> None:
    adjusted = _boost_semantic_catalog_candidates(
        [
            {
                "catalog_id": "doc_quality",
                "doc_name": "Об утверждении требований к качеству электрической энергии и распределению обязанностей между потребителями",
                "score": 0.31,
                "reasons": [],
            },
            {"catalog_id": "doc_market", "doc_name": "О функционировании розничных рынков", "score": 0.36, "reasons": []},
        ],
        "Кто отвечает за соблюдение норм по отклонению напряжения в точке присоединения к сети?",
    )

    assert adjusted[0]["catalog_id"] == "doc_quality"
    assert "semantic_anchor:voltage_quality_responsibility" in adjusted[0]["reasons"]


def test_conditional_fallback_uses_order_number_match() -> None:
    names, catalog_id, confidence, ambiguous, warnings = _conditional_deterministic_fallback(
        [
            {
                "catalog_id": "doc_796",
                "doc_name": "Правила работы с персоналом",
                "score": 0.48,
                "reasons": ["order_number:796"],
            }
        ],
        original_query="что в приказе минэнерго 796 про оперативный персонал",
    )
    assert names == ["Правила работы с персоналом"]
    assert catalog_id == "doc_796"
    assert confidence >= 0.6
    assert ambiguous is False
    assert any("order_number match" in item for item in warnings)


def test_prepare_query_partial_order_before_llm() -> None:
    plan = prepare_query(
        "что в приказе минэнерго 796 про оперативный персонал",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="auto",
    )
    assert plan.resolved_doc_names
    assert "796" in plan.resolved_doc_names[0] or plan.resolved_doc_names[0].startswith("Об утверждении")
    assert plan.trace.resolver == "partial_order"


def test_plan_query_llm_refusal_uses_conditional_fallback(monkeypatch) -> None:
    def fake_llm_plan(query, candidates, matched_terms, *, llm_provider, keys_path=None, gost_definitions=None):
        return {
            "question_type": "factual",
            "answer_shape": "narrow",
            "concepts": [],
            "significant_words": [],
            "selected_catalog_ids": [],
            "point_number_hints": [],
            "confidence": 0.2,
            "tool_queries": {"payload": [query], "vector": [query], "point": []},
            "warnings": [],
        }, []

    monkeypatch.setattr("mr_norm.runtime.query_planner._llm_plan", fake_llm_plan)
    plan = plan_query(
        "что в приказе минэнерго 796 про оперативный персонал",
        catalog=load_sample_catalog(),
        knowledge=load_sample_knowledge(),
        mode="llm",
        llm_provider="ollama",
    )
    assert plan.resolved_doc_names
    assert plan.trace.resolver in {"partial_order", "llm"}
