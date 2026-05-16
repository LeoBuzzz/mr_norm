from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult


@dataclass(frozen=True)
class RuntimeRequest:
    query: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = 10
    profile: str = "balanced"
    trace_id: str = ""
    mode: str = "evidence"


@dataclass(frozen=True)
class ToolCallPlan:
    tool_name: str
    request: ToolRequest
    reason: str
    priority: int = 0


@dataclass(frozen=True)
class RuntimeTrace:
    trace_id: str = ""
    profile: str = "balanced"
    mode: str = "evidence"
    selected_tools: list[str] = field(default_factory=list)
    routing_reasons: list[str] = field(default_factory=list)
    fusion: str = ""
    empty_reason: str = ""


@dataclass(frozen=True)
class RuntimeMetrics:
    elapsed_sec: float
    tools_planned: int
    tools_succeeded: int
    items_returned: int
    qdrant_calls: int = 0


@dataclass(frozen=True)
class RuntimeResult:
    items: list[RetrievedItem]
    tool_results: dict[str, ToolResult]
    plan: list[ToolCallPlan]
    trace: RuntimeTrace
    metrics: RuntimeMetrics
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [asdict(item) for item in self.items],
            "tool_results": {name: result.to_dict() for name, result in self.tool_results.items()},
            "plan": [
                {
                    "tool_name": step.tool_name,
                    "reason": step.reason,
                    "priority": step.priority,
                    "request": asdict(step.request),
                }
                for step in self.plan
            ],
            "trace": asdict(self.trace),
            "metrics": asdict(self.metrics),
            "warnings": list(self.warnings),
        }
