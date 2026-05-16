from __future__ import annotations

from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.profiles import get_profile_config
from mr_norm.runtime.router import route_runtime


def test_route_runtime_rejects_empty_query_and_filters() -> None:
    plan, warnings = route_runtime(RuntimeRequest())

    assert plan == []
    assert any("requires a non-empty query" in item for item in warnings)


def test_route_runtime_selects_point_and_payload_for_point_lookup() -> None:
    plan, warnings = route_runtime(
        RuntimeRequest(
            query="",
            filters={"doc_name": "ПУЭ", "point_number": "1.7.1"},
            profile="balanced",
        )
    )

    assert warnings == []
    assert [step.tool_name for step in plan] == ["point", "payload"]


def test_route_runtime_selects_payload_and_vector_for_query_balanced() -> None:
    plan, _warnings = route_runtime(
        RuntimeRequest(query="требования к заземлению", filters={"doc_name": "ПУЭ"}, profile="balanced")
    )

    assert [step.tool_name for step in plan] == ["payload", "vector"]


def test_route_runtime_fast_skips_hybrid_but_keeps_vector_for_query() -> None:
    plan, _warnings = route_runtime(RuntimeRequest(query="заземление", profile="fast"))

    assert [step.tool_name for step in plan] == ["payload", "vector"]
    assert get_profile_config("fast").use_hybrid is False


def test_route_runtime_deep_uses_same_tools_as_balanced_with_higher_default_limit() -> None:
    balanced_plan, _ = route_runtime(RuntimeRequest(query="заземление", profile="balanced"))
    deep_plan, _ = route_runtime(RuntimeRequest(query="заземление", profile="deep"))

    assert [step.tool_name for step in balanced_plan] == [step.tool_name for step in deep_plan]
    assert get_profile_config("deep").default_limit > get_profile_config("balanced").default_limit
