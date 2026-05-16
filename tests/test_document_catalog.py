from __future__ import annotations

import json
from pathlib import Path

from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCatalogEntry,
    extract_point_number_hint,
    find_catalog_candidates,
    load_catalog_snapshot,
)


def load_sample_catalog() -> DocumentCatalog:
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_find_catalog_candidates_resolves_pue_alias() -> None:
    catalog = load_sample_catalog()
    candidates = find_catalog_candidates("расскажи про ПУЭ по заземлению", catalog)

    assert candidates
    assert candidates[0].doc_name == "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"
    assert candidates[0].score >= 0.55


def test_find_catalog_candidates_resolves_personnel_rules_by_order_number() -> None:
    catalog = load_sample_catalog()
    candidates = find_catalog_candidates(
        "что в правилах работы с персоналом про оперативный персонал",
        catalog,
    )

    assert candidates
    assert "796" in candidates[0].doc_name or candidates[0].catalog_id == "doc_796"
    assert candidates[0].score >= 0.55


def test_find_catalog_candidates_explicit_unverified_doc_name() -> None:
    catalog = load_sample_catalog()
    candidates = find_catalog_candidates("вопрос", catalog, explicit_doc_name="Неизвестный документ")

    assert len(candidates) == 1
    assert candidates[0].doc_name == "Неизвестный документ"
    assert candidates[0].reasons[0] == "explicit_doc_name_unverified"


def test_extract_point_number_hint() -> None:
    assert extract_point_number_hint("требования пункта 1.7.1 по заземлению") == "1.7.1"


def test_load_catalog_snapshot_roundtrip(tmp_path: Path) -> None:
    catalog = DocumentCatalog(
        entries=[
            DocumentCatalogEntry(
                catalog_id="doc_1",
                doc_name="Тестовый документ",
                filename="test.txt",
                aliases=("тестовый документ",),
                order_numbers=("123",),
            )
        ],
        source_path="memory",
    )
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "mr_document_catalog_v1",
                "source_path": "memory",
                "entries": [entry.to_dict() for entry in catalog.entries],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_catalog_snapshot(path)

    assert loaded.entries[0].doc_name == "Тестовый документ"
    assert loaded.entries[0].order_numbers == ("123",)
