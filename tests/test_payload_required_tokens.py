from __future__ import annotations

from mr_norm.retrieval.contracts import ToolRequest
from mr_norm.retrieval.filters import build_payload_filter_spec


def test_payload_filter_required_tokens_are_must_conditions() -> None:
    spec = build_payload_filter_spec(
        "наведенное напряжение безопасно",
        {},
        required_tokens=("наведенное", "напряжение"),
    )
    must = spec.get("must") or []
    must_values = [item["value"] for item in must]
    assert len(must) == 2
    assert "наведенное" in must_values
    assert "напряжение" in must_values
    assert all(item["field"] == "text" for item in must)


def test_tool_request_carries_required_tokens() -> None:
    request = ToolRequest(query="наведенное напряжение", required_tokens=("наведенное", "напряжение"))
    assert request.required_tokens == ("наведенное", "напряжение")
