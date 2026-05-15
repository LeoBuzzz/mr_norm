from __future__ import annotations

import time
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace, clamp_limit
from mr_norm.retrieval.filters import filter_spec_to_trace, normalize_filters


def start_timer() -> float:
    return time.perf_counter()


def build_result(
    *,
    tool_name: str,
    request: ToolRequest,
    config: IndexingConfig,
    filter_spec: dict[str, Any],
    items: list[RetrievedItem],
    started_at: float,
    qdrant_calls: int,
    warnings: list[str] | None = None,
    vector_name: str | None = None,
) -> ToolResult:
    normalized_filters = normalize_filters(request.filters)
    empty_reason = ""
    if not items:
        empty_reason = "no_qdrant_matches"
        if not request.query.strip() and not normalized_filters:
            empty_reason = "empty_query_and_filters"
    return ToolResult(
        items=items,
        trace=ToolTrace(
            tool_name=tool_name,
            trace_id=request.trace_id,
            collection_name=config.collection_name,
            vector_name=vector_name or config.vector_name,
            query=request.query.strip(),
            normalized_filters=normalized_filters,
            qdrant_filter=filter_spec_to_trace(filter_spec),
            limit=clamp_limit(request.limit),
            profile=request.profile,
            empty_reason=empty_reason,
        ),
        metrics=ToolMetrics(
            elapsed_sec=round(time.perf_counter() - started_at, 6),
            candidates_returned=len(items),
            qdrant_calls=qdrant_calls,
        ),
        warnings=warnings or [],
    )
