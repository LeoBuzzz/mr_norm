"""Build a separate Windows checkout containing the production bots and their data."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_tree(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if (not path.is_file() or "__pycache__" in path.parts
                or "document_passports" in path.parts or path.suffix in {".pyc", ".pyo"}):
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def build(target: Path) -> None:
    target = target.resolve()
    if not target.is_dir() or any(target.iterdir()):
        raise ValueError(f"Target must be an existing empty directory: {target}")
    copy_tree(ROOT / "src" / "mr_norm", target / "src" / "mr_norm")
    copy_tree(ROOT / "sandbox" / "question_parser", target / "sandbox" / "question_parser")
    for relative in (
        "pyproject.toml", "requirements-gpu.txt", "requirements-vk.txt",
        "requirements-telegram.txt", "run_bots.bat", "run_vk_bot.bat",
        "run_telegram_bot.bat", "scripts/bot_db_transfer.py",
        "probe/term-definitions.jsonl",
    ):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    for name in ("input", "output", "planning", "tmp"):
        (target / name).mkdir(exist_ok=True)
    for relative in ("output/qdrant_chunks.json", "tmp/knowledge_catalog_mapping.json"):
        source = ROOT / relative
        if relative == "output/qdrant_chunks.json":
            v2_chunks = ROOT / "output/new_pipeline_20261011/qdrant_chunks.json"
            if v2_chunks.is_file():
                source = v2_chunks
        if source.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    if (target / "output/qdrant_chunks.json").is_file():
        sys.path.insert(0, str(ROOT / "src"))
        from mr_norm.retrieval.document_catalog import build_catalog_from_chunks, save_catalog_snapshot

        save_catalog_snapshot(
            build_catalog_from_chunks(target / "output/qdrant_chunks.json"),
            target / "output/document_catalog.json",
        )
    for name in ("keys", "VK_token.txt", "bot_token.txt"):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, target / name)

    (target / ".gitignore").write_text(
        "venv312/\n__pycache__/\n*.pyc\n.pytest_cache/\n"
        "keys\nVK_token.txt\nbot_token.txt\n.env\n.env.*\n"
        "output/\ninput/\ntmp/\ntransfer/\n*.log\n",
        encoding="utf-8",
    )
    (target / "setup_bot.ps1").write_text(
        '$ErrorActionPreference = "Stop"\n'
        'Set-Location $PSScriptRoot\n'
        'py -3.12 -m venv venv312\n'
        '.\\venv312\\Scripts\\python.exe -m pip install --upgrade pip\n'
        '.\\venv312\\Scripts\\python.exe -m pip install -e . -r requirements-vk.txt -r requirements-telegram.txt\n'
        'Write-Host "Setup complete. Import Qdrant data before starting the bots."\n',
        encoding="utf-8",
    )
    (target / "compose.yaml").write_text(
        "services:\n"
        "  qdrant:\n"
        "    image: qdrant/qdrant:v1.15.4\n"
        "    ports:\n"
        '      - "127.0.0.1:6333:6333"\n'
        "    volumes:\n"
        "      - qdrant_storage:/qdrant/storage\n"
        "    restart: unless-stopped\n"
        "volumes:\n"
        "  qdrant_storage:\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "scripts/bot_package_readme.md", target / "README.md")
    print(f"Built bot package in {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    build(args.target)


if __name__ == "__main__":
    main()
