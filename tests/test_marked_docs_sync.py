from __future__ import annotations

import json
from pathlib import Path

from mr_norm.data.normative_registry import normalize_match_stem
from mr_norm.tools.marked_docs_sync import (
    collect_marked_output_groups,
    is_marked_group_complete,
    prune_orphan_marked_outputs,
    sync_marked_docs_with_input,
)


def _write_structured(path: Path, *, filename: str, paragraphs: list[dict] | None = None) -> None:
    payload = {
        "filename": filename,
        "paragraphs": paragraphs or [{"text": "п. 1", "point_number": "1"}],
        "read_error": "",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_prune_orphan_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    marked = tmp_path / "marked_docs"
    input_dir.mkdir()
    marked.mkdir()

    (input_dir / "keep.rtf").write_text("x", encoding="utf-8")
    (marked / "keep.txt").write_text("text", encoding="utf-8")
    _write_structured(marked / "keep.structured.json", filename="keep.txt")

    (marked / "orphan.txt").write_text("old", encoding="utf-8")
    _write_structured(marked / "orphan.structured.json", filename="orphan.txt")

    removed = prune_orphan_marked_outputs(input_dir, marked)
    assert len(removed) == 2
    assert (marked / "keep.txt").is_file()
    assert (marked / "keep.structured.json").is_file()
    assert not (marked / "orphan.txt").is_file()
    assert not (marked / "orphan.structured.json").is_file()


def test_is_complete_requires_both_files_and_paragraphs(tmp_path: Path) -> None:
    marked = tmp_path / "marked_docs"
    marked.mkdir()
    _write_structured(marked / "doc.structured.json", filename="doc.txt")
    groups = collect_marked_output_groups(marked)
    key = normalize_match_stem("doc.txt")
    assert key in groups
    assert not is_marked_group_complete(groups[key])

    (marked / "doc.txt").write_text("body", encoding="utf-8")
    groups = collect_marked_output_groups(marked)
    assert is_marked_group_complete(groups[key])


def test_sync_report_counts_complete(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    marked = tmp_path / "marked_docs"
    input_dir.mkdir()
    marked.mkdir()
    (input_dir / "a.rtf").write_text("x", encoding="utf-8")
    (marked / "a.txt").write_text("t", encoding="utf-8")
    _write_structured(marked / "a.structured.json", filename="a.txt")
    (marked / "stale.txt").write_text("s", encoding="utf-8")

    report = sync_marked_docs_with_input(input_dir, marked)
    assert report.input_rtf_count == 1
    assert len(report.orphans_removed) == 1
    assert report.already_complete_count == 1
    assert not (marked / "stale.txt").is_file()
