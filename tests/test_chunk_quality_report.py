from __future__ import annotations

from mr_norm.eval.chunk_quality import build_quality_report, chunk_quality_score
from mr_norm.tools.chunker import ChunkBuilder
from tests.test_marking_payload_quality import make_approved_order_document, make_structured_document


def test_quality_report_counts_payload_and_structure() -> None:
    chunks = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())  # type: ignore[arg-type]

    report = build_quality_report(chunks)

    assert report["chunks"]["chunks_total"] == len(chunks)
    assert report["retrieval_readiness"]["chunks_missing_required_payload_keys"] == 0
    assert report["retrieval_readiness"]["required_payload_keys_coverage"] == 1.0
    assert report["chunks"]["chunks_with_service_markers"] == 0
    assert report["structure"]["chunks_without_heading_path_text"] < len(chunks)
    assert report["points"]["chunks_with_point_number"] >= 2
    assert report["worst_chunks"]


def test_chunk_quality_score_penalizes_bad_chunk() -> None:
    good = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())[-1]  # type: ignore[arg-type]
    bad = {"text": "Оборванный:", "payload": {}}

    assert chunk_quality_score(good) > chunk_quality_score(bad)


def test_quality_report_scores_document_metadata() -> None:
    chunks = ChunkBuilder(paths=None).build_document_chunks(make_approved_order_document())  # type: ignore[arg-type]

    metadata_quality = build_quality_report(chunks)["documents"]["metadata_quality"]

    assert metadata_quality["documents_evaluated"] == 1
    assert metadata_quality["avg_score"] == 100
    assert metadata_quality["missing_counts"]["doc_kind"] == 0
    assert metadata_quality["missing_counts"]["doc_date"] == 0
    assert metadata_quality["missing_counts"]["authority"] == 0
    assert metadata_quality["missing_counts"]["approving_act"] == 0
