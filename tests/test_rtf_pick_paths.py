from __future__ import annotations

from pathlib import Path

from mr_norm.tools.rtf_processor import pick_size_diverse_rtf_paths


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
