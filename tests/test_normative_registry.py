from __future__ import annotations

import json
from pathlib import Path

import pytest

from mr_norm.data.normative_registry import (
    NormativeRegistry,
    RegistryEntry,
    RegistryMissingMode,
    audit_registry_coverage,
    collect_stems_from_structured_paths,
    filename_has_pue_marker,
    merge_registry_into_chunk_metadata,
    normalize_match_stem,
    resolve_metadata_with_registry,
)
from mr_norm.data.normative_registry_key import build_doc_key
from mr_norm.tools.schema import StructuredDocument


def test_build_doc_key_gost() -> None:
    assert build_doc_key("ГОСТ", "32144", "22.07.2013") == "gost_32144_22072013"


def test_normalize_match_stem() -> None:
    assert normalize_match_stem("ГОСТ 32144-2013 Качество.txt") == normalize_match_stem("ГОСТ 32144-2013 Качество")


def test_normalize_match_stem_preserves_dots_in_title() -> None:
    title = "ГОСТ 19431-2023 Энергетика и электрификация. Термины и определения"
    assert normalize_match_stem(f"{title}.txt") == normalize_match_stem(title)
    assert normalize_match_stem(f"{title}.rtf") == normalize_match_stem(title)


def test_registry_lookup_with_dots_in_filename(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    title = "Постановление от 02_03_2017 N 244 О совершенствовании требований к обеспечению надежности. Полномочия МЭ"
    reg_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "registry_key": "pprf_244_02032017",
                        "doc_type": "Постановление",
                        "authority": "Правительство Российской Федерации",
                        "doc_number": "244",
                        "adoption_date": "2017-03-02",
                        "adoption_date_display": "02.03.2017",
                        "title": title,
                        "short_title": "надежности",
                        "match_stems": [title],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from mr_norm.data.normative_registry import load_registry

    reg = load_registry(reg_path, reload=True)
    doc = StructuredDocument(source_file="", filename=f"{title}.txt")
    hit = reg.lookup_by_structured_doc(doc)
    assert hit is not None
    assert hit.registry_key == "pprf_244_02032017"


def test_lookup_by_stem(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "registry_key": "gost_32144_22072013",
                        "doc_type": "ГОСТ",
                        "authority": "Росстандарт",
                        "doc_number": "32144",
                        "adoption_date": "2013-07-22",
                        "adoption_date_display": "22.07.2013",
                        "title": "ГОСТ 32144-2013 — Качество электроэнергии",
                        "short_title": "Качество электроэнергии",
                        "match_stems": ["ГОСТ 32144-2013 Качество электроэнергии"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from mr_norm.data.normative_registry import load_registry

    reg = load_registry(reg_path, reload=True)
    doc = StructuredDocument(
        source_file="",
        filename="ГОСТ 32144-2013 Качество электроэнергии.txt",
    )
    hit = reg.lookup_by_structured_doc(doc)
    assert hit is not None
    assert hit.registry_key == "gost_32144_22072013"


def test_pue_filename_uses_canonical_registry_entry() -> None:
    doc = StructuredDocument(
        source_file="",
        filename="ПУЭ глава 1.1.txt",
    )
    assert filename_has_pue_marker(doc) is True
    meta = {"doc_title_full": "x", "doc_reg": "y"}
    pue = RegistryEntry(
        registry_key="pr_204_08072002",
        doc_type="Приказ",
        authority="Министерство энергетики Российской Федерации",
        doc_number="204",
        adoption_date="2002-07-08",
        adoption_date_display="08.07.2002",
        title="Правила устройства электроустановок",
        short_title="ПУЭ",
        match_stems=(),
        is_pue_canonical=True,
    )
    reg = NormativeRegistry(
        path=Path("x.json"),
        schema_version=1,
        documents=[pue],
    )
    out = resolve_metadata_with_registry(
        doc, meta, reg, missing_mode=RegistryMissingMode.FAIL
    )
    assert out["registry_key"] == "pr_204_08072002"
    assert out["short_title"] == "ПУЭ"
    assert out["doc_title_full"] == "Правила устройства электроустановок"
    assert "registry:" in out["metadata_source"]


def test_merge_registry_sets_required_fields() -> None:
    entry = RegistryEntry(
        registry_key="pr_548_12072018",
        doc_type="Приказ",
        authority="Министерство энергетики Российской Федерации",
        doc_number="548",
        adoption_date="2018-07-12",
        adoption_date_display="12.07.2018",
        title="Об утверждении требований",
        short_title="Требования к надежности",
        match_stems=("test",),
    )
    merged = merge_registry_into_chunk_metadata({}, entry, registry_source="test.json")
    assert merged["doc_title_full"] == entry.title
    assert merged["doc_reg"]
    assert merged["registry_key"] == "pr_548_12072018"
    assert merged["short_title"] == "Требования к надежности"


def test_registry_missing_fail(tmp_path: Path) -> None:
    from mr_norm.data.normative_registry import RegistryLookupError, load_registry

    reg_path = tmp_path / "empty.json"
    reg_path.write_text(
        json.dumps({"schema_version": 1, "documents": []}),
        encoding="utf-8",
    )
    reg = load_registry(reg_path, reload=True)
    doc = StructuredDocument(source_file="", filename="unknown_doc.txt")
    with pytest.raises(RegistryLookupError):
        resolve_metadata_with_registry(
            doc, {"doc_title_full": "a", "doc_reg": "b"},
            reg,
            missing_mode=RegistryMissingMode.FAIL,
        )


def test_audit_registry_coverage_pue_and_missing(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "registry_key": "gost_x",
                        "doc_type": "ГОСТ",
                        "authority": "Росстандарт",
                        "doc_number": "1",
                        "adoption_date": "2020-01-01",
                        "adoption_date_display": "01.01.2020",
                        "title": "Known",
                        "short_title": "Known",
                        "match_stems": ["Known doc"],
                    },
                    {
                        "registry_key": "pr_pue",
                        "doc_type": "Приказ",
                        "authority": "Минэнерго",
                        "doc_number": "204",
                        "adoption_date": "2002-07-08",
                        "adoption_date_display": "08.07.2002",
                        "title": "ПУЭ",
                        "short_title": "ПУЭ",
                        "match_stems": [],
                        "is_pue_canonical": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from mr_norm.data.normative_registry import load_registry

    reg = load_registry(reg_path, reload=True)
    stems = {
        normalize_match_stem("Known doc.txt"): "known.txt",
        normalize_match_stem("ПУЭ глава 1.txt"): "pue.txt",
        normalize_match_stem("Orphan file.txt"): "orphan.txt",
    }
    report = audit_registry_coverage(reg, stems)
    assert report.ok_for_chunking is False
    assert len(report.missing) == 1
    assert report.missing[0].stem == normalize_match_stem("Orphan file.txt")
    assert len(report.pue_canonical) == 1
    assert len(report.matched) == 1


def test_chunker_registry_metadata_without_preamble_text(tmp_path: Path) -> None:
    """Реквизиты только из JSON; преамбула в тексте не обязательна."""
    from mr_norm.config.paths import ProjectPaths
    from mr_norm.tools.chunker import ChunkBuilder
    from mr_norm.tools.rtf_processor import make_paragraph

    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "registry_key": "gost_19431_24102023",
                        "doc_type": "ГОСТ",
                        "authority": "Росстандарт",
                        "doc_number": "19431",
                        "adoption_date": "2023-10-24",
                        "adoption_date_display": "24.10.2023",
                        "title": "ГОСТ 19431-2023 — Термины",
                        "short_title": "Термины",
                        "match_stems": ["ГОСТ 19431-2023 Энергетика. Термины"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    doc = StructuredDocument(
        source_file="x.rtf",
        filename="ГОСТ 19431-2023 Энергетика. Термины.txt",
        paragraphs=[
            make_paragraph(0, "1. Общие положения", outline_level=1, style_name="Heading 1"),
            make_paragraph(1, "1.1. Термин определён."),
        ],
    )
    paths = ProjectPaths.from_root(tmp_path)
    builder = ChunkBuilder(paths, registry_path=reg_path, use_registry=True)
    chunks = builder.build_document_chunks(doc)
    assert chunks
    p = chunks[0]["payload"]
    assert p["registry_key"] == "gost_19431_24102023"
    assert p["doc_title_full"] == "ГОСТ 19431-2023 — Термины"
    assert str(p["metadata_source"]).startswith("registry:")
    assert p.get("point_number") == "1.1"


def test_backup_qdrant_chunks_json(tmp_path: Path) -> None:
    from mr_norm.tools.chunker import backup_qdrant_chunks_json

    chunks = tmp_path / "qdrant_chunks.json"
    chunks.write_text("[{\"chunk_id\": \"c1\"}]", encoding="utf-8")
    old_bak = tmp_path / "qdrant_chunks.bak"
    old_bak.write_text("stale", encoding="utf-8")

    out = backup_qdrant_chunks_json(chunks)
    assert out == old_bak
    assert not chunks.is_file()
    assert old_bak.read_text(encoding="utf-8") == "[{\"chunk_id\": \"c1\"}]"
    assert backup_qdrant_chunks_json(chunks) is None


def test_collect_stems_from_structured_paths(tmp_path: Path) -> None:
    sp = tmp_path / "doc.structured.json"
    sp.write_text(
        json.dumps({"filename": "ГОСТ 99-2020 Title.txt", "source_file": "x.rtf"}),
        encoding="utf-8",
    )
    stems = collect_stems_from_structured_paths([sp])
    assert normalize_match_stem("ГОСТ 99-2020 Title.txt") in stems
