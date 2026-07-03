"""Tests for pipeline enhancement diagnostics."""

from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import PreparedToolQuery
from mr_norm.runtime.pipeline_diagnostics import (
    apply_intent_tool_routing,
    merge_retry_items,
    rerank_items_for_doc_point,
    resolve_retrieval_limits,
    select_items_for_final_answer,
    should_retry_doc_point_lookup,
    should_retry_doc_scoped_lookup,
    should_retry_soft_catalog_lookup,
    summarize_source_ranks,
    try_early_deterministic_doc_resolution,
)
from mr_norm.runtime.contracts import DocumentResolution, PreparedQueryPlan, RuntimeRequest


def _item(**kwargs):
    defaults = {
        "chunk_id": "",
        "doc_id": "",
        "doc_name": "",
        "point_number": "",
        "text": "sample",
        "source_tool": "hybrid_rrf",
        "score": 0.1,
        "matched": {},
    }
    defaults.update(kwargs)
    return RetrievedItem(**defaults)


def test_resolve_retrieval_limits_wide_pool():
    retrieval, final, wide = resolve_retrieval_limits(40)
    assert retrieval == 80
    assert final == 25
    assert wide is True


def test_select_items_for_final_answer_keeps_resolved_doc_slots():
    ranked = [
        _item(chunk_id="g1", doc_name="GOST", score=0.95),
        _item(chunk_id="g2", doc_name="GOST", score=0.94),
        _item(chunk_id="d1", doc_id="doc_1", doc_name="Target Doc", point_number="1", score=0.5),
        _item(chunk_id="d2", doc_id="doc_1", doc_name="Target Doc", point_number="2", score=0.4),
        _item(chunk_id="d3", doc_id="doc_1", doc_name="Target Doc", point_number="3", score=0.3),
    ]
    request = RuntimeRequest(
        query="test",
        filters={"doc_id": "doc_1"},
        limit=40,
        retrieval_limit=80,
        final_answer_limit=25,
    )
    selected = select_items_for_final_answer(ranked, request=request, limit=4)
    selected_ids = [item.chunk_id for item in selected]
    assert "d1" in selected_ids
    assert "d2" in selected_ids
    assert len(selected) == 4


def test_should_retry_doc_scoped_lookup_when_doc_missing_from_final_top():
    ranked = [_item(chunk_id="x", doc_name="Other", score=0.9) for _ in range(5)]
    do_retry, reason = should_retry_doc_scoped_lookup(
        filters={"doc_id": "doc_1"},
        prepared_plan=None,
        ranked_items=ranked,
        tool_results={"payload": type("R", (), {"items": [_item(chunk_id="p1", doc_id="doc_1")]})()},
        top_n=4,
        question_type="requirement",
    )
    assert do_retry is True
    assert reason.startswith("retry:")


def test_select_items_for_final_answer_prioritizes_exact_point_from_wide_pool():
    ranked = [
        _item(chunk_id=f"noise_{idx}", doc_name="Other", score=0.99 - idx * 0.01)
        for idx in range(30)
    ]
    ranked.append(
        _item(
            chunk_id="gold",
            doc_id="doc_1",
            doc_name="Target Doc",
            point_number="18",
            score=0.05,
        )
    )
    request = RuntimeRequest(
        query="test",
        filters={"doc_id": "doc_1", "point_number": "18"},
        limit=40,
        retrieval_limit=80,
        final_answer_limit=25,
    )
    selected = select_items_for_final_answer(ranked, request=request, limit=25)
    assert any(item.chunk_id == "gold" for item in selected)


def test_intent_tool_routing_requirement_broad():
    prepared = (
        PreparedToolQuery(tool_name="vector", queries=("query",)),
        PreparedToolQuery(tool_name="payload", queries=("query",)),
    )
    tools, routed, mode = apply_intent_tool_routing(
        prepared,
        question_type="requirement",
        doc_scoped=False,
        point_number_hints=[],
        original_query="Какие требования к хранению документации?",
    )
    assert tools[0] == "vector"
    assert mode == "requirement_broad"

    tools_explicit, _, _ = apply_intent_tool_routing(
        prepared,
        question_type="requirement",
        doc_scoped=False,
        point_number_hints=[],
        original_query="Что в приказе Минэнерго №548?",
    )
    assert tools_explicit[0] == "point"


def test_soft_catalog_retry_allows_vector_confirmed_low_score_candidate():
    plan = PreparedQueryPlan(
        original_query="какие требования к интернет доступу для дистанционного управления",
        question_type="requirement",
        candidates=(
            {
                "catalog_id": "doc_kii",
                "doc_name": "Требования безопасности значимых объектов критической информационной инфраструктуры",
                "score": 0.40,
                "reasons": ["semantic_anchor:internet_remote_control_kii"],
            },
            {"catalog_id": "doc_other", "doc_name": "Другой документ", "score": 0.20, "reasons": []},
        ),
        document_resolution=DocumentResolution(confidence=0.40),
    )
    ranked = [_item(chunk_id=f"noise_{idx}", doc_id="doc_noise") for idx in range(30)]
    ranked.append(_item(chunk_id="candidate_doc", doc_id="doc_kii", doc_name="Target", score=0.2))

    do_retry, reason, retry_filters = should_retry_soft_catalog_lookup(
        filters={},
        prepared_plan=plan,
        ranked_items=ranked,
        top_n=25,
    )

    assert do_retry is True
    assert "soft_catalog_vector_confirmed" in reason
    assert retry_filters == {"doc_id": "doc_kii"}


def test_soft_catalog_retry_rejects_ambiguous_low_score_candidate():
    plan = PreparedQueryPlan(
        original_query="какие требования предъявляются",
        question_type="requirement",
        ambiguous=True,
        candidates=(
            {"catalog_id": "doc_a", "doc_name": "Документ А", "score": 0.40, "reasons": []},
            {"catalog_id": "doc_b", "doc_name": "Документ Б", "score": 0.38, "reasons": []},
        ),
    )
    ranked = [_item(chunk_id="candidate_doc", doc_id="doc_a", score=0.9)]

    do_retry, reason, retry_filters = should_retry_soft_catalog_lookup(
        filters={},
        prepared_plan=plan,
        ranked_items=ranked,
        top_n=0,
    )

    assert do_retry is False
    assert reason == "retry_skip:soft_catalog_ambiguous_rejected"
    assert retry_filters == {}


def test_select_items_for_final_answer_uses_soft_catalog_candidate_slots():
    plan = PreparedQueryPlan(
        original_query="какие требования к интернет доступу для дистанционного управления",
        question_type="requirement",
        candidates=(
            {"catalog_id": "doc_kii", "doc_name": "Target Doc", "score": 0.40, "reasons": []},
            {"catalog_id": "doc_other", "doc_name": "Other Doc", "score": 0.20, "reasons": []},
        ),
    )
    ranked = [_item(chunk_id=f"noise_{idx}", doc_id="doc_noise", score=0.99 - idx * 0.01) for idx in range(12)]
    ranked.extend(
        [
            _item(chunk_id="target_1", doc_id="doc_kii", doc_name="Target Doc", score=0.2),
            _item(chunk_id="target_2", doc_id="doc_kii", doc_name="Target Doc", score=0.1),
        ]
    )
    request = RuntimeRequest(query=plan.original_query, prepared_plan=plan, limit=40, final_answer_limit=8)

    selected = select_items_for_final_answer(ranked, request=request, limit=8)

    selected_ids = [item.chunk_id for item in selected]
    assert "target_1" in selected_ids
    assert selected_ids[0] == "target_1"


def test_early_deterministic_doc_resolution_order_number():
    candidates = [
        {
            "catalog_id": "doc_1",
            "doc_name": "Приказ Минэнерго №548",
            "score": 0.62,
            "reasons": ["order_number:548"],
        }
    ]
    names, catalog_id, confidence, ambiguous, applied, reason = try_early_deterministic_doc_resolution(
        candidates,
        original_query="Что в приказе Минэнерго №548?",
    )
    assert applied is True
    assert names == ["Приказ Минэнерго №548"]
    assert catalog_id == "doc_1"
    assert confidence == 0.62
    assert ambiguous is False
    assert "order_number" in reason


def test_intent_tool_routing_point_lookup():
    prepared = (
        PreparedToolQuery(tool_name="vector", queries=("query",)),
        PreparedToolQuery(tool_name="payload", queries=("query",)),
    )
    tools, routed, mode = apply_intent_tool_routing(
        prepared,
        question_type="point_lookup",
        doc_scoped=True,
        point_number_hints=["18"],
    )
    assert tools[0] == "point"
    assert any(entry.tool_name == "point" for entry in routed)
    assert mode == "point_lookup_path"


def test_rerank_items_for_doc_point_promotes_exact_match():
    items = [
        _item(chunk_id="a", doc_id="d1", doc_name="Doc A", point_number="2", score=0.9),
        _item(chunk_id="b", doc_id="d1", doc_name="Doc A", point_number="18", score=0.2),
    ]
    ranked, moves = rerank_items_for_doc_point(
        items,
        doc_id="d1",
        doc_name="Doc A",
        point_number="18",
    )
    assert ranked[0].chunk_id == "b"
    assert moves >= 1


def test_should_retry_doc_point_lookup():
    do_retry, reason = should_retry_doc_point_lookup(
        filters={"doc_id": "d1", "point_number": "18"},
        prepared_plan=None,
        ranked_items=[_item(chunk_id="x", doc_id="d1", doc_name="Doc", point_number="2")],
        tool_results={"point": type("R", (), {"items": []})()},
        top_n=15,
    )
    assert do_retry is True
    assert reason.startswith("retry:")


def test_merge_retry_items_prefers_retry_first():
    primary = [_item(chunk_id="p1", score=0.5)]
    retry = [_item(chunk_id="r1", score=0.9)]
    merged, added = merge_retry_items(primary, retry, limit=5)
    assert merged[0].chunk_id == "r1"
    assert added == 1


def test_summarize_source_ranks():
    summary = summarize_source_ranks(
        [
            _item(
                chunk_id="h1",
                matched={"source_ranks": {"vector": 3, "payload": 1}},
            )
        ],
        top_n=5,
    )
    assert summary["both_vector_and_payload"] == 1
    assert summary["items"][0]["source_ranks"]["payload"] == 1
