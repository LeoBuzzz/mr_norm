from __future__ import annotations

from unittest.mock import MagicMock, patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.doc_point_topic_index import TopicRerankResult
from mr_norm.runtime.contracts import (
    Citation,
    DocumentResolution,
    FinalAnswerResult,
    PipelineResult,
    PipelineTrace,
    PlannerPlan,
    PreparedQueryPlan,
    RerankResult,
    RuntimeMetrics,
    RuntimeResult,
    RuntimeTrace,
)
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup


def make_pipeline_result(*, items: list[RetrievedItem] | None = None) -> PipelineResult:
    baseline = items or [
        RetrievedItem(
            chunk_id="chunk_general",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.1",
            text="3.1. Общие требования.",
            score=0.95,
            source_tool="hybrid_rrf",
        ),
    ]
    runtime = RuntimeResult(
        items=baseline,
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=1, tools_succeeded=1, items_returned=1),
    )
    return PipelineResult(
        runtime=runtime,
        planner=PlannerPlan(selected_tools=["payload"]),
        rerank=RerankResult(items=baseline),
        final_answer=FinalAnswerResult(
            answer="Общий ответ",
            citations=[Citation(chunk_id="chunk_general", doc_name="Правила технической эксплуатации", point_number="3.1")],
        ),
        trace=PipelineTrace(
            planner_backend="deterministic",
            reranker_backend="passthrough",
            final_answer_backend="prompt",
        ),
        warnings=[],
    )


def test_run_norm_lookup_applies_topic_rerank_before_topic_retry(monkeypatch) -> None:
    promoted = [
        RetrievedItem(
            chunk_id="chunk_duty",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.2",
            text="3.2. Персонал обязан выполнять инструкции.",
            score=3.5,
            source_tool="topic_index",
        ),
        RetrievedItem(
            chunk_id="chunk_general",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.1",
            score=0.95,
            source_tool="hybrid_rrf",
        ),
    ]
    rerank_result = TopicRerankResult(
        ranked_items=tuple(promoted),
        applied=True,
        reason="topic_rerank:promoted",
        policy_label="base:promote",
        items_promoted=1,
        top_hits=(),
    )
    final_answer_mock = MagicMock()
    final_answer_mock.answer.return_value = FinalAnswerResult(
        answer="Обязанности персонала",
        citations=[Citation(chunk_id="chunk_duty", doc_name="Правила технической эксплуатации", point_number="3.2")],
    )

    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.prefetch_gost_snippets",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.run_pipeline",
        lambda *args, **kwargs: make_pipeline_result(),
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.load_default_doc_point_topic_index",
        lambda *args, **kwargs: MagicMock(entries=[]),
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.rerank_with_doc_point_topics",
        lambda *args, **kwargs: rerank_result,
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.build_pipeline_llm_providers",
        lambda *args, **kwargs: MagicMock(final_answer=final_answer_mock, planner=None, reranker=None),
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.build_final_answer",
        lambda *args, **kwargs: final_answer_mock,
    )
    monkeypatch.setattr(
        "mr_norm.skills.norm_lookup.plan_query",
        lambda *args, **kwargs: PreparedQueryPlan(
            original_query="Какие обязанности у персонала?",
            question_type="requirement",
            resolved_doc_names=("Правила технической эксплуатации",),
            document_resolution=DocumentResolution(catalog_id="doc_a", doc_name="Правила технической эксплуатации"),
        ),
    )

    result = run_norm_lookup(
        NormLookupRequest(
            query="Какие обязанности у персонала?",
            limit=2,
            understand_query_mode="off",
            final_answer_backend="prompt",
            llm_provider="polza",
        ),
        IndexingConfig(collection_name="test_collection"),
    )

    diagnostics = result.trace.pipeline_diagnostics
    assert diagnostics["topic_rerank_applied"] is True
    assert diagnostics["topic_rerank_items_promoted"] == 1
    assert diagnostics["topic_retry_triggered"] is False
    assert result.evidence[0].chunk_id == "chunk_duty"
    assert diagnostics["stage_timings"]["topic_rerank_sec"] >= 0.0
