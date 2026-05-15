from __future__ import annotations

import json

from mr_norm.config.paths import ProjectPaths
from mr_norm.eval.chunk_quality import compare_quality, render_comparison_markdown
from mr_norm.eval.chunk_quality import save_baseline_comparison
from mr_norm.tools.chunker import ChunkBuilder
from tests.test_marking_payload_quality import make_structured_document


def test_new_chunks_are_better_than_controlled_baseline_fixture() -> None:
    new_chunks = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())  # type: ignore[arg-type]
    baseline = []
    for chunk in new_chunks:
        degraded = {
            "text": f"// {chunk['text']} \\",
            "payload": {
                "filename": chunk["payload"]["filename"],
                "doc_name": "",
                "doc_reg": chunk["payload"]["doc_reg"],
                "doc_title_full": "",
                "approving_act": "",
                "metadata_source": "filename_fallback",
                "metadata_confidence": "low",
                "headings": [],
                "nearest_heading": "",
                "heading_path_text": "",
                "point_number": "",
                "point_scope": "__no_heading__",
                "point_anchor": str(chunk["payload"]["chunk_start"]),
                "point_identity_key": "__unnumbered__::__no_heading__::0",
                "chunk_index": chunk["payload"]["chunk_index"],
                "chunk_start": chunk["payload"]["chunk_start"],
                "part_index": 0,
                "total_parts": 1,
                "is_split": False,
            },
        }
        baseline.append(degraded)

    comparison = compare_quality(new_chunks, baseline)

    assert comparison["passes"]
    assert comparison["comparisons"]["chunks_without_doc_name"]["passes"]
    assert comparison["comparisons"]["chunks_without_heading_path_text"]["passes"]
    assert comparison["comparisons"]["chunks_without_point_number"]["passes"]
    assert comparison["comparisons"]["service_markers"]["passes"]
    assert comparison["baseline_scope"]["same_document_scope"]
    assert "PASS" in render_comparison_markdown(comparison)


def test_baseline_comparison_fails_on_document_scope_mismatch() -> None:
    new_chunks = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())  # type: ignore[arg-type]
    baseline = []
    for chunk in new_chunks:
        degraded = dict(chunk)
        degraded["payload"] = dict(chunk["payload"])
        degraded["payload"]["filename"] = "another_document.txt"
        baseline.append(degraded)

    comparison = compare_quality(new_chunks, baseline)

    assert not comparison["passes"]
    assert any(item["code"] == "baseline_scope_mismatch" for item in comparison["blocking_defects"])


def test_save_baseline_comparison_reports_missing_baseline(tmp_path) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()
    paths.chunks_json.write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

    result = save_baseline_comparison(paths, root / "missing_baseline.json")

    assert not result["passes"]
    assert result["report_path"]
    assert result["markdown_report_path"]
    assert result["blocking_defects"][0]["code"] == "baseline_chunks_json_missing"
