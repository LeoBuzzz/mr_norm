"""Build a separate Windows checkout containing the production bots and their data."""

from __future__ import annotations

import argparse
import shutil
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
    for relative in ("output/qdrant_chunks.json", "output/document_catalog.json", "tmp/knowledge_catalog_mapping.json"):
        source = ROOT / relative
        if source.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
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
    (target / "README.md").write_text(
        "# MR Norm Bot\n\n"
        "Windows package for the VK and Telegram bots. Python 3.12 and Qdrant are required.\n\n"
        "1. Run `powershell -ExecutionPolicy Bypass -File .\\setup_bot.ps1`.\n"
        "2. Start Qdrant on localhost:6333.\n"
        "3. On the source machine, export with `python scripts/bot_db_transfer.py export --directory transfer`. "
        "Copy `transfer/` separately to this machine.\n"
        "4. Import with `.\\venv312\\Scripts\\python.exe scripts/bot_db_transfer.py import --directory transfer`. "
        "This restores the Qdrant collection, chunks, document catalog and optional mapping.\n"
        "5. Provide `keys`, `VK_token.txt` and `bot_token.txt` locally (they are ignored by Git).\n"
        "6. Run `run_bots.bat`, or one transport with `run_vk_bot.bat` / `run_telegram_bot.bat`.\n\n"
        "Bot launchers use collection `mr_norm_docs_bge_m3_rtf_v2_20261011`. "
        "On a machine without the local Telegram proxy, set `TELEGRAM_PROXY` to an empty string. "
        "Do not run both source and migrated bots with the same tokens at once.\n",
        encoding="utf-8",
    )
    print(f"Built bot package in {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    build(args.target)


if __name__ == "__main__":
    main()
