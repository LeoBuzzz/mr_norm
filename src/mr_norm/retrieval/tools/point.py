from __future__ import annotations

from typing import Any, Protocol

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult, clamp_limit
from mr_norm.retrieval.filters import build_filter_spec, doc_name_variants
from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient
from mr_norm.retrieval.tools.common import build_result, start_timer


class PointSearchClient(Protocol):
    calls: int

    def payload_search(self, filter_spec: dict, *, limit: int, source_tool: str) -> list[RetrievedItem]: ...


def run_point_tool(
    request: ToolRequest,
    config: IndexingConfig | None = None,
    *,
    client: PointSearchClient | None = None,
) -> ToolResult:
    started_at = start_timer()
    config = config or IndexingConfig.from_env()
    client = client or QdrantRetrievalClient(config)
    point_filters, warnings = select_point_filters(request.filters)
    filter_spec = build_filter_spec(point_filters)
    if point_filters and is_point_lookup_filters(point_filters):
        items = client.payload_search(filter_spec, limit=clamp_limit(request.limit), source_tool="point")
    else:
        items: list[RetrievedItem] = []
    return build_result(
        tool_name="point",
        request=ToolRequest(
            query=request.query,
            filters=point_filters,
            limit=request.limit,
            profile=request.profile,
            trace_id=request.trace_id,
        ),
        config=config,
        filter_spec=filter_spec,
        items=items,
        started_at=started_at,
        qdrant_calls=client.calls,
        warnings=warnings,
    )


def is_point_lookup_filters(filters: dict[str, Any]) -> bool:
    if filters.get("chunk_id") or filters.get("point_identity_key"):
        return True
    return bool(filters.get("point_number") or filters.get("heading_path_text"))


def select_point_filters(filters: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    source = filters or {}
    warnings: list[str] = []
    if source.get("chunk_id"):
        return {"chunk_id": source["chunk_id"]}, warnings
    if source.get("point_identity_key"):
        return {"point_identity_key": source["point_identity_key"]}, warnings
    selected: dict[str, Any] = {}
    for key in ("doc_name", "point_number", "heading_path_text", "filename"):
        if source.get(key):
            selected[key] = doc_name_variants(source[key]) if key == "doc_name" else source[key]
    if not selected:
        warnings.append("point tool requires chunk_id, point_identity_key, or document point filters")
    elif not is_point_lookup_filters(selected):
        warnings.append(
            "point tool requires point_number, heading_path_text, chunk_id, or point_identity_key; "
            "document-only filters are not supported"
        )
    return selected, warnings


