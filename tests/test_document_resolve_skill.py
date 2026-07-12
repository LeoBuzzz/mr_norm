from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.document_catalog import load_catalog_snapshot
from mr_norm.skills.document_resolve import (
    DocumentResolveResult,
    resolve_document,
    resolve_document_by_name,
    resolve_document_from_label,
)
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
from tests.test_skills_norm_lookup import make_pipeline_result


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_resolve_document_by_name_finds_full_title() -> None:
    catalog = load_sample_catalog()
    doc_name = "Об электроэнергетике"

    result = resolve_document_by_name(doc_name, catalog=catalog)

    assert result.found is True
    assert result.doc_id == "doc_fz35"
    assert result.doc_name == doc_name
    assert result.status in {"exact", "probable"}
    assert result.mention_kind == "title"


def test_resolve_document_finds_doc_by_abbreviation() -> None:
    catalog = load_sample_catalog()

    result = resolve_document(
        "расскажи про ПУЭ по заземлению",
        catalog=catalog,
        enable_pue_aliases=True,
    )

    assert result.found is True
    assert result.doc_id == "doc_pue"
    assert result.doc_name == "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"
    assert result.mention_kind == "abbreviation"


def test_resolve_document_finds_doc_by_order_number() -> None:
    catalog = load_sample_catalog()

    result = resolve_document(
        "что в приказе минэнерго 796 про персонал",
        catalog=catalog,
    )

    assert result.found is True
    assert result.doc_id == "doc_796"
    assert result.status == "exact"
    assert result.mention_kind == "order_number"


def test_resolve_document_finds_doc_from_embedded_title_phrase() -> None:
    catalog = load_sample_catalog()

    result = resolve_document(
        "Что сказано в Правилах предотвращения развития и ликвидации нарушений "
        "нормального режима про пункт 9?",
        catalog=catalog,
    )

    assert result.found is True
    assert result.doc_id == "doc_likvid"
    assert "ликвидации нарушений" in result.doc_name


def test_resolve_document_rejects_unknown_name() -> None:
    catalog = load_sample_catalog()

    result = resolve_document_by_name("Совершенно неизвестный документ XYZ", catalog=catalog)

    assert result.found is False
    assert result.status == "not_found"
    assert result.doc_id == ""


def test_resolve_document_skips_when_no_document_mention() -> None:
    catalog = load_sample_catalog()

    result = resolve_document(
        "какие требования к заземлению",
        catalog=catalog,
    )

    assert result.found is False
    assert "document_resolve:no_document_mention" in result.warnings


def test_resolve_document_from_label_finds_short_alias() -> None:
    catalog = load_sample_catalog()

    result = resolve_document_from_label("796", catalog=catalog)

    assert result.found is True
    assert result.doc_id == "doc_796"
    assert "персоналом" in result.doc_name


def test_resolve_document_from_label_finds_fz_alias() -> None:
    catalog = load_sample_catalog()

    result = resolve_document_from_label("35-фз", catalog=catalog)

    assert result.found is True
    assert result.doc_id == "doc_fz35"
    assert result.doc_name == "Об электроэнергетике"


def test_run_norm_lookup_applies_document_resolve_doc_filter(monkeypatch) -> None:
    locked = DocumentResolveResult(
        found=True,
        doc_id="doc_796",
        doc_name="Об утверждении Правил работы с персоналом в организациях электроэнергетики Российской Федерации",
        status="exact",
        confidence=0.9,
        mention_kind="order_number",
    )
    captured: dict[str, object] = {}

    def fake_plan_query(*args, **kwargs):
        captured["filters"] = dict(kwargs.get("filters") or {})
        from mr_norm.runtime.contracts import PreparedQueryPlan

        return PreparedQueryPlan(original_query=str(args[0] if args else ""))

    monkeypatch.setattr("mr_norm.skills.norm_lookup.resolve_document", lambda *args, **kwargs: locked)
    monkeypatch.setattr("mr_norm.skills.norm_lookup.prefetch_gost_snippets", lambda *args, **kwargs: [])
    monkeypatch.setattr("mr_norm.skills.norm_lookup.plan_query", fake_plan_query)
    monkeypatch.setattr("mr_norm.skills.norm_lookup.run_pipeline", lambda *args, **kwargs: make_pipeline_result())

    run_norm_lookup(
        NormLookupRequest(
            query="что в приказе минэнерго 796 про персонал",
            understand_query_mode="auto",
        ),
        IndexingConfig(collection_name="test_collection"),
    )

    assert captured["filters"]["doc_id"] == "doc_796"
