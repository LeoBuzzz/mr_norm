from __future__ import annotations

from typing import Protocol

from mr_norm.config.indexing import IndexingConfig
from mr_norm.indexing.qdrant_adapter import SentenceTransformerEmbedder
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest, ToolResult, clamp_limit
from mr_norm.retrieval.filters import build_filter_spec, expand_doc_name_filter_variants
from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient
from mr_norm.retrieval.tools.common import build_result, start_timer


class VectorSearchClient(Protocol):
    calls: int

    def vector_search(
        self,
        vector: list[float],
        filter_spec: dict,
        *,
        limit: int,
        source_tool: str,
    ) -> list[RetrievedItem]: ...


class QueryEmbedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


def run_vector_tool(
    request: ToolRequest,
    config: IndexingConfig | None = None,
    *,
    client: VectorSearchClient | None = None,
    embedder: QueryEmbedder | None = None,
) -> ToolResult:
    started_at = start_timer()
    config = config or IndexingConfig.from_env()
    client = client or QdrantRetrievalClient(config)
    embedder = embedder or SentenceTransformerEmbedder(config)
    expanded_filters = expand_doc_name_filter_variants(request.filters)
    filter_spec = build_filter_spec(expanded_filters)
    warnings = []
    query = request.query.strip()
    if not query:
        warnings.append("vector tool received an empty query")
        items: list[RetrievedItem] = []
    else:
        vector = embedder.encode([query])[0]
        items = client.vector_search(vector, filter_spec, limit=clamp_limit(request.limit), source_tool="vector")
    return build_result(
        tool_name="vector",
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
