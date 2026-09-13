from __future__ import annotations

from mr_norm.retrieval.filters import build_filter_spec
from mr_norm.skills.document_scope import resolve_document_scope
from mr_norm.skills.document_resolve import resolve_document_from_label


def test_include_scope_supports_multiple_documents() -> None:
    scope = resolve_document_scope("Ищи только в ПТЭ и ПТФЭС")

    assert scope.include_doc_ids == (
        "doc_eae0520bf60a818d",
        "doc_6b2e4ec78884fb5f",
    )
    assert scope.exclude_doc_ids == ()
    assert scope.to_filters() == {"doc_id": list(scope.include_doc_ids)}


def test_exclude_scope_does_not_match_short_alias_inside_long_alias() -> None:
    scope = resolve_document_scope("Ищи везде кроме ПТЭ потребителей")

    assert scope.include_doc_ids == ()
    assert scope.exclude_doc_ids == ("doc_691ad88b0e765823",)
    assert scope.to_filters() == {"exclude_doc_id": ["doc_691ad88b0e765823"]}


def test_exclude_scope_supports_numbered_acts() -> None:
    scope = resolve_document_scope("Найди сведения, исключая ГОСТ 32144 и приказ 690")

    assert scope.exclude_doc_ids == (
        "doc_fc7ef4265a0c65cc",
        "doc_aba6345406b47fba",
    )
    assert scope.include_doc_ids == ()


def test_filter_spec_keeps_include_and_exclude_constraints_separate() -> None:
    spec = build_filter_spec(
        {
            "doc_id": ["doc_a", "doc_b"],
            "exclude_doc_id": ["doc_c"],
        }
    )

    assert spec == {
        "must": [{"field": "doc_id", "kind": "keyword", "any": ["doc_a", "doc_b"]}],
        "must_not": [{"field": "doc_id", "kind": "keyword", "any": ["doc_c"]}],
    }


def test_numbered_document_matrix() -> None:
    cases = {
        "постановление № 86": "doc_7c4420ee6b8c3915",
        "постановлением 937": "doc_6b2e4ec78884fb5f",
        "приказом 903н": "doc_ad116a1f6b41adb1",
        "приказ 811": "doc_691ad88b0e765823",
        "приказ 1070": "doc_eae0520bf60a818d",
        "приказ от 29_11_2016 N 1256": "doc_e41ce9884ed5620b",
        "ГОСТ 32144": "doc_fc7ef4265a0c65cc",
        "ГОСТ Р 56302": "doc_51a757da569f076d",
        "ФЗ 35": "doc_888d9d2a434ba4e8",
        "35-ФЗ": "doc_888d9d2a434ba4e8",
        "закон 35-ФЗ": "doc_888d9d2a434ba4e8",
        "ФЗ 36": "doc_8cb5865b4ede5e5a",
        "36-ФЗ": "doc_8cb5865b4ede5e5a",
    }

    results = [resolve_document_from_label(label).doc_id == expected for label, expected in cases.items()]
    assert sum(results) / len(results) >= 0.9


def test_all_catalog_gost_number_shapes_are_scope_resolvable() -> None:
    cases = {
        "ГОСТ 19431-2023": "doc_6797067e3cb8d107",
        "ГОСТ 29322-2014": "doc_82ad184e1228f5ef",
        "ГОСТ 32144-2013": "doc_fc7ef4265a0c65cc",
        "ГОСТ 34184-2017": "doc_700512191fa36c65",
        "ГОСТ Р 1.4-2004": "doc_ee47fbd4b1175a52",
        "ГОСТ Р 1.5-2012": "doc_b193decd5fb3e9b5",
        "ГОСТ Р 56302-2014": "doc_51a757da569f076d",
        "ГОСТ Р 56303-2014": "doc_bac4a8bb87f6f64c",
        "ГОСТ Р 58651.1-2019": "doc_ef18285b05ae80fc",
        "ГОСТ Р 59948-2021": "doc_d71608402f199d28",
    }

    results = [
        resolve_document_scope(f"найди требования в {label}").include_doc_ids == (expected,)
        for label, expected in cases.items()
    ]
    assert all(results)


def test_scope_supports_verbose_act_number_forms() -> None:
    cases = {
        "по приказу от 29_11_2016 N 1256": "doc_e41ce9884ed5620b",
        "по постановлению правительства РФ 86": "doc_7c4420ee6b8c3915",
        "по ФЗ-35": "doc_888d9d2a434ba4e8",
    }

    for query, expected in cases.items():
        assert resolve_document_scope(query).include_doc_ids == (expected,)


def test_short_gost_terms_alias_locks_the_terms_standard() -> None:
    scope = resolve_document_scope("дай пункт 22 из ГОСТ по терминам")

    assert scope.include_doc_ids == ("doc_4745dec28ca589e1",)
