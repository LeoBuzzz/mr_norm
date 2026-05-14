from __future__ import annotations

import pytest

from mr_norm.tools.chunker import extract_authority
from mr_norm.tools.chunker_document_menu import (
    internal_doc_kind_to_payload_label,
    load_chunker_document_menu,
    resolve_payload_authority,
)


def test_load_chunker_document_menu_has_expected_keys() -> None:
    menu = load_chunker_document_menu()
    assert menu.get("schema_version") == 1
    assert "internal_to_payload" in menu
    assert menu["internal_to_payload"]["gost"] == "ГОСТ"
    assert menu["internal_to_payload"]["pue"] == "приказ"


def test_resolve_payload_authority_order_without_org_is_empty() -> None:
    meta = {"authority": "", "doc_reg": "Приказ от 01.01.2020 N 1", "approving_act": ""}
    assert resolve_payload_authority(meta, "order", extract_authority) == ""


def test_resolve_payload_authority_pue_uses_menu_fallback() -> None:
    meta = {"authority": "", "doc_reg": "Приказ от 01.01.2020 N 1", "approving_act": ""}
    out = resolve_payload_authority(meta, "pue", extract_authority)
    assert out == "Министерство энергетики Российской Федерации"


def test_extract_authority_detects_minenergo_abbreviation() -> None:
    line = "Утверждены приказом Минэнерго России от 12 июля 2018 г. N 548"
    assert extract_authority(line) == "Министерство энергетики Российской Федерации"


def test_internal_doc_kind_to_payload_label() -> None:
    assert internal_doc_kind_to_payload_label("law") == "федеральный закон"
    assert internal_doc_kind_to_payload_label("decree") == "постановление"
    assert internal_doc_kind_to_payload_label("unknown") == "не классифицировано"


def test_internal_doc_kind_missing_key_raises() -> None:
    with pytest.raises(ValueError, match="отсутствует в internal_to_payload"):
        internal_doc_kind_to_payload_label("not_a_real_kind")
