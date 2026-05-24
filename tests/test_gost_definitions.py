from __future__ import annotations

from mr_norm.retrieval.gost_definitions import (
    GostSnippet,
    enrich_tool_queries_with_gost,
    extract_gost_search_terms,
    merge_gost_into_evidence,
    should_prefetch_gost,
)
from mr_norm.retrieval.contracts import RetrievedItem


def test_should_prefetch_gost_skips_point_lookup() -> None:
    assert should_prefetch_gost("дай определение оперативного персонала") is True
    assert should_prefetch_gost("дай определение по п. 98 приказа 796") is False
    assert should_prefetch_gost("определение", {"point_number": "3.1"}) is False


def test_extract_gost_search_terms_inflected_phrase() -> None:
    terms = extract_gost_search_terms("Дай определение оперативного персонала")
    assert "оперативного персонала" in terms
    assert "оперативный персонал" in terms
    assert "определение" not in terms


def test_merge_gost_into_evidence_prepends_and_dedupes() -> None:
    snippet = GostSnippet(
        keyword="оперативный персонал",
        text="107 оперативный персонал: определение",
        doc_name="ГОСТ Р 57114-2022",
        point_number="107",
        chunk_id="chunk_gost",
        doc_id="doc_4745dec28ca589e1",
        score=0.9,
    )
    existing = RetrievedItem(
        chunk_id="chunk_gost",
        doc_name="другой",
        text="дубликат",
        source_tool="vector",
    )
    other = RetrievedItem(chunk_id="chunk_other", doc_name="ПТЭ", text="норма", source_tool="vector")
    merged = merge_gost_into_evidence([existing, other], [snippet])
    assert merged[0].source_tool == "gost_definition"
    assert merged[0].chunk_id == "chunk_gost"
    assert len(merged) == 2
    assert merged[1].chunk_id == "chunk_other"


def test_enrich_tool_queries_with_gost() -> None:
    snippet = GostSnippet(
        keyword="оперативного персонала",
        text="text",
        doc_name="ГОСТ",
        point_number="107",
        chunk_id="c1",
        doc_id="doc_4745dec28ca589e1",
        score=0.5,
    )
    enriched = enrich_tool_queries_with_gost(
        {"vector": ["исходный"]},
        [snippet],
        original_query="Дай определение оперативного персонала",
    )
    assert "оперативного персонала" in enriched["vector"]
    assert "определение оперативного персонала" in enriched["vector"]
