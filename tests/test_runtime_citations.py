from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.citations import validate_citations
from mr_norm.runtime.contracts import Citation


def make_evidence() -> list[RetrievedItem]:
    return [
        RetrievedItem(
            chunk_id="chunk_1",
            doc_name="ПУЭ",
            point_number="1.7.1",
            source_tool="payload",
        ),
        RetrievedItem(
            chunk_id="chunk_2",
            doc_name="ГОСТ",
            point_number="2.1",
            source_tool="vector",
        ),
    ]


def test_validate_citations_accepts_matching_evidence() -> None:
    valid, warnings = validate_citations(
        make_evidence(),
        [
            {"chunk_id": "chunk_1", "doc_name": "ПУЭ", "point_number": "1.7.1"},
            Citation(chunk_id="chunk_2"),
        ],
    )

    assert warnings == []
    assert [citation.chunk_id for citation in valid] == ["chunk_1", "chunk_2"]
    assert valid[0].doc_name == "ПУЭ"
    assert valid[1].doc_name == "ГОСТ"


def test_validate_citations_fills_doc_name_and_point_number_from_evidence() -> None:
    valid, warnings = validate_citations(make_evidence(), [{"chunk_id": "chunk_2"}])

    assert warnings == []
    assert valid[0].doc_name == "ГОСТ"
    assert valid[0].point_number == "2.1"


def test_validate_citations_rejects_unknown_chunk_id() -> None:
    valid, warnings = validate_citations(make_evidence(), [{"chunk_id": "missing"}])

    assert valid == []
    assert warnings == ["citation[0]: unknown chunk_id 'missing'"]


def test_validate_citations_rejects_mismatched_doc_name() -> None:
    valid, warnings = validate_citations(
        make_evidence(),
        [{"chunk_id": "chunk_1", "doc_name": "ГОСТ"}],
    )

    assert valid == []
    assert "doc_name" in warnings[0]


def test_validate_citations_rejects_mismatched_point_number() -> None:
    valid, warnings = validate_citations(
        make_evidence(),
        [{"chunk_id": "chunk_1", "doc_name": "ПУЭ", "point_number": "9.9"}],
    )

    assert valid == []
    assert "point_number" in warnings[0]


def test_validate_citations_rejects_missing_chunk_id() -> None:
    valid, warnings = validate_citations(make_evidence(), [{"doc_name": "ПУЭ"}])

    assert valid == []
    assert warnings == ["citation[0]: missing chunk_id"]
