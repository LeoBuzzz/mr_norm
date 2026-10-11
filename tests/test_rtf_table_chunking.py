from __future__ import annotations

import json
from pathlib import Path

import pytest

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.apps.main import run_chunk
from mr_norm.data.normative_registry import RegistryMissingMode
from mr_norm.eval.chunk_quality import build_quality_report
from mr_norm.indexing.qdrant_adapter import build_index_verify_report, build_qdrant_index
from mr_norm.rtf_tables.chunk import _dehyphenate, chunk_rtf, document_id
from mr_norm.rtf_tables.pipeline import RtfChunkBuilder
from mr_norm.rtf_tables.vectorize import attach_vectors
from mr_norm.tools.chunker import missing_payload_keys


DOCUMENTS = [
    {
        "id": "sample_doc",
        "registry_key": "sample_doc",
        "filename": "sample.rtf",
        "match_stems": ["sample"],
        "title": "Тестовый нормативный документ",
    }
]


def sample_source() -> str:
    return (
        r"{\rtf1\ansi\ansicpg1251\uc1"
        r"\pard 1. \uc0\'D1\'EC. \'F2\'E0\'E1\'EB\'E8\'F6\'F3 9.\par"
        r"\pard \'D2\'E0\'E1\'EB\'E8\'F6\'E0 9. \'D2\'EE\'EA\'E8 \par"
        r"\trowd\clbrdrt\clbrdrl\clbrdrr\clbrdrb\cellx9000"
        r"\pard\intbl \'D8\'E0\'EF\'EA\'E0 \cell\row"
        r"\trowd\clbrdrt\clbrdrl\clbrdrr\clbrdrb\cellx4500"
        r"\clbrdrt\clbrdrl\clbrdrr\clbrdrb\cellx9000"
        r"\pard\intbl \'CF\'EE\'EA\'E0\'E7\'E0\'F2\'E5\'EB\'FC \cell"
        r"\pard\intbl \'CA\'F2\'EE \cell\row"
        r"\trowd\clbrdrt\clbrdrl\clbrdrr\clbrdrb\cellx4500"
        r"\clbrdrt\clbrdrl\clbrdrr\clbrdrb\cellx9000"
        r"\pard\intbl \'C2\'FB\'EF\'EE\'EB\'ED\'E5\'ED\'E8\'E5 \cell"
        r"\pard\intbl \'D1\'E8\'F1\'F2\'E5\'EC\'ED\'FB\'E9 \'EE\'EF\'E5\'F0\'E0\'F2\'EE\'F0 \cell\row}"
    )


def test_chunk_rtf_builds_v2_points_and_table_rows() -> None:
    chunks = chunk_rtf(sample_source(), "sample.rtf", documents=DOCUMENTS)

    point = next(chunk for chunk in chunks if chunk["payload"]["kind"] == "point")
    row = next(chunk for chunk in chunks if "Системный оператор" in chunk["text"])
    assert all(chunk["schema_version"] == "mr_chunks_v2" for chunk in chunks)
    assert point["chunk_id"].startswith("sample_doc:point:1:")
    assert point["payload"]["point_ref"] == point["payload"]["point_number"] == "1"
    assert point["payload"]["table_refs"] == [{"table_id": "sample_doc:table:0", "label": "9"}]
    assert row["chunk_id"] == "sample_doc:table:0:row:2"
    assert row["payload"]["doc_name"] == "Тестовый нормативный документ"
    assert list(row["payload"]["cells"].values()) == ["Выполнение", "Системный оператор"]
    assert not missing_payload_keys(point)
    assert not missing_payload_keys(row)


def test_v2_quality_and_index_verification_do_not_require_v1_heading_fields() -> None:
    chunks = chunk_rtf(sample_source(), "sample.rtf", documents=DOCUMENTS)

    quality = build_quality_report(chunks)
    verify = build_index_verify_report(chunks, IndexingConfig(vector_size=3))

    assert quality["passes_quality_gate"]
    assert quality["structure"]["chunks_without_heading_path_text"] == 0
    assert verify["passes"]
    assert verify["chunk_schema_versions"] == {"mr_chunks_v2": len(chunks)}


def test_vectorizer_uses_injected_encoder_and_checks_cardinality() -> None:
    chunks = chunk_rtf(sample_source(), "sample.rtf", documents=DOCUMENTS)
    attach_vectors(chunks, lambda texts: [[float(len(text)), 1.0] for text in texts])
    assert chunks[0]["vector"] == [float(len(chunks[0]["text"])), 1.0]

    with pytest.raises(ValueError, match="encode returned"):
        attach_vectors(chunks, lambda _texts: [])


def test_rtf_chunk_builder_reads_raw_rtf_and_writes_qdrant_json(tmp_path: Path) -> None:
    root = tmp_path / "project"
    input_dir = root / "input" / "All_raw_docks"
    input_dir.mkdir(parents=True)
    (root / "planning").mkdir()
    source_path = input_dir / "sample.rtf"
    source_path.write_text(sample_source(), encoding="latin-1")
    (input_dir / "~$sample.rtf").write_text("not an rtf", encoding="latin-1")
    registry_path = root / "registry.json"
    registry_path.write_text(json.dumps({"documents": DOCUMENTS}, ensure_ascii=False), encoding="utf-8")
    paths = ProjectPaths.from_root(root)

    chunks = RtfChunkBuilder(paths, registry_path=registry_path).build_all()

    written = json.loads(paths.chunks_json.read_text(encoding="utf-8"))
    assert written == chunks
    assert any(chunk["payload"]["kind"] == "table_row" for chunk in chunks)


def test_main_chunk_stage_uses_raw_rtf_pipeline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    input_dir = root / "input" / "All_raw_docks"
    input_dir.mkdir(parents=True)
    (root / "planning").mkdir()
    (input_dir / "sample.rtf").write_text(sample_source(), encoding="latin-1")
    registry_path = root / "registry.json"
    registry_path.write_text(
        json.dumps({"schema_version": 1, "documents": DOCUMENTS}, ensure_ascii=False),
        encoding="utf-8",
    )
    paths = ProjectPaths.from_root(root)

    result = run_chunk(
        paths,
        limit=1,
        registry_path=registry_path,
        registry_missing=RegistryMissingMode.FAIL,
    )

    assert result["chunk_schema_version"] == "mr_chunks_v2"
    assert result["source_format"] == "raw_rtf"
    assert result["files_processed"] == 1
    assert result["quality_gate_passes"]


def test_pue_chapter_gets_the_registry_compatible_document_id() -> None:
    filename = "Правила устройства электроустановок (ПУЭ). Глава 1.7 Заземление.rtf"

    assert document_id(filename, documents=[]) == "pue_1_7"


def test_v2_index_build_requires_collection_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    (root / "planning").mkdir()
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()
    paths.chunks_json.write_text(
        json.dumps(chunk_rtf(sample_source(), "sample.rtf", documents=DOCUMENTS), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires rebuild=True"):
        build_qdrant_index(paths, IndexingConfig(), rebuild=False, show_progress=False)


def test_line_break_hyphen_rules_are_preserved() -> None:
    assert _dehyphenate("Предостав- ляют") == "Предоставляют"
    assert _dehyphenate("электро- энергетики") == "электроэнергетики"
    assert _dehyphenate("информационно- телекоммуникационные") == "информационно-телекоммуникационные"
