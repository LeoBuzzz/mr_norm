"""Сверка stem файлов с match_stems в normative_documents_registry.json."""

from __future__ import annotations

import sys

from mr_norm.apps.main import build_parser, main


if __name__ == "__main__":
    argv = ["registry-check", *sys.argv[1:]]
    raise SystemExit(main(argv))
