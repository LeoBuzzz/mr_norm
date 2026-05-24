"""Tests for per-stage pipeline timing."""

from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace
from mr_norm.runtime.final_answer import EvidenceOnlyFinalAnswer
from mr_norm.runtime.pipeline import run_pipeline
from mr_norm.runtime.pipeline_timing import PipelineStageTimings
from mr_norm.runtime.planner import DeterministicPlanner
from mr_norm.runtime.reranker import PassthroughReranker


def _runtime_result() -> RuntimeResult:
    item = RetrievedItem(
        chunk_id="chunk_1",
        doc_name="Doc",
        point_number="1",
        text="sample",
        score=0.8,
        source_tool="payload",
    )
    return RuntimeResult(
        items=[item],
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="t1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=1, tools_succeeded=1, items_returned=1),
    )


def test_run_pipeline_records_stage_timings() -> None:
    request = RuntimeRequest(query="test", profile="balanced", limit=10)
    with patch("mr_norm.runtime.pipeline.run_runtime", return_value=_runtime_result()):
        result = run_pipeline(
            request,
            IndexingConfig(collection_name="test_collection"),
            planner=DeterministicPlanner(),
            reranker=PassthroughReranker(),
            final_answer=EvidenceOnlyFinalAnswer(),
        )
    timings = result.diagnostics.get("stage_timings") or {}
    assert timings.get("retrieval_sec", 0) >= 0
    assert timings.get("final_answer_sec", 0) >= 0
    assert "planner_sec" in timings


def test_pipeline_stage_timings_with_total() -> None:
    timings = PipelineStageTimings(gost_prefetch_sec=0.1, retrieval_sec=1.5, final_answer_sec=2.0)
    payload = timings.with_total().to_dict()
    assert payload["total_sec"] == round(0.1 + 1.5 + 2.0, 3)
