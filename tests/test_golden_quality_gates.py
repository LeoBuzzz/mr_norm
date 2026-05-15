from __future__ import annotations

import json
from pathlib import Path

from mr_norm.eval.chunk_quality import build_quality_report
from mr_norm.tools.chunker import ChunkBuilder, REQUIRED_RAG_NORM_PAYLOAD_KEYS
from mr_norm.tools.rtf_processor import make_paragraph
from mr_norm.tools.schema import StructuredDocument


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_cases.json"


def load_golden_cases() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def document_from_case(case: dict) -> StructuredDocument:
    doc = case["document"]
    paragraphs = [
        make_paragraph(
            i,
            paragraph["text"],
            outline_level=paragraph.get("outline_level", 10),
            style_name=paragraph.get("style_name", ""),
        )
        for i, paragraph in enumerate(doc["paragraphs"])
    ]
    return StructuredDocument(
        source_file=doc["source_file"],
        filename=doc["filename"],
        paragraphs=[paragraph for paragraph in paragraphs if paragraph is not None],
    )


def test_golden_cases_preserve_metadata_headings_and_point_identity() -> None:
    for case in load_golden_cases():
        chunks = ChunkBuilder(paths=None).build_document_chunks(document_from_case(case))  # type: ignore[arg-type]
        expected = case["expected_payload"]
        point_chunk = next(
            chunk for chunk in chunks if chunk["payload"].get("point_number") == expected["point_number"]
        )
        payload = point_chunk["payload"]

        assert REQUIRED_RAG_NORM_PAYLOAD_KEYS <= set(payload), case["name"]
        assert payload["doc_name"] == expected["doc_name"], case["name"]
        assert expected["doc_reg_contains"] in payload["doc_reg"], case["name"]
        assert payload["doc_kind"] == expected["doc_kind"], case["name"]
        assert payload["doc_number"] == expected["doc_number"], case["name"]
        assert payload["doc_date"] == expected["doc_date"], case["name"]
        assert payload["authority"] == expected["authority"], case["name"]
        assert payload["heading_path_text"] == expected["heading_path_text"], case["name"]
        assert payload["point_number"] == expected["point_number"], case["name"]
        assert payload["point_identity_key"].startswith(f"{expected['point_number']}::"), case["name"]


def test_golden_cases_pass_quality_gate() -> None:
    chunks = []
    for case in load_golden_cases():
        chunks.extend(ChunkBuilder(paths=None).build_document_chunks(document_from_case(case)))  # type: ignore[arg-type]

    report = build_quality_report(chunks, run_context={"scope": "golden-fixtures"})

    assert report["passes_quality_gate"]
    assert report["blocking_defects"] == []
    assert report["retrieval_readiness"]["required_payload_keys_coverage"] == 1.0
