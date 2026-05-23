from __future__ import annotations

import json
from pathlib import Path

from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
from mr_norm.retrieval.document_knowledge import load_document_knowledge
from mr_norm.runtime.query_planner import _merge_candidates
from mr_norm.tools.chunker import resolve_document_id
from mr_norm.tools.document_knowledge_build import (
    assemble_knowledge_index,
    build_opening_record,
    extract_document_openings,
    group_chunks_by_doc_id,
)


def test_group_chunks_by_doc_id_skips_missing_id() -> None:
    grouped = group_chunks_by_doc_id(
        [
            {"payload": {"doc_id": "doc_a", "doc_name": "A"}, "text": "t1"},
            {"payload": {"doc_name": "B"}, "text": "t2"},
        ]
    )
    assert list(grouped) == ["doc_a"]


def test_build_opening_record_uses_doc_name_not_filename() -> None:
    record = build_opening_record(
        "doc_abc",
        [
            {
                "payload": {
                    "doc_id": "doc_abc",
                    "doc_name": "Федеральный закон N 35-ФЗ",
                    "registry_key": "fz_35",
                    "chunk_index": 0,
                    "part_index": 0,
                    "chunk_start": 0,
                },
                "text": "Статья 1. Общие положения.",
            }
        ],
    )
    assert record["doc_id"] == "doc_abc"
    assert record["registry_key"] == "fz_35"
    assert "35" in record["doc_name"] or "Федеральный" in record["doc_name"]
    assert record["kind"] == "law"


def test_extract_and_assemble_knowledge_index(tmp_path: Path) -> None:
    doc_id = resolve_document_id({"registry_key": "pr_548_12072018", "doc_name": "Правила ликвидации"})
    chunks_path = tmp_path / "chunks.json"
    chunks_path.write_text(
        json.dumps(
            [
                {
                    "chunk_id": "c1",
                    "text": "Раздел 1. Общие положения.",
                    "payload": {
                        "doc_id": doc_id,
                        "doc_name": "Правила ликвидации",
                        "registry_key": "pr_548_12072018",
                        "chunk_index": 0,
                        "part_index": 0,
                        "chunk_start": 0,
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    openings_path = tmp_path / "openings.json"
    extract_document_openings(chunks_path, output_path=openings_path)

    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(
        json.dumps(
            {
                "annotations": [
                    {"doc_id": doc_id, "annotation": "Порядок действий при нарушениях режима в энергосистемах."}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    index_path = tmp_path / "knowledge_index.json"
    bundle = assemble_knowledge_index(openings_path, annotations_path, output_path=index_path)

    assert bundle["schema_version"] == "mr_document_knowledge_v2"
    assert len(bundle["documents"]) == 1
    assert bundle["documents"][0]["doc_id"] == doc_id
    assert bundle["documents"][0]["registry_key"] == "pr_548_12072018"

    loaded = load_document_knowledge(index_path)
    assert loaded.by_doc_id()[doc_id].annotation.startswith("Порядок")


def test_merge_candidates_links_knowledge_doc_id_to_catalog() -> None:
    doc_id = "doc_shared_123"
    catalog = DocumentCatalog(
        entries=[
            DocumentCatalogEntry(
                catalog_id=doc_id,
                doc_id=doc_id,
                doc_name="Об электроэнергетике",
            )
        ]
    )
    from mr_norm.retrieval.document_knowledge import KnowledgeCandidate

    merged = _merge_candidates(
        catalog,
        [],
        [
            KnowledgeCandidate(
                doc_id=doc_id,
                doc_name="Об электроэнергетике",
                score=0.8,
                reasons=("annotation_match",),
                annotation="Закон об электроэнергетике.",
            )
        ],
    )
    assert merged[0]["catalog_id"] == doc_id
    assert not str(merged[0]["catalog_id"]).startswith("knowledge:")
