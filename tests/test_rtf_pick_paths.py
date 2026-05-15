from __future__ import annotations

from pathlib import Path

from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.chunker import ChunkBuilder
from mr_norm.tools.rtf_processor import RtfProcessor, pick_size_diverse_rtf_paths


def test_pick_size_diverse_rtf_paths_spreads_by_size(tmp_path: Path) -> None:
    d = tmp_path / "in"
    d.mkdir()
    sizes = [100, 1, 50, 200, 75, 300, 2, 400, 150, 25, 500, 10]
    for i, sz in enumerate(sizes):
        p = d / f"doc_{i:02d}.rtf"
        p.write_bytes(b"x" * sz)
    picked = pick_size_diverse_rtf_paths(d, 10)
    assert len(picked) == 10
    assert len(set(picked)) == 10
    by_size = sorted(picked, key=lambda p: p.stat().st_size)
    assert by_size[0].stat().st_size <= by_size[-1].stat().st_size
    assert by_size[0].stat().st_size == 1
    assert by_size[-1].stat().st_size == 500


def test_pick_size_diverse_returns_all_when_fewer_files(tmp_path: Path) -> None:
    d = tmp_path / "in"
    d.mkdir()
    for i in range(3):
        (d / f"a{i}.rtf").write_text("{}", encoding="utf-8")
    picked = pick_size_diverse_rtf_paths(d, 10)
    assert len(picked) == 3


def test_empty_structured_path_list_builds_empty_chunks(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    paths.ensure_output_dirs()

    chunks = ChunkBuilder(paths).build_all(structured_paths=[])

    assert chunks == []
    assert paths.chunks_json.is_file()


def test_rtf_processor_empty_input_does_not_open_word(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    paths = ProjectPaths.from_root(root)
    processor = RtfProcessor(paths)

    def fail_open_word() -> object:
        raise AssertionError("Word should not be opened for empty input")

    monkeypatch.setattr(processor, "_open_word", fail_open_word)

    assert processor.process_all() == []


def test_rtf_processor_uses_isolated_worker_per_file(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    input_dir = root / "input" / "All_raw_docks"
    input_dir.mkdir(parents=True)
    first = input_dir / "a.rtf"
    second = input_dir / "b.rtf"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    paths = ProjectPaths.from_root(root)
    processor = RtfProcessor(paths)
    seen: list[tuple[Path, float]] = []

    def fake_process(path: Path, per_file_timeout_sec: float):
        seen.append((path, per_file_timeout_sec))
        return processor._failed_rtf_result(path, "simulated failure")

    monkeypatch.setattr(processor, "_process_file_isolated", fake_process)

    results = processor.process_all(per_file_timeout_sec=7)

    assert [item[0].name for item in seen] == ["a.rtf", "b.rtf"]
    assert [item[1] for item in seen] == [7, 7]
    assert len(results) == 2
    assert processor.last_word_cleanup["mode"] == "isolated_per_file"


def test_rtf_processor_records_isolated_timeout(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "proj"
    (root / "planning").mkdir(parents=True)
    input_dir = root / "input" / "All_raw_docks"
    input_dir.mkdir(parents=True)
    source = input_dir / "timeout.rtf"
    source.write_text("{}", encoding="utf-8")
    paths = ProjectPaths.from_root(root)
    processor = RtfProcessor(paths)

    def fake_process(path: Path, per_file_timeout_sec: float):
        return processor._failed_rtf_result(path, f"{path}: Word COM read timed out after {per_file_timeout_sec:g}s")

    monkeypatch.setattr(processor, "_process_file_isolated", fake_process)

    results = processor.process_all(per_file_timeout_sec=3)

    assert results[0].paragraphs == 0
    assert "timed out" in results[0].error
    assert processor.last_word_cleanup["status"] == "timeouts"
    assert processor.last_word_cleanup["files_timed_out"] == 1
