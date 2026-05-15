from __future__ import annotations

import json
from types import SimpleNamespace

from mr_norm.config.indexing import DEFAULT_COLLECTION_NAME, IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.indexing.qdrant_adapter import (
    EXPECTED_PAYLOAD_INDEXES,
    PointRecord,
    QdrantIndexer,
    build_payload_index_schema_report,
    build_index_verify_report,
    build_qdrant_index,
    build_qdrant_payload,
    ensure_qdrant_payload_indexes,
    payload_schema_to_index_types,
    prepare_point_records,
    save_index_verify_report,
    stable_qdrant_point_id,
    verify_qdrant_payload_indexes,
)
from mr_norm.tools.chunker import ChunkBuilder
from tests.test_marking_payload_quality import make_structured_document


def make_chunks() -> list[dict]:
    return ChunkBuilder(paths=None).build_document_chunks(make_structured_document())  # type: ignore[arg-type]


def test_indexing_config_defaults_to_separate_mr_norm_collection(monkeypatch) -> None:
    monkeypatch.delenv("MR_NORM_QDRANT_COLLECTION", raising=False)
    monkeypatch.setenv("QDRANT_COLLECTION", "docs_collection_bge_m3")

    config = IndexingConfig.from_env()

    assert config.collection_name == DEFAULT_COLLECTION_NAME
    assert config.collection_name != "docs_collection_bge_m3"


def test_indexing_config_uses_mr_norm_specific_collection_env(monkeypatch) -> None:
    monkeypatch.setenv("MR_NORM_QDRANT_COLLECTION", "mr_norm_test_collection")
    monkeypatch.setenv("MR_NORM_QDRANT_TIMEOUT_SEC", "240")

    config = IndexingConfig.from_env()

    assert config.collection_name == "mr_norm_test_collection"
    assert config.qdrant_timeout_sec == 240


def test_qdrant_payload_preserves_text_and_stable_ids() -> None:
    chunk = make_chunks()[0]

    payload = build_qdrant_payload(chunk)

    assert payload["text"] == chunk["text"]
    assert payload["chunk_id"] == chunk["chunk_id"]
    assert payload["schema_version"] == chunk["schema_version"]
    assert payload["doc_id"] == chunk["payload"]["doc_id"]
    assert payload["point_identity_key"] == chunk["payload"]["point_identity_key"]


def test_stable_qdrant_point_id_is_deterministic_uuid() -> None:
    chunk_id = make_chunks()[0]["chunk_id"]

    first = stable_qdrant_point_id(chunk_id)
    second = stable_qdrant_point_id(chunk_id)

    assert first == second
    assert stable_qdrant_point_id(f"{chunk_id}_other") != first


def test_prepare_point_records_uses_named_vector_and_payload() -> None:
    chunks = make_chunks()[:2]
    config = IndexingConfig(vector_name="bge-m3")
    embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    records = prepare_point_records(chunks, embeddings, config)

    assert len(records) == 2
    assert records[0].vector == {"bge-m3": [0.1, 0.2, 0.3]}
    assert records[0].payload["text"] == chunks[0]["text"]
    assert records[0].payload["chunk_id"] == chunks[0]["chunk_id"]


def test_index_verify_report_passes_for_valid_chunks() -> None:
    chunks = make_chunks()

    report = build_index_verify_report(chunks, IndexingConfig(vector_size=3))

    assert report["passes"]
    assert report["blocking_defects"] == []
    assert report["required_payload_keys_coverage"] == 1.0
    assert report["vector_readiness"]["chunks_with_text"] == len(chunks)
    assert report["payload_lookup_readiness"]["payload_text_will_be_indexed"]
    assert report["payload_lookup_readiness"]["chunks_with_heading_path_text"] == len(chunks)
    assert report["payload_lookup_readiness"]["expected_payload_indexes"] == EXPECTED_PAYLOAD_INDEXES
    assert report["point_lookup_readiness"]["stable_qdrant_point_ids"]
    assert report["point_lookup_readiness"]["chunks_with_point_number"] > 0


def test_index_verify_report_blocks_missing_chunk_id() -> None:
    chunk = make_chunks()[0]
    bad = dict(chunk)
    bad.pop("chunk_id")

    report = build_index_verify_report([bad], IndexingConfig())

    assert not report["passes"]
    assert any(item["code"] == "qdrant_payload_errors" for item in report["blocking_defects"])


def test_save_index_verify_report_writes_json_and_markdown(tmp_path) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()
    paths.chunks_json.write_text(json.dumps(make_chunks(), ensure_ascii=False), encoding="utf-8")

    report = save_index_verify_report(paths, IndexingConfig(vector_size=3))

    assert report["passes"]
    assert (paths.reports_dir / "index_verify_").parent.is_dir()
    assert report["report_path"].endswith(".json")
    assert report["markdown_report_path"].endswith(".md")


class FakeEmbedder:
    def __init__(self, config: IndexingConfig):
        self.config = config

    @property
    def dimension(self) -> int:
        return 3

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 0.0, 1.0] for text in texts]


class FakeIndexer:
    created: list[dict] = []
    upserts: list[list[PointRecord]] = []
    ensured = 0
    ensured_expected_indexes: dict[str, str] = {}

    def __init__(self, config: IndexingConfig):
        self.config = config

    def create_collection(self, vector_size: int, rebuild: bool = False) -> None:
        self.__class__.created.append({"vector_size": vector_size, "rebuild": rebuild})

    def upsert_records(self, records: list[PointRecord]) -> None:
        self.__class__.upserts.append(records)

    def ensure_payload_indexes(self) -> None:
        self.__class__.ensured += 1
        self.__class__.ensured_expected_indexes = dict(EXPECTED_PAYLOAD_INDEXES)


def test_build_qdrant_index_uses_embedder_and_indexer_contract(tmp_path) -> None:
    FakeIndexer.created = []
    FakeIndexer.upserts = []
    FakeIndexer.ensured = 0
    FakeIndexer.ensured_expected_indexes = {}
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()
    paths.chunks_json.write_text(json.dumps(make_chunks(), ensure_ascii=False), encoding="utf-8")

    result = build_qdrant_index(
        paths,
        IndexingConfig(batch_size=1, upsert_batch_size=1),
        rebuild=True,
        show_progress=False,
        embedder_factory=FakeEmbedder,
        indexer_factory=FakeIndexer,
    )

    assert result["passes"]
    assert result["points_indexed"] == len(make_chunks())
    assert FakeIndexer.created == [{"vector_size": 3, "rebuild": True}]
    assert sum(len(batch) for batch in FakeIndexer.upserts) == len(make_chunks())
    assert FakeIndexer.ensured == 1
    assert FakeIndexer.ensured_expected_indexes == EXPECTED_PAYLOAD_INDEXES


def test_build_qdrant_index_prints_progress(tmp_path, capsys) -> None:
    FakeIndexer.created = []
    FakeIndexer.upserts = []
    FakeIndexer.ensured = 0
    FakeIndexer.ensured_expected_indexes = {}
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()
    paths.chunks_json.write_text(json.dumps(make_chunks(), ensure_ascii=False), encoding="utf-8")

    result = build_qdrant_index(
        paths,
        IndexingConfig(batch_size=1, upsert_batch_size=1),
        rebuild=False,
        show_progress=True,
        embedder_factory=FakeEmbedder,
        indexer_factory=FakeIndexer,
    )
    captured = capsys.readouterr()

    assert result["passes"]
    assert "[index-build] embedding/upsert" in captured.err
    assert "100.00%" in captured.err


def test_payload_schema_report_detects_missing_and_wrong_index_types() -> None:
    report = build_payload_index_schema_report(
        {
            "filename": {"data_type": "keyword"},
            "doc_name": {"data_type": "keyword"},
            "point_identity_key": {"data_type": "keyword"},
            "chunk_id": {"data_type": "keyword"},
            "point_number": {"data_type": "integer"},
            "text": {"data_type": "text"},
        },
        collection_name="test_collection",
    )

    assert not report["passes"]
    assert {"field_name": "heading_path_text", "expected_schema": "text"} in report["missing_indexes"]
    assert {
        "field_name": "point_number",
        "expected_schema": "keyword",
        "actual_schema": "integer",
    } in report["wrong_type_indexes"]


def test_payload_schema_report_passes_for_expected_indexes() -> None:
    report = build_payload_index_schema_report(
        {field_name: {"data_type": schema} for field_name, schema in EXPECTED_PAYLOAD_INDEXES.items()},
        collection_name="test_collection",
    )

    assert report["passes"]
    assert report["missing_indexes"] == []
    assert report["wrong_type_indexes"] == []
    assert report["actual_payload_indexes"] == EXPECTED_PAYLOAD_INDEXES


class FakeQdrantClient:
    def __init__(self) -> None:
        self.payload_schema = {
            "filename": SimpleNamespace(data_type="keyword"),
            "doc_name": SimpleNamespace(data_type="keyword"),
            "point_identity_key": SimpleNamespace(data_type="keyword"),
            "chunk_id": SimpleNamespace(data_type="keyword"),
            "text": SimpleNamespace(data_type="text"),
        }
        self.created_indexes: list[dict] = []

    def get_collection(self, collection_name: str) -> SimpleNamespace:
        assert collection_name == "test_collection"
        return SimpleNamespace(payload_schema=self.payload_schema)

    def create_payload_index(self, collection_name: str, field_name: str, field_schema: object) -> None:
        assert collection_name == "test_collection"
        schema = payload_schema_to_index_types({field_name: SimpleNamespace(data_type=field_schema)})[field_name]
        self.created_indexes.append({"field_name": field_name, "field_schema": schema})
        self.payload_schema[field_name] = SimpleNamespace(data_type=schema)


def test_qdrant_indexer_ensures_only_missing_payload_indexes() -> None:
    indexer = object.__new__(QdrantIndexer)
    indexer.config = IndexingConfig(collection_name="test_collection")
    indexer.client = FakeQdrantClient()

    result = indexer.ensure_payload_indexes()

    assert result["passes"]
    assert indexer.client.created_indexes == [
        {"field_name": "point_number", "field_schema": "keyword"},
        {"field_name": "heading_path_text", "field_schema": "text"},
    ]
    assert result["after"]["actual_payload_indexes"] == EXPECTED_PAYLOAD_INDEXES


class FakeSchemaIndexer:
    def __init__(self, config: IndexingConfig):
        self.config = config

    def payload_index_schema_report(self) -> dict:
        return build_payload_index_schema_report(
            {field_name: {"data_type": schema} for field_name, schema in EXPECTED_PAYLOAD_INDEXES.items()},
            collection_name=self.config.collection_name,
        )

    def ensure_payload_indexes(self) -> dict:
        return {"passes": True, "collection_name": self.config.collection_name, "created_indexes": []}


def test_schema_verify_and_ensure_helpers_accept_indexer_factory() -> None:
    config = IndexingConfig(collection_name="test_collection")

    verify = verify_qdrant_payload_indexes(config, indexer_factory=FakeSchemaIndexer)
    ensure = ensure_qdrant_payload_indexes(config, indexer_factory=FakeSchemaIndexer)

    assert verify["passes"]
    assert verify["collection_name"] == "test_collection"
    assert ensure == {"passes": True, "collection_name": "test_collection", "created_indexes": []}
