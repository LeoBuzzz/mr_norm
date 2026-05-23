"""Синхронизация input/*.rtf с output/marked_docs без запуска Word."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mr_norm.config.paths import ProjectPaths  # noqa: E402
from mr_norm.tools.marked_docs_sync import sync_marked_docs_with_input  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Удалить хвосты marked_docs без RTF в input.")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    paths = ProjectPaths.from_root(args.root)
    paths.ensure_output_dirs()
    report = sync_marked_docs_with_input(paths.input_dir, paths.marked_docs_dir)
    out = args.report or (paths.reports_dir / "marked_docs_sync.json")
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(out), **report.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
