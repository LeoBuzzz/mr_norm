from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.runtime.contracts import (
    Citation,
    FinalAnswerResult,
    PipelineResult,
    PipelineTrace,
    PlannerPlan,
    RerankResult,
    RuntimeMetrics,
    RuntimeRequest,
    RuntimeResult,
    RuntimeTrace,
    ToolCallPlan,
)


def make_runtime_result() -> RuntimeResult:
    request = ToolRequest(query="заземление", profile="balanced", trace_id="trace_1")
    item = RetrievedItem(chunk_id="chunk_1", doc_name="ПУЭ", point_number="1.7.1", source_tool="payload")
    tool_result = ToolResult(
        items=[item],
        trace=ToolTrace(tool_name="payload", trace_id="trace_1"),
        metrics=ToolMetrics(elapsed_sec=0.001, candidates_returned=1),
    )
    return RuntimeResult(
        items=[item],
        tool_results={"payload": tool_result},
        plan=[
            ToolCallPlan(
                tool_name="payload",
                request=request,
                reason="text lookup",
            )
        ],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(
            elapsed_sec=0.01,
            tools_planned=1,
            tools_succeeded=1,
            items_returned=1,
        ),
    )


def test_runtime_result_to_dict_is_unchanged_shape() -> None:
    payload = make_runtime_result().to_dict()

    assert set(payload) == {"items", "tool_results", "plan", "trace", "metrics", "warnings"}
    assert payload["items"][0]["chunk_id"] == "chunk_1"
    assert payload["plan"][0]["tool_name"] == "payload"


def test_planner_plan_to_dict() -> None:
    plan = PlannerPlan(
        selected_tools=["payload", "vector"],
        routing_reasons=["payload: text lookup"],
        filter_hints={"doc_name": "ПУЭ"},
    )

    payload = plan.to_dict()

    assert payload["schema_version"] == "mr_planner_plan_v1"
    assert payload["selected_tools"] == ["payload", "vector"]
    assert payload["filter_hints"] == {"doc_name": "ПУЭ"}


def test_rerank_result_to_dict() -> None:
    item = RetrievedItem(chunk_id="chunk_1", score=0.9, source_tool="payload")
    result = RerankResult(items=[item], scores={"chunk_1": 0.9})

    payload = result.to_dict()

    assert payload["schema_version"] == "mr_rerank_v1"
    assert payload["scores"] == {"chunk_1": 0.9}
    assert payload["items"][0]["chunk_id"] == "chunk_1"


def test_final_answer_result_to_dict() -> None:
    result = FinalAnswerResult(
        answer="Норма требует заземления.",
        citations=[Citation(chunk_id="chunk_1", doc_name="ПУЭ", point_number="1.7.1")],
    )

    payload = result.to_dict()

    assert payload["schema_version"] == "mr_final_answer_v1"
    assert payload["answer"] == "Норма требует заземления."
    assert payload["citations"][0]["chunk_id"] == "chunk_1"


def test_pipeline_result_to_dict() -> None:
    runtime = make_runtime_result()
    pipeline = PipelineResult(
        runtime=runtime,
        planner=PlannerPlan(selected_tools=["payload"], routing_reasons=["payload: text lookup"]),
        rerank=RerankResult(items=runtime.items),
        final_answer=FinalAnswerResult(answer="answer", citations=[]),
        trace=PipelineTrace(
            planner_backend="deterministic",
            reranker_backend="passthrough",
            final_answer_backend="evidence_only",
        ),
        warnings=["pipeline warning"],
    )

    payload = pipeline.to_dict()

    assert payload["runtime"]["items"][0]["chunk_id"] == "chunk_1"
    assert payload["planner"]["selected_tools"] == ["payload"]
    assert payload["rerank"]["items"][0]["chunk_id"] == "chunk_1"
    assert payload["final_answer"]["answer"] == "answer"
    assert payload["trace"]["planner_backend"] == "deterministic"
    assert payload["warnings"] == ["pipeline warning"]


def test_runtime_request_defaults_are_unchanged() -> None:
    request = RuntimeRequest()

    assert request.query == ""
    assert request.filters == {}
    assert request.limit == 10
    assert request.profile == "balanced"
    assert request.mode == "evidence"
