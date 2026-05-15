from __future__ import annotations

import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.chunker import load_chunks, missing_payload_keys
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text


@dataclass(frozen=True)
class PointRecord:
    id: str
    vector: dict[str, list[float]]
    payload: dict[str, Any]


EXPECTED_PAYLOAD_INDEXES = {
    "filename": "keyword",
    "doc_name": "keyword",
    "point_identity_key": "keyword",
    "chunk_id": "keyword",
    "point_number": "keyword",
    "text": "text",
    "heading_path_text": "text",
}


def normalize_payload_schema_type(schema_info: Any) -> str:
    if isinstance(schema_info, dict):
        raw = schema_info.get("data_type") or schema_info.get("type") or schema_info.get("schema") or ""
    else:
        raw = getattr(schema_info, "data_type", schema_info)
    if hasattr(raw, "value"):
        raw = raw.value
    text = str(raw or "").strip().lower()
    if "keyword" in text:
        return "keyword"
    if "text" in text:
        return "text"
    return text


def payload_schema_to_index_types(payload_schema: dict[str, Any] | None) -> dict[str, str]:
    return {
        field_name: normalize_payload_schema_type(schema_info)
        for field_name, schema_info in (payload_schema or {}).items()
    }


def build_payload_index_schema_report(
    payload_schema: dict[str, Any] | None,
    *,
    collection_name: str,
    expected_indexes: dict[str, str] | None = None,
) -> dict[str, Any]:
    expected = dict(expected_indexes or EXPECTED_PAYLOAD_INDEXES)
    actual = payload_schema_to_index_types(payload_schema)
    missing_indexes = [
        {"field_name": field_name, "expected_schema": expected_schema}
        for field_name, expected_schema in expected.items()
        if field_name not in actual
    ]
    wrong_type_indexes = [
        {
            "field_name": field_name,
            "expected_schema": expected_schema,
            "actual_schema": actual[field_name],
        }
        for field_name, expected_schema in expected.items()
        if field_name in actual and actual[field_name] != expected_schema
    ]
    return {
        "schema_version": "mr_payload_index_schema_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection_name": collection_name,
        "expected_payload_indexes": expected,
        "actual_payload_indexes": actual,
        "missing_indexes": missing_indexes,
        "wrong_type_indexes": wrong_type_indexes,
        "passes": not missing_indexes and not wrong_type_indexes,
    }


def stable_qdrant_point_id(chunk_id: str) -> str:
    if not chunk_id:
        raise ValueError("chunk_id is required for stable Qdrant point id")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mr_norm:qdrant_chunk:{chunk_id}"))


def build_qdrant_payload(chunk: dict[str, Any]) -> dict[str, Any]:
    payload = dict(chunk.get("payload") or {})
    text = str(chunk.get("text") or "")
    chunk_id = str(chunk.get("chunk_id") or payload.get("chunk_id") or "")
    schema_version = str(chunk.get("schema_version") or payload.get("schema_version") or "")
    if not chunk_id:
        raise ValueError("chunk_id is required before indexing")
    payload["text"] = text
    payload["chunk_id"] = chunk_id
    payload["schema_version"] = schema_version
    return payload


def prepare_point_records(
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
    config: IndexingConfig,
) -> list[PointRecord]:
    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks/embeddings length mismatch: {len(chunks)} != {len(embeddings)}")
    records: list[PointRecord] = []
    for chunk, embedding in zip(chunks, embeddings):
        chunk_id = str(chunk.get("chunk_id") or "")
        records.append(
            PointRecord(
                id=stable_qdrant_point_id(chunk_id),
                vector={config.vector_name: [float(value) for value in embedding]},
                payload=build_qdrant_payload(chunk),
            )
        )
    return records


def chunks_to_texts(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(chunk.get("text") or "") for chunk in chunks]


def chunk_batches(items: list[Any], batch_size: int) -> Iterable[list[Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def print_index_progress(label: str, current: int, total: int, *, width: int = 32) -> None:
    total = max(total, 1)
    current = min(max(current, 0), total)
    ratio = current / total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    percent = ratio * 100
    end = "\n" if current >= total else "\r"
    print(f"[index-build] {label} [{bar}] {current}/{total} {percent:6.2f}%", end=end, file=sys.stderr, flush=True)


def build_index_verify_report(
    chunks: list[dict[str, Any]],
    config: IndexingConfig,
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    payloads = [chunk.get("payload") or {} for chunk in chunks]
    qdrant_payload_errors = []
    point_ids: list[str] = []
    vector_text_empty = 0
    for index, chunk in enumerate(chunks):
        text = str(chunk.get("text") or "")
        if not text.strip():
            vector_text_empty += 1
        try:
            payload = build_qdrant_payload(chunk)
            point_ids.append(stable_qdrant_point_id(payload["chunk_id"]))
        except ValueError as exc:
            qdrant_payload_errors.append({"index": index, "error": str(exc)})
    missing_key_chunks = sum(1 for chunk in chunks if missing_payload_keys(chunk))
    duplicate_point_ids = len(point_ids) - len(set(point_ids))
    chunks_with_doc_name = sum(1 for payload in payloads if payload.get("doc_name"))
    chunks_with_filename = sum(1 for payload in payloads if payload.get("filename"))
    chunks_with_point_identity = sum(1 for payload in payloads if payload.get("point_identity_key"))
    chunks_with_point_number = sum(1 for payload in payloads if payload.get("point_number"))
    chunks_with_heading_path_text = sum(1 for payload in payloads if payload.get("heading_path_text"))
    chunks_with_chunk_id = sum(1 for chunk in chunks if chunk.get("chunk_id"))
    blocking_defects = []
    if not chunks:
        blocking_defects.append({"code": "no_chunks", "stage": "indexing"})
    if missing_key_chunks:
        blocking_defects.append({"code": "missing_required_payload_keys", "stage": "payload", "count": missing_key_chunks})
    if qdrant_payload_errors:
        blocking_defects.append({"code": "qdrant_payload_errors", "stage": "payload", "count": len(qdrant_payload_errors)})
    if vector_text_empty:
        blocking_defects.append({"code": "empty_vector_text", "stage": "embedding", "count": vector_text_empty})
    if duplicate_point_ids:
        blocking_defects.append({"code": "duplicate_qdrant_point_ids", "stage": "qdrant", "count": duplicate_point_ids})
    return {
        "schema_version": "mr_index_verify_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(source_path) if source_path else "",
        "config": asdict(config),
        "chunks_total": len(chunks),
        "required_payload_keys_coverage": 1.0 if not chunks else (len(chunks) - missing_key_chunks) / len(chunks),
        "qdrant_payload_errors": qdrant_payload_errors[:50],
        "duplicate_qdrant_point_ids": duplicate_point_ids,
        "vector_readiness": {
            "chunks_with_text": len(chunks) - vector_text_empty,
            "empty_text_chunks": vector_text_empty,
            "embedding_model_name": config.embedding_model_name,
            "vector_name": config.vector_name,
            "vector_size": config.vector_size,
        },
        "payload_lookup_readiness": {
            "chunks_with_filename": chunks_with_filename,
            "chunks_with_doc_name": chunks_with_doc_name,
            "chunks_with_heading_path_text": chunks_with_heading_path_text,
            "payload_text_will_be_indexed": True,
            "expected_payload_indexes": dict(EXPECTED_PAYLOAD_INDEXES),
        },
        "point_lookup_readiness": {
            "chunks_with_chunk_id": chunks_with_chunk_id,
            "chunks_with_point_identity_key": chunks_with_point_identity,
            "chunks_with_point_number": chunks_with_point_number,
            "stable_qdrant_point_ids": duplicate_point_ids == 0 and not qdrant_payload_errors,
        },
        "blocking_defects": blocking_defects,
        "passes": not blocking_defects,
    }


def save_index_verify_report(paths: ProjectPaths, config: IndexingConfig) -> dict[str, Any]:
    chunks = load_chunks(paths.chunks_json)
    report = build_index_verify_report(chunks, config, source_path=paths.chunks_json)
    paths.ensure_output_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = paths.reports_dir / f"index_verify_{timestamp}.json"
    markdown_path = paths.reports_dir / f"index_verify_{timestamp}.md"
    atomic_write_json(report_path, report)
    atomic_write_text(markdown_path, render_index_verify_markdown(report))
    report["report_path"] = str(report_path)
    report["markdown_report_path"] = str(markdown_path)
    return report


def render_index_verify_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Index Verify Report",
        "",
        f"- Status: {'PASS' if report.get('passes') else 'FAIL'}",
        f"- Chunks: {report.get('chunks_total')}",
        f"- Collection: `{report.get('config', {}).get('collection_name')}`",
        f"- Vector: `{report.get('config', {}).get('vector_name')}`",
        f"- Model: `{report.get('config', {}).get('embedding_model_name')}`",
        f"- Required payload coverage: {report.get('required_payload_keys_coverage', 0):.4f}",
        "",
        "## Blocking Defects",
        "",
    ]
    defects = report.get("blocking_defects") or []
    if defects:
        lines.extend(f"- `{item['code']}` ({item['stage']}): {item.get('count', '')}" for item in defects)
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


class SentenceTransformerEmbedder:
    def __init__(self, config: IndexingConfig):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is required for index-build") from exc
        kwargs = {"device": config.embedding_device} if config.embedding_device else {}
        self.config = config
        self.model = SentenceTransformer(config.embedding_model_name, **kwargs)

    @property
    def dimension(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> list[list[float]]:
        prepared = [f"passage: {text}" if self.config.use_query_passage_prefix else text for text in texts]
        embeddings = self.model.encode(
            prepared,
            convert_to_numpy=True,
            normalize_embeddings=self.config.normalize and self.config.use_query_passage_prefix,
            show_progress_bar=False,
            batch_size=self.config.batch_size,
        )
        if self.config.normalize and not self.config.use_query_passage_prefix:
            import numpy as np

            arr = np.asarray(embeddings)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            embeddings = arr / (norms + 1e-8)
        return embeddings.tolist()


class QdrantIndexer:
    def __init__(self, config: IndexingConfig):
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is required for index-build") from exc
        self.config = config
        self.client = QdrantClient(url=config.qdrant_url, timeout=config.qdrant_timeout_sec)

    def create_collection(self, vector_size: int, rebuild: bool = False) -> None:
        from qdrant_client import models

        collections = [col.name for col in self.client.get_collections().collections]
        if rebuild and self.config.collection_name in collections:
            self.client.delete_collection(self.config.collection_name)
            collections.remove(self.config.collection_name)
        if self.config.collection_name in collections:
            return
        self.client.create_collection(
            collection_name=self.config.collection_name,
            vectors_config={
                self.config.vector_name: models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                )
            },
        )

    def upsert_records(self, records: list[PointRecord]) -> None:
        from qdrant_client import models

        points = [
            models.PointStruct(id=record.id, vector=record.vector, payload=record.payload)
            for record in records
        ]
        self.client.upsert(collection_name=self.config.collection_name, points=points)

    def payload_schema(self) -> dict[str, Any]:
        collection = self.client.get_collection(self.config.collection_name)
        return dict(collection.payload_schema or {})

    def payload_index_schema_report(self) -> dict[str, Any]:
        return build_payload_index_schema_report(
            self.payload_schema(),
            collection_name=self.config.collection_name,
        )

    def ensure_payload_indexes(self) -> dict[str, Any]:
        from qdrant_client import models

        schemas = {
            "keyword": models.PayloadSchemaType.KEYWORD,
            "text": models.PayloadSchemaType.TEXT,
        }
        before = self.payload_index_schema_report()
        for item in before["missing_indexes"]:
            field_name = item["field_name"]
            schema_name = item["expected_schema"]
            self.client.create_payload_index(
                collection_name=self.config.collection_name,
                field_name=field_name,
                field_schema=schemas[schema_name],
            )
        after = self.payload_index_schema_report()
        return {
            "schema_version": "mr_payload_index_ensure_v1",
            "collection_name": self.config.collection_name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "created_indexes": before["missing_indexes"],
            "wrong_type_indexes": before["wrong_type_indexes"],
            "before": before,
            "after": after,
            "passes": after["passes"],
        }


def verify_qdrant_payload_indexes(
    config: IndexingConfig,
    *,
    indexer_factory: Callable[[IndexingConfig], QdrantIndexer] = QdrantIndexer,
) -> dict[str, Any]:
    return indexer_factory(config).payload_index_schema_report()


def ensure_qdrant_payload_indexes(
    config: IndexingConfig,
    *,
    indexer_factory: Callable[[IndexingConfig], QdrantIndexer] = QdrantIndexer,
) -> dict[str, Any]:
    return indexer_factory(config).ensure_payload_indexes()


def build_qdrant_index(
    paths: ProjectPaths,
    config: IndexingConfig,
    *,
    rebuild: bool = False,
    limit: int | None = None,
    show_progress: bool = True,
    embedder_factory: Callable[[IndexingConfig], SentenceTransformerEmbedder] = SentenceTransformerEmbedder,
    indexer_factory: Callable[[IndexingConfig], QdrantIndexer] = QdrantIndexer,
) -> dict[str, Any]:
    start = time.perf_counter()
    if show_progress:
        print("[index-build] loading chunks", file=sys.stderr, flush=True)
    chunks = load_chunks(paths.chunks_json)
    if limit is not None:
        chunks = chunks[:limit]
    if show_progress:
        print(f"[index-build] verifying {len(chunks)} chunks", file=sys.stderr, flush=True)
    verify = build_index_verify_report(chunks, config, source_path=paths.chunks_json)
    if not verify["passes"]:
        return {
            "schema_version": "mr_index_build_v1",
            "passes": False,
            "stage": "verify",
            "verify": verify,
            "elapsed_sec": round(time.perf_counter() - start, 3),
        }
    if show_progress:
        print(f"[index-build] loading embedding model: {config.embedding_model_name}", file=sys.stderr, flush=True)
    embedder = embedder_factory(config)
    vector_size = config.vector_size or embedder.dimension
    if show_progress:
        print(
            f"[index-build] creating collection: {config.collection_name} "
            f"(vector={config.vector_name}, size={vector_size}, rebuild={rebuild})",
            file=sys.stderr,
            flush=True,
        )
    indexer = indexer_factory(config)
    indexer.create_collection(vector_size=vector_size, rebuild=rebuild)
    indexed = 0
    total_batches = max(1, (len(chunks) + config.upsert_batch_size - 1) // config.upsert_batch_size)
    for batch_number, batch in enumerate(chunk_batches(chunks, config.upsert_batch_size), start=1):
        embeddings = embedder.encode(chunks_to_texts(batch))
        records = prepare_point_records(batch, embeddings, config)
        indexer.upsert_records(records)
        indexed += len(records)
        if show_progress:
            print_index_progress("embedding/upsert", batch_number, total_batches)
    if show_progress:
        print("[index-build] creating payload indexes", file=sys.stderr, flush=True)
    indexer.ensure_payload_indexes()
    return {
        "schema_version": "mr_index_build_v1",
        "passes": True,
        "collection_name": config.collection_name,
        "vector_name": config.vector_name,
        "embedding_model_name": config.embedding_model_name,
        "vector_size": vector_size,
        "points_indexed": indexed,
        "rebuild": rebuild,
        "elapsed_sec": round(time.perf_counter() - start, 3),
    }


def save_index_build_report(paths: ProjectPaths, result: dict[str, Any]) -> dict[str, Any]:
    paths.ensure_output_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = paths.reports_dir / f"index_build_{timestamp}.json"
    atomic_write_json(report_path, result)
    result["report_path"] = str(report_path)
    return result
