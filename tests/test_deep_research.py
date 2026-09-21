from __future__ import annotations

import pytest

from mr_norm.runtime.deep_research_memory import parse_document_selection
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.skills.deep_research import (
    ResearchAnalysis,
    build_document_candidates,
    group_evidence_by_doc_id,
    normalize_research_analysis_payload,
    rank_document_groups,
    _dedupe_items,
    _select_memo_evidence,
)


def test_parse_document_selection_numbers() -> None:
    indices, error = parse_document_selection("1, 3, 3", max_index=5)
    assert error == ""
    assert indices == (1, 3)


def test_parse_document_selection_all() -> None:
    indices, error = parse_document_selection("все", max_index=4)
    assert error == ""
    assert indices == (1, 2, 3, 4)


def test_parse_document_selection_invalid() -> None:
    indices, error = parse_document_selection("abc", max_index=3)
    assert indices == ()
    assert "Неверный номер" in error


def test_parse_document_selection_out_of_range() -> None:
    indices, error = parse_document_selection("5", max_index=3)
    assert indices == ()
    assert "вне диапазона" in error


def test_normalize_research_analysis_payload() -> None:
    analysis, warnings = normalize_research_analysis_payload(
        {
            "normalized_question": "Требования к хранению документации",
            "aspects": ["хранение", "сроки"],
            "terms": ["ПТЭ", "архив"],
            "search_queries": ["требования хранение", "срок хранения"],
        }
    )
    assert analysis.normalized_question.startswith("Требования")
    assert len(analysis.aspects) == 2
    assert warnings == []


def test_group_evidence_by_doc_id_rejects_missing_doc_id() -> None:
    items = [
        RetrievedItem(chunk_id="a", doc_id="doc1", doc_name="ПТЭ", score=0.9),
        RetrievedItem(chunk_id="b", doc_id="", doc_name="Без id", score=0.8),
    ]
    groups, rejected = group_evidence_by_doc_id(items)
    assert rejected == 1
    assert list(groups) == ["doc1"]


def test_rank_document_groups_prefers_coverage() -> None:
    groups = {
        "doc_a": [
            RetrievedItem(
                chunk_id="a1",
                doc_id="doc_a",
                doc_name="Документ A",
                point_number="1",
                text="требования хранения",
                score=0.5,
            )
        ],
        "doc_b": [
            RetrievedItem(
                chunk_id="b1",
                doc_id="doc_b",
                doc_name="Документ B",
                point_number="2",
                text="требования хранения и сроки архивирования",
                score=0.45,
            ),
            RetrievedItem(
                chunk_id="b2",
                doc_id="doc_b",
                doc_name="Документ B",
                point_number="3",
                text="ответственность",
                score=0.4,
            ),
        ],
    }
    ranked = rank_document_groups(groups, aspects=("хранение", "сроки", "ответственность"))
    assert ranked[0][0] == "doc_b"


def test_build_document_candidates_numbering() -> None:
    ranked = [
        (
            "doc1",
            [RetrievedItem(chunk_id="1", doc_id="doc1", doc_name="ПТЭ", point_number="65", score=0.9)],
            1.0,
            "reason",
        )
    ]
    candidates = build_document_candidates(ranked)
    assert len(candidates) == 1
    assert candidates[0].index == 1
    assert candidates[0].doc_name == "ПТЭ"
    assert candidates[0].point_numbers == ("65",)


def test_dedupe_items() -> None:
    items = [
        RetrievedItem(chunk_id="same", doc_id="doc1"),
        RetrievedItem(chunk_id="same", doc_id="doc1"),
        RetrievedItem(chunk_id="other", doc_id="doc2"),
    ]
    deduped = _dedupe_items(items)
    assert len(deduped) == 2


def test_select_memo_evidence_respects_per_doc_limit() -> None:
    items = [
        RetrievedItem(chunk_id=f"c{i}", doc_id="doc1", score=float(i))
        for i in range(5)
    ] + [
        RetrievedItem(chunk_id=f"d{i}", doc_id="doc2", score=float(i))
        for i in range(5)
    ]
    selected = _select_memo_evidence(
        items,
        selected_doc_ids=("doc1", "doc2"),
        per_doc_limit=2,
        total_limit=10,
    )
    doc1_count = sum(1 for item in selected if item.doc_id == "doc1")
    doc2_count = sum(1 for item in selected if item.doc_id == "doc2")
    assert doc1_count == 2
    assert doc2_count == 2


def test_normalize_research_analysis_requires_fields() -> None:
    with pytest.raises(ValueError):
        normalize_research_analysis_payload({"normalized_question": "", "aspects": [], "terms": [], "search_queries": []})
