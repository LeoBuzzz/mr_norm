from __future__ import annotations

import json
from pathlib import Path

from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCatalogEntry,
    extract_document_label_hint,
    extract_point_number_hint,
    extract_point_number_hints,
    find_catalog_candidates,
    is_generic_tech_reg_doc_name,
    load_catalog_snapshot,
    query_suggests_energy_sector,
    resolve_by_partial_order_hint,
)


def load_sample_catalog() -> DocumentCatalog:
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_find_catalog_candidates_resolves_pue_alias() -> None:
    catalog = load_sample_catalog()
    candidates = find_catalog_candidates(
        "расскажи про ПУЭ по заземлению", catalog, enable_pue_aliases=True
    )

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
    assert extract_point_number_hint("абзаце пункта 34 Основных положений") == "34"
    assert (
        extract_point_number_hint(
            "подпунктом 4.14_1 Положения о Минэнерго (Постановление от 28.05.2008 № 400)"
        )
        == "4.14_1"
    )
    assert extract_point_number_hint("от 28.05.2008 № 400") == ""


def test_extract_point_number_hints_multi() -> None:
    assert extract_point_number_hints("Дай текст пунктов 3 и 99 ПТФ") == ["3", "99"]
    assert extract_point_number_hints("п. 3, 99 правил") == ["3", "99"]
    assert extract_point_number_hints("пункт 24 правил работы с персоналом") == ["24"]


def test_extract_document_label_hint() -> None:
    assert extract_document_label_hint("Дай текст пунктов 3 и 99 ПТФ") == "ПТФ"
    assert extract_document_label_hint("что в ПУЭ про заземление") == "ПУЭ"


def test_resolve_by_partial_order_hint_minenergo_number() -> None:
    catalog = load_sample_catalog()
    hit = resolve_by_partial_order_hint("что в приказе минэнерго 796 про персонал", catalog)
    assert hit is not None
    names, catalog_id, confidence, ambiguous, reasons = hit
    assert names == [
        "Об утверждении Правил работы с персоналом в организациях электроэнергетики Российской Федерации"
    ]
    assert catalog_id == "doc_796"
    assert confidence >= 0.85
    assert ambiguous is False
    assert reasons == ["partial_order:796"]


def test_query_suggests_energy_sector_and_generic_fz() -> None:
    assert query_suggests_energy_sector("требования к лэп 330 кв")
    assert is_generic_tech_reg_doc_name("О техническом регулировании")
    assert not is_generic_tech_reg_doc_name("Об электроэнергетике")


def test_load_catalog_snapshot_roundtrip(tmp_path: Path) -> None:
    catalog = DocumentCatalog(
        entries=[
            DocumentCatalogEntry(
                catalog_id="doc_1",
                doc_name="Тестовый документ",
                doc_id="doc_1",
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
