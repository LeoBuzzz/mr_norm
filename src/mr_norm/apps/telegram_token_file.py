from __future__ import annotations

import os
from pathlib import Path


def get_telegram_token_from_env_or_file(
    *,
    root: Path | None = None,
) -> str:
    """Load the Telegram bot token without ever logging its value."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip().strip('"').strip("'")
    if token:
        return token

    project_root = root or Path(__file__).resolve().parents[3]
    token_path = project_root / "bot_token.txt"
    if not token_path.is_file():
        return ""
    return token_path.read_text(encoding="utf-8-sig").strip().strip('"').strip("'")


def validate_telegram_token(token: str) -> None:
    value = (token or "").strip()
    prefix, separator, suffix = value.partition(":")
    if not separator or not prefix.isdigit() or not suffix:
        raise ValueError(
            "Неверный токен Telegram. Задайте TELEGRAM_BOT_TOKEN или положите токен в bot_token.txt."
        )
