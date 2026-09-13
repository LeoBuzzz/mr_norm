from __future__ import annotations

from mr_norm.retrieval.document_catalog import load_default_document_catalog
from mr_norm.skills.document_resolve import resolve_document, resolve_document_from_label


def test_canonical_act_number_resolution() -> None:
    catalog = load_default_document_catalog()

    cases = {
        "937 постановление": "doc_6b2e4ec78884fb5f",
        "ФЗ 35": "doc_888d9d2a434ba4e8",
        "приказ 690": "doc_aba6345406b47fba",
        "постановление правительства 86": "doc_7c4420ee6b8c3915",
        "приказ 903н": "doc_ad116a1f6b41adb1",
        "приказ 811": "doc_691ad88b0e765823",
        "приказ 1070": "doc_eae0520bf60a818d",
        "приказ 1256": "doc_e41ce9884ed5620b",
    }

    for label, expected_doc_id in cases.items():
        result = resolve_document_from_label(label, catalog=catalog)
        assert result.found, label
        assert result.doc_id == expected_doc_id, label
        assert result.mention_kind == "canonical", label


def test_canonical_short_aliases_beat_fuzzy_candidates() -> None:
    catalog = load_default_document_catalog()

    cases = {
        "правила ОТ": "doc_ad116a1f6b41adb1",
        "ПТЭ": "doc_eae0520bf60a818d",
        "ПТЭ потребителей": "doc_691ad88b0e765823",
        "методуказания по устойчивости": "doc_e6915ad2d756375e",
        "методуказания по расчету надежности": "doc_e41ce9884ed5620b",
        "правила вывода в ремонт": "doc_7c4420ee6b8c3915",
    }

    for label, expected_doc_id in cases.items():
        result = resolve_document(label, catalog=catalog)
        assert result.found, label
        assert result.doc_id == expected_doc_id, label


def test_catalog_gap_does_not_fallback_to_wrong_document() -> None:
    result = resolve_document_from_label("методуказания по проектированию")

    assert not result.found
    assert result.status == "not_found"
    assert "document_resolve:catalog_gap" in result.warnings


def test_aliases_resolve_inside_questions_and_inflected_phrases() -> None:
    cases = {
        "что говорит приказом 903н о требованиях": "doc_ad116a1f6b41adb1",
        "требования закона об электроэнергетике": "doc_888d9d2a434ba4e8",
        "по постановлению правительства 86": "doc_7c4420ee6b8c3915",
        "в правилах вывода объектов в ремонт": "doc_7c4420ee6b8c3915",
        "по ПТЭ потребителей": "doc_691ad88b0e765823",
        "согласно методическими указаниями по устойчивости": "doc_e6915ad2d756375e",
        "вопрос по приказу о правилах переключений": "doc_d031d10cc8150018",
        "что устанавливает закон об электроэнергетике": "doc_888d9d2a434ba4e8",
        "требования постановления 937": "doc_6b2e4ec78884fb5f",
        "по правилам техобслуживания и ремонта": "doc_da35fc1a267743f9",
    }

    passed = sum(resolve_document_from_label(query).doc_id == expected for query, expected in cases.items())
    assert passed / len(cases) >= 0.9
