from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.runtime.contracts import PreparedQueryPlan
from mr_norm.runtime.dialog_memory import DialogContext, DialogSourceReference, DialogTurn
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup


def test_run_norm_lookup_point_followup_uses_prior_citation_doc() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="что такое оперативный персонал",
                answer="определение",
                source_references=(
                    DialogSourceReference(
                        doc_id="doc_pte",
                        doc_name="Правила технической эксплуатации",
                        point_number="65",
                    ),
                ),
            ),
        ),
    )
    captured_filters: dict[str, object] = {}

    def fake_plan_query(*args, **kwargs):
        captured_filters["filters"] = kwargs.get("filters")
        return PreparedQueryPlan(
            original_query=str(kwargs.get("user_query") or args[0]),
            question_type="point_lookup",
            point_number_hints=("65",),
        )

    with patch("mr_norm.skills.norm_lookup.plan_query", side_effect=fake_plan_query):
        with patch("mr_norm.skills.norm_lookup.prefetch_gost_snippets", return_value=[]):
            with patch("mr_norm.skills.norm_lookup._resolve_document_for_norm_lookup") as resolve_doc:
                from mr_norm.skills.document_resolve import DocumentResolveResult

                resolve_doc.return_value = DocumentResolveResult(found=False, status="not_found")
                with patch("mr_norm.skills.norm_lookup._try_point_lookup_fast_path") as fast_path:
                    fast_path.return_value = None
                    with patch(
                        "mr_norm.skills.norm_lookup.should_retry_doc_point_lookup",
                        return_value=(False, ""),
                    ):
                        with patch("mr_norm.skills.norm_lookup.run_pipeline") as run_pipeline:
                            from mr_norm.runtime.contracts import (
                                FinalAnswerResult,
                                PipelineResult,
                                PipelineTrace,
                                PlannerPlan,
                                RerankResult,
                                RuntimeMetrics,
                                RuntimeResult,
                                RuntimeTrace,
                            )

                            run_pipeline.return_value = PipelineResult(
                                runtime=RuntimeResult(
                                    items=[],
                                    tool_results={},
                                    plan=[],
                                    trace=RuntimeTrace(trace_id="t"),
                                    metrics=RuntimeMetrics(
                                        elapsed_sec=0.0,
                                        tools_planned=0,
                                        tools_succeeded=0,
                                        items_returned=0,
                                    ),
                                ),
                                planner=PlannerPlan(selected_tools=[]),
                                rerank=RerankResult(items=[]),
                                final_answer=FinalAnswerResult(answer="ok", citations=[], warnings=[]),
                                trace=PipelineTrace(
                                    planner_backend="deterministic",
                                    reranker_backend="score",
                                    final_answer_backend="evidence",
                                ),
                                warnings=[],
                                diagnostics={},
                            )
                            run_norm_lookup(
                                NormLookupRequest(
                                    query="Дай текст п. 65",
                                    understand_query_mode="auto",
                                    dialog_context=context,
                                ),
                                IndexingConfig(collection_name="test"),
                            )

    filters = captured_filters.get("filters") or {}
    assert filters.get("doc_id") == "doc_pte"
    assert filters.get("point_number") == "65"


def test_run_norm_lookup_passes_dialog_context_to_plan_query() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(DialogTurn(user_text="первый вопрос", answer="ответ"),),
    )
    captured: dict[str, object] = {}

    def fake_plan_query(*args, **kwargs):
        captured["dialog_context"] = kwargs.get("dialog_context")
        captured["user_query"] = kwargs.get("user_query")
        captured["query"] = args[0]
        return PreparedQueryPlan(
            original_query=str(kwargs.get("user_query") or args[0]),
            question_type="factual",
        )

    with patch("mr_norm.skills.norm_lookup.plan_query", side_effect=fake_plan_query):
        with patch("mr_norm.skills.norm_lookup.prefetch_gost_snippets", return_value=[]):
            with patch("mr_norm.skills.norm_lookup.run_pipeline") as run_pipeline:
                from mr_norm.runtime.contracts import FinalAnswerResult, PipelineResult, PipelineTrace, PlannerPlan, RerankResult, RuntimeResult, RuntimeTrace, RuntimeMetrics

                run_pipeline.return_value = PipelineResult(
                    runtime=RuntimeResult(
                        items=[],
                        tool_results={},
                        plan=[],
                        trace=RuntimeTrace(trace_id="t"),
                        metrics=RuntimeMetrics(elapsed_sec=0.0, tools_planned=0, tools_succeeded=0, items_returned=0),
                    ),
                    planner=PlannerPlan(selected_tools=[]),
                    rerank=RerankResult(items=[]),
                    final_answer=FinalAnswerResult(answer="ok", citations=[], warnings=[]),
                    trace=PipelineTrace(
                        planner_backend="deterministic",
                        reranker_backend="score",
                        final_answer_backend="evidence",
                    ),
                    warnings=[],
                    diagnostics={},
                )
                run_norm_lookup(
                    NormLookupRequest(
                        query="как часто?",
                        understand_query_mode="auto",
                        dialog_context=context,
                    ),
                    IndexingConfig(collection_name="test"),
                )

    assert captured["dialog_context"] is context
    assert captured["user_query"] == "как часто?"
    assert "первый вопрос" in str(captured["query"])


def test_run_norm_lookup_without_dialog_context_is_unchanged() -> None:
    captured: dict[str, object] = {}

    def fake_plan_query(*args, **kwargs):
        captured["dialog_context"] = kwargs.get("dialog_context")
        captured["query"] = args[0]
        return PreparedQueryPlan(original_query=args[0], question_type="factual")

    with patch("mr_norm.skills.norm_lookup.plan_query", side_effect=fake_plan_query):
        with patch("mr_norm.skills.norm_lookup.prefetch_gost_snippets", return_value=[]):
            with patch("mr_norm.skills.norm_lookup.run_pipeline") as run_pipeline:
                from mr_norm.runtime.contracts import FinalAnswerResult, PipelineResult, PipelineTrace, PlannerPlan, RerankResult, RuntimeResult, RuntimeTrace, RuntimeMetrics

                run_pipeline.return_value = PipelineResult(
                    runtime=RuntimeResult(
                        items=[],
                        tool_results={},
                        plan=[],
                        trace=RuntimeTrace(trace_id="t"),
                        metrics=RuntimeMetrics(elapsed_sec=0.0, tools_planned=0, tools_succeeded=0, items_returned=0),
                    ),
                    planner=PlannerPlan(selected_tools=[]),
                    rerank=RerankResult(items=[]),
                    final_answer=FinalAnswerResult(answer="ok", citations=[], warnings=[]),
                    trace=PipelineTrace(
                        planner_backend="deterministic",
                        reranker_backend="score",
                        final_answer_backend="evidence",
                    ),
                    warnings=[],
                    diagnostics={},
                )
                run_norm_lookup(
                    NormLookupRequest(
                        query="одиночный вопрос",
                        understand_query_mode="auto",
                    ),
                    IndexingConfig(collection_name="test"),
                )

    assert captured["dialog_context"] is None
    assert captured["query"] == "одиночный вопрос"
