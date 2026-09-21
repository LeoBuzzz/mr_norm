from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.document_knowledge import (
    DocumentKnowledgeIndex,
    KnowledgeDocument,
    find_abbreviation_document_candidates,
    find_knowledge_candidates,
    load_document_knowledge,
    match_query_terms,
)
from mr_norm.runtime.query_planner import prepare_query
from mr_norm.retrieval.document_catalog import load_catalog_snapshot


def _knowledge() -> DocumentKnowledgeIndex:
    return DocumentKnowledgeIndex(
        documents=[
            KnowledgeDocument(
                doc_id="doc_ptf",
                doc_name="Правила технологического функционирования электроэнергетических систем",
            )
        ],
        abbreviations=[
            {
                "abbreviation": "птф",
                "expansion": "правила технологического функционирования",
            }
        ],
    )


def test_default_index_loads_copied_rag_norm_abbreviations() -> None:
    knowledge = load_document_knowledge()

    assert len(knowledge.abbreviations) > 1000
    assert any(item.get("abbreviation") == "ACER" for item in knowledge.abbreviations)
    assert not any(item.get("abbreviation") == "2009." for item in knowledge.abbreviations)
    assert not any(item.get("abbreviation") == "ABB" for item in knowledge.abbreviations)


def test_vl_has_both_full_and_short_expansions() -> None:
    knowledge = load_document_knowledge()
    expansions = {
        str(item["expansion"])
        for item in knowledge.abbreviations
        if item.get("abbreviation") == "ВЛ"
    }

    assert expansions == {"воздушная линия электропередачи", "воздушная линия"}


def test_vl_equivalent_phrasings_produce_the_same_search_variants() -> None:
    knowledge = load_document_knowledge()

    short = match_query_terms("как часто надо осматривать ВЛ", knowledge)
    full = match_query_terms("как часто надо осматривать воздушные линии", knowledge)

    assert set(short.abbreviation_expansions) == set(full.abbreviation_expansions)
    assert set(short.abbreviation_expansions) == {
        "воздушная линия электропередачи",
        "воздушная линия",
        "ВЛ",
    }

    catalog = load_catalog_snapshot(
        Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    )
    short_plan = prepare_query("как часто надо осматривать ВЛ", catalog=catalog, knowledge=knowledge)
    full_plan = prepare_query(
        "как часто надо осматривать воздушные линии",
        catalog=catalog,
        knowledge=knowledge,
    )
    short_payload = next(item.queries for item in short_plan.tool_queries if item.tool_name == "payload")
    full_payload = next(item.queries for item in full_plan.tool_queries if item.tool_name == "payload")

    assert "периодичность осмотров воздушная линия электропередачи" in short_payload
    assert "периодичность осмотров воздушная линия электропередачи" in full_payload
    assert "как часто надо осматривать воздушная линия электропередачи" in short_payload
    assert "как часто надо осматривать воздушная линия электропередачи" in full_payload
    assert "как часто надо осматривать воздушные линии" in short_payload
    assert "как часто надо осматривать воздушные линии" in full_payload
    assert "верховой осмотр воздушная линия электропередачи" in short_payload
    assert "верховой осмотр воздушная линия электропередачи" in full_payload


def test_frequency_plan_keeps_short_form_contextual_and_drops_bare_alias() -> None:
    knowledge = load_document_knowledge()
    catalog = load_catalog_snapshot(
        Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    )

    plan = prepare_query(
        "как часто надо осматривать воздушные линии",
        catalog=catalog,
        knowledge=knowledge,
    )
    payload = next(item.queries for item in plan.tool_queries if item.tool_name == "payload")

    assert "как часто надо осматривать ВЛ" in payload
    assert "ВЛ" not in payload


def test_imported_full_variant_is_matched_without_long_description_cross_hits() -> None:
    knowledge = load_document_knowledge()
    entry = next(item for item in knowledge.abbreviations if item.get("abbreviation") == "ACER")

    matches = match_query_terms(str(entry["expansion"]), knowledge)

    assert "ACER" in matches.document_hints
    assert str(entry["expansion"]) in matches.abbreviation_expansions


def test_abbreviation_matching_expands_both_directions() -> None:
    knowledge = _knowledge()

    short = match_query_terms("Что устанавливает ПТФ?", knowledge)
    full = match_query_terms(
        "Что устанавливают правила технологического функционирования?", knowledge
    )

    assert short.abbreviation_expansions == (
        "правила технологического функционирования",
        "птф",
    )
    assert full.abbreviation_expansions == (
        "птф",
        "правила технологического функционирования",
    )


def test_full_abbreviation_variant_resolves_document() -> None:
    knowledge = _knowledge()

    candidates = find_abbreviation_document_candidates(
        "в правилах технологического функционирования",
        knowledge,
    )
    knowledge_candidates = find_knowledge_candidates(
        "в правилах технологического функционирования",
        knowledge,
    )

    assert [item.doc_id for item in candidates] == ["doc_ptf"]
    assert [item.doc_id for item in knowledge_candidates] == ["doc_ptf"]


def test_planner_sends_both_abbreviation_variants_to_search_tools() -> None:
    knowledge = _knowledge()
    plan = prepare_query(
        "Что устанавливает ПТФ?",
        catalog=load_catalog_snapshot(
            Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
        ),
        knowledge=knowledge,
        mode="auto",
    )

    by_tool = {entry.tool_name: entry.queries for entry in plan.tool_queries}
    for tool_name in ("payload", "vector"):
        assert "птф" in by_tool[tool_name]
        assert "правила технологического функционирования" in by_tool[tool_name]
