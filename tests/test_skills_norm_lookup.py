from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import (
    Citation,
    FinalAnswerResult,
    PipelineResult,
    PipelineTrace,
    PlannerPlan,
    RerankResult,
    RuntimeMetrics,
    RuntimeResult,
    RuntimeTrace,
)
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup


def make_pipeline_result() -> PipelineResult:
    item = RetrievedItem(
        chunk_id="chunk_1",
        doc_name="ПУЭ",
        point_number="1.7.1",
        text="Требования к заземлению.",
        score=0.8,
        source_tool="payload",
    )
    runtime = RuntimeResult(
        items=[item],
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=1, tools_succeeded=1, items_returned=1),
    )
    return PipelineResult(
        runtime=runtime,
        planner=PlannerPlan(selected_tools=["payload"]),
        rerank=RerankResult(items=[item]),
        final_answer=FinalAnswerResult(
            answer="Ответ по норме",
            citations=[Citation(chunk_id="chunk_1", doc_name="ПУЭ", point_number="1.7.1")],
        ),
        trace=PipelineTrace(
            planner_backend="deterministic",
            reranker_backend="passthrough",
            final_answer_backend="evidence",
        ),
        warnings=["runtime ok"],
    )


def test_run_norm_lookup_maps_pipeline_to_skill_contract() -> None:
    request = NormLookupRequest(
        query="заземление",
        filters={"doc_name": "ПУЭ"},
        profile="balanced",
        limit=1,
        trace_id="skill_trace",
    )

    with patch("mr_norm.skills.norm_lookup.run_pipeline", return_value=make_pipeline_result()):
        result = run_norm_lookup(request, IndexingConfig(collection_name="test_collection"))

    assert result.answer == "Ответ по норме"
    assert result.citations[0].chunk_id == "chunk_1"
    assert result.evidence[0].chunk_id == "chunk_1"
    assert result.trace.planner_backend == "deterministic"
    assert result.trace.trace_id == "trace_1"
    assert "runtime ok" in result.warnings

    payload = result.to_dict()
    assert payload["answer"] == "Ответ по норме"
    assert payload["pipeline"]["final_answer"]["answer"] == "Ответ по норме"


def test_norm_lookup_golden_fixture_shape() -> None:
    request = NormLookupRequest(
        query="требования к заземлению",
        filters={"doc_name": "Правила устройства электроустановок"},
        limit=5,
    )

    with patch("mr_norm.skills.norm_lookup.run_pipeline", return_value=make_pipeline_result()):
        result = run_norm_lookup(request, IndexingConfig(collection_name="test_collection"))

    assert request.planner_backend == "deterministic"
    assert request.llm_provider == "none"
    assert len(result.evidence) == 1
