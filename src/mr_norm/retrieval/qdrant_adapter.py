from __future__ import annotations

from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem


class QdrantRetrievalClient:
    def __init__(self, config: IndexingConfig):
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is required for retrieval tools") from exc
        self.config = config
        self.client = QdrantClient(url=config.qdrant_url, timeout=config.qdrant_timeout_sec)
        self.calls = 0

    def vector_search(
        self,
        vector: list[float],
        filter_spec: dict[str, Any],
        *,
        limit: int,
        source_tool: str,
    ) -> list[RetrievedItem]:
        self.calls += 1
        qdrant_filter = filter_spec_to_qdrant_filter(filter_spec)
        if hasattr(self.client, "search"):
            points = self.client.search(
                collection_name=self.config.collection_name,
                query_vector=(self.config.vector_name, vector),
                query_filter=qdrant_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        else:
            response = self.client.query_points(
                collection_name=self.config.collection_name,
                query=vector,
                using=self.config.vector_name,
                query_filter=qdrant_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        return [point_to_item(point, source_tool=source_tool) for point in points]

    def payload_search(self, filter_spec: dict[str, Any], *, limit: int, source_tool: str) -> list[RetrievedItem]:
        self.calls += 1
        points, _offset = self.client.scroll(
            collection_name=self.config.collection_name,
            scroll_filter=filter_spec_to_qdrant_filter(filter_spec),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [point_to_item(point, source_tool=source_tool) for point in points]


def point_to_item(point: Any, *, source_tool: str) -> RetrievedItem:
    payload = dict(getattr(point, "payload", None) or {})
    score = getattr(point, "score", None)
    return RetrievedItem(
        chunk_id=str(payload.get("chunk_id") or ""),
        doc_id=str(payload.get("doc_id") or ""),
        point_id=str(payload.get("point_id") or ""),
        filename=str(payload.get("filename") or ""),
        doc_name=str(payload.get("doc_name") or ""),
        heading_path_text=str(payload.get("heading_path_text") or ""),
        point_number=str(payload.get("point_number") or ""),
        text=str(payload.get("text") or ""),
        score=float(score) if score is not None else None,
        source_tool=source_tool,
        point_identity_key=str(payload.get("point_identity_key") or ""),
        qdrant_point_id=str(getattr(point, "id", "") or ""),
        matched={},
    )


def filter_spec_to_qdrant_filter(spec: dict[str, Any]):
    from qdrant_client import models

    must = [_condition(item, models) for item in spec.get("must") or []]
    should = [_condition(item, models) for item in spec.get("should") or []]
    return models.Filter(must=must or None, should=should or None)


def _condition(item: dict[str, Any], models: Any):
    field = item["field"]
    kind = item.get("kind")
    if "any" in item:
        return models.FieldCondition(key=field, match=models.MatchAny(any=item["any"]))
    if kind == "text":
        return models.FieldCondition(key=field, match=models.MatchText(text=str(item["value"])))
    return models.FieldCondition(key=field, match=models.MatchValue(value=item["value"]))
