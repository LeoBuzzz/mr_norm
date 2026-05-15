from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolRequest:
    query: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    limit: int = 10
    profile: str = "fast"
    trace_id: str = ""


@dataclass(frozen=True)
class RetrievedItem:
    chunk_id: str
    doc_id: str = ""
    point_id: str = ""
    filename: str = ""
    doc_name: str = ""
    heading_path_text: str = ""
    point_number: str = ""
    text: str = ""
    score: float | None = None
    source_tool: str = ""
    point_identity_key: str = ""
    qdrant_point_id: str = ""
    matched: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    trace_id: str = ""
    collection_name: str = ""
    vector_name: str = ""
    query: str = ""
    normalized_filters: dict[str, Any] = field(default_factory=dict)
    qdrant_filter: dict[str, Any] = field(default_factory=dict)
    limit: int = 10
    profile: str = "fast"
    empty_reason: str = ""


@dataclass(frozen=True)
class ToolMetrics:
    elapsed_sec: float
    candidates_returned: int
    qdrant_calls: int = 0


@dataclass(frozen=True)
class ToolResult:
    items: list[RetrievedItem]
    trace: ToolTrace
    metrics: ToolMetrics
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [asdict(item) for item in self.items],
            "trace": asdict(self.trace),
            "metrics": asdict(self.metrics),
            "warnings": list(self.warnings),
        }


def clamp_limit(limit: int, *, default: int = 10, maximum: int = 100) -> int:
    if limit <= 0:
        return default
    return min(limit, maximum)
