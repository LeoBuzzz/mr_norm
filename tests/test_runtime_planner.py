from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace, ToolCallPlan
from mr_norm.runtime.planner import DeterministicPlanner, PromptPackPlanner, build_planner


def make_runtime_result(selected_tools: list[str]) -> RuntimeResult:
    request = ToolRequest(query="заземление", profile="balanced", trace_id="trace_1")
    plan = [
        ToolCallPlan(tool_name=name, request=request, reason=f"{name}: reason")
        for name in selected_tools
    ]
    return RuntimeResult(
        items=[RetrievedItem(chunk_id="chunk_1", source_tool="payload")],
        tool_results={},
        plan=plan,
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=selected_tools),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=len(plan), tools_succeeded=1, items_returned=1),
    )


def test_deterministic_planner_matches_route_runtime() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced", limit=5)
    plan = DeterministicPlanner().plan(request)

    assert plan.selected_tools == ["payload", "vector"]
    assert plan.routing_reasons[0].startswith("payload:")
    assert plan.schema_version == "mr_planner_plan_v1"


def test_deterministic_planner_warns_when_runtime_trace_differs() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced")
    runtime = make_runtime_result(["payload"])

    plan = DeterministicPlanner().plan(request, runtime)

    assert any("planner tools differ from runtime trace" in warning for warning in plan.warnings)


def test_prompt_pack_planner_falls_back_without_provider() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced")
    plan = PromptPackPlanner().plan(request)

    assert plan.selected_tools == ["payload", "vector"]
    assert any("provider not configured" in warning for warning in plan.warnings)


def test_prompt_pack_planner_uses_provider_and_filters_invalid_tools() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced")

    def provider(_request, _runtime, _pack):
        return {
            "selected_tools": ["payload", "unknown_tool", "vector"],
            "routing_reasons": ["payload: chosen", "vector: chosen"],
        }

    plan = PromptPackPlanner(provider=provider).plan(request)

    assert plan.selected_tools == ["payload", "vector"]
    assert any("ignored unknown tool" in warning for warning in plan.warnings)


def test_prompt_pack_planner_falls_back_on_invalid_payload() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced")

    def provider(_request, _runtime, _pack):
        return {"selected_tools": "bad", "routing_reasons": []}

    plan = PromptPackPlanner(provider=provider).plan(request)

    assert plan.selected_tools == ["payload", "vector"]
    assert any("prompt planner failed" in warning for warning in plan.warnings)


def test_build_planner_rejects_unknown_backend() -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported planner backend"):
        build_planner("unknown")
