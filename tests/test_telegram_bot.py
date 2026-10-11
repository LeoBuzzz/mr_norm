from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mr_norm.apps.telegram_bot import (
    MRNormTelegramBot,
    _format_router2_result,
    _split_message,
)
from mr_norm.apps.telegram_token_file import (
    get_telegram_token_from_env_or_file,
    validate_telegram_token,
)
from mr_norm.runtime.dialog_memory import DialogSessionStore
from question_parser.router2_runtime import Router2Result


def _message(text: str = "вопрос", chat_id: int = 100, user_id: int = 200):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id),
    )


def _result(answer: str = "ответ") -> Router2Result:
    return Router2Result(
        status="answered",
        question="вопрос",
        plan={"status": "ready", "operation": "search", "tools": []},
        answer=answer,
        citations=[{"chunk_id": "c1", "doc_name": "Правила", "point_number": "7"}],
        evidence=[{
            "chunk_id": "c1",
            "doc_id": "doc_rules",
            "doc_name": "Правила",
            "point_number": "7",
        }],
    )


def _bot() -> MRNormTelegramBot:
    bot = MRNormTelegramBot.__new__(MRNormTelegramBot)
    bot.bot = SimpleNamespace(reply_to=AsyncMock())
    bot._dialog_sessions = DialogSessionStore()
    bot._router2_pending = {}
    return bot


def test_token_loader_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "bot_token.txt").write_text("222:file", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:env")
    assert get_telegram_token_from_env_or_file(root=tmp_path) == "111:env"


def test_token_validation_rejects_placeholder() -> None:
    with pytest.raises(ValueError):
        validate_telegram_token("not-a-token")


def test_message_split_respects_telegram_limit() -> None:
    chunks = _split_message("слово " * 2000, 500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_router2_format_includes_citation() -> None:
    formatted = _format_router2_result(_result())
    assert "ответ" in formatted
    assert "п. 7 — Правила" in formatted


def test_plain_question_runs_router2_and_records_dialog() -> None:
    bot = _bot()
    bot._run_router2 = AsyncMock(return_value=_result("готово"))
    message = _message("дай пункт 7")

    asyncio.run(bot._process_dialog(message, message.text))

    bot._run_router2.assert_awaited_once_with("дай пункт 7")
    context = bot._dialog_sessions.get("100:200")
    assert context.last_turn is not None
    assert context.last_turn.answer == "готово"
    assert context.last_turn.source_references[0].doc_id == "doc_rules"


def test_clarification_is_isolated_by_chat_and_resumed_by_number() -> None:
    bot = _bot()
    plan = {
        "status": "needs_clarification",
        "clarification": {"question": "Какой документ?", "option_ids": ["doc_a", "doc_b"]},
    }
    clarification = Router2Result(
        status="needs_clarification",
        question="дай пункт 5",
        plan=plan,
        clarification=plan["clarification"],
    )
    bot._run_router2 = AsyncMock(side_effect=[clarification, _result("текст пункта")])

    asyncio.run(bot._process_dialog(_message("дай пункт 5"), "дай пункт 5"))
    assert "100:200" in bot._router2_pending
    assert "101:200" not in bot._router2_pending

    asyncio.run(bot._process_dialog(_message("2"), "2"))
    assert "100:200" not in bot._router2_pending
    assert bot._run_router2.await_args_list[1].kwargs == {
        "selected_option": "doc_b",
        "prior_plan": plan,
    }
