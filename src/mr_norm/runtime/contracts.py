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


@dataclass(frozen=True)
class PlannerPlan:
    selected_tools: list[str] = field(default_factory=list)
    routing_reasons: list[str] = field(default_factory=list)
    filter_hints: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "mr_planner_plan_v1"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selected_tools": list(self.selected_tools),
            "routing_reasons": list(self.routing_reasons),
            "filter_hints": dict(self.filter_hints),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RerankResult:
    items: list[RetrievedItem] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    schema_version: str = "mr_rerank_v1"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "items": [asdict(item) for item in self.items],
            "scores": dict(self.scores),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    doc_name: str = ""
    point_number: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_name": self.doc_name,
            "point_number": self.point_number,
        }


@dataclass(frozen=True)
class FinalAnswerResult:
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    schema_version: str = "mr_final_answer_v1"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "answer": self.answer,
            "citations": [citation.to_dict() for citation in self.citations],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PipelineTrace:
    planner_backend: str = ""
    reranker_backend: str = ""
    final_answer_backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PipelineResult:
    runtime: RuntimeResult
    planner: PlannerPlan
    rerank: RerankResult
    final_answer: FinalAnswerResult
    trace: PipelineTrace = field(default_factory=PipelineTrace)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime.to_dict(),
            "planner": self.planner.to_dict(),
            "rerank": self.rerank.to_dict(),
            "final_answer": self.final_answer.to_dict(),
            "trace": self.trace.to_dict(),
            "warnings": list(self.warnings),
        }
