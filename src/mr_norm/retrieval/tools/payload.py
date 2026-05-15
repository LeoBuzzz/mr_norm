from __future__ import annotations

from typing import Protocol

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult, clamp_limit
from mr_norm.retrieval.filters import build_payload_filter_spec, expand_doc_name_filter_variants
from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient
from mr_norm.retrieval.tools.common import build_result, start_timer


class PayloadSearchClient(Protocol):
    calls: int

    def payload_search(self, filter_spec: dict, *, limit: int, source_tool: str) -> list[RetrievedItem]: ...


def run_payload_tool(
    request: ToolRequest,
    config: IndexingConfig | None = None,
    *,
    client: PayloadSearchClient | None = None,
    search_fields: list[str] | None = None,
) -> ToolResult:
    started_at = start_timer()
    config = config or IndexingConfig.from_env()
    client = client or QdrantRetrievalClient(config)
    expanded_filters = expand_doc_name_filter_variants(request.filters)
    filter_spec = build_payload_filter_spec(request.query, expanded_filters, search_fields=search_fields)
    warnings = []
    if not request.query.strip() and not filter_spec.get("must"):
        warnings.append("payload tool received no query or filters")
        items: list[RetrievedItem] = []
    else:
        items = client.payload_search(filter_spec, limit=clamp_limit(request.limit), source_tool="payload")
    return build_result(
        tool_name="payload",
        request=ToolRequest(
            query=request.query,
            filters=expanded_filters,
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
