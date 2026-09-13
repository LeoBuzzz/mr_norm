from __future__ import annotations

import re
from typing import Any, Protocol

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult, clamp_limit
from mr_norm.retrieval.filters import build_filter_spec, build_payload_filter_spec, doc_name_variants
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
        point_label = str(point_filters.get("point_number") or request.query or "").strip()
        if not items and point_label:
            scope_filters = {
                key: value
                for key, value in point_filters.items()
                if key in {"doc_id", "doc_name", "heading_path_text"} and value
            }
            if scope_filters:
                fallback_spec = build_payload_filter_spec(
                    point_label,
                    scope_filters,
                    search_fields=["text"],
                )
                fallback_items = client.payload_search(
                    fallback_spec,
                    # Text matching on a number is broad. Fetch enough
                    # candidates to select the article whose heading really
                    # starts with the requested point instead of accepting a
                    # random chunk that merely mentions the number.
                    limit=max(clamp_limit(request.limit), 50),
                    source_tool="point",
                )
                exact_text_items = [
                    item for item in fallback_items if _text_starts_with_point(item.text, point_label)
                ]
                items = exact_text_items
                if items:
                    warnings.append(
                        "point tool used text fallback because exact point_number metadata did not match"
                    )
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
    for key in ("doc_id", "doc_name", "point_number", "heading_path_text"):
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


def _text_starts_with_point(text: str, expected: str) -> bool:
    """Return whether a text chunk begins with the requested numbered article."""
    value = str(expected or "").strip().replace("_", ".")
    if not value:
        return False
    escaped = re.escape(value)
    candidate_text = str(text or "").replace("_", ".")
    return bool(re.match(rf"^\s*{escaped}(?:\s|[.:)])", candidate_text))
