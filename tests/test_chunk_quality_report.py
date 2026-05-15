from __future__ import annotations

from mr_norm.eval.chunk_quality import (
    build_quality_report,
    chunk_quality_penalties,
    chunk_quality_score,
    render_quality_markdown,
)
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
    assert "penalties" in report["worst_chunks"][0]
    assert report["passes_quality_gate"]
    assert report["blocking_defects"] == []


def test_chunk_quality_score_penalizes_bad_chunk() -> None:
    good = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())[-1]  # type: ignore[arg-type]
    bad = {"text": "Оборванный:", "payload": {}}

    assert chunk_quality_score(good) > chunk_quality_score(bad)
    assert {item["code"] for item in chunk_quality_penalties(bad)} >= {
        "missing_doc_name",
        "missing_heading_path_text",
        "looks_truncated",
        "missing_required_payload_keys",
    }


def test_quality_report_scores_document_metadata() -> None:
    chunks = ChunkBuilder(paths=None).build_document_chunks(make_approved_order_document())  # type: ignore[arg-type]

    metadata_quality = build_quality_report(chunks)["documents"]["metadata_quality"]

    assert metadata_quality["documents_evaluated"] == 1
    assert metadata_quality["avg_score"] == 100
    assert metadata_quality["missing_counts"]["doc_kind"] == 0
    assert metadata_quality["missing_counts"]["doc_date"] == 0
    assert metadata_quality["missing_counts"]["authority"] == 0
    assert metadata_quality["missing_counts"]["approving_act"] == 0


def test_quality_report_records_blocking_defects_and_markdown() -> None:
    bad = {"chunk_id": "dup", "text": "// Оборванный: \\", "payload": {}}

    report = build_quality_report([bad, bad], run_context={"command": "quality-report", "scope": "test"})
    markdown = render_quality_markdown(report)

    assert not report["passes_quality_gate"]
    assert {item["code"] for item in report["blocking_defects"]} >= {
        "missing_required_payload_keys",
        "service_markers_in_text",
        "missing_doc_name",
    }
    assert "# Chunk Quality Report" in markdown
    assert "FAIL" in markdown
