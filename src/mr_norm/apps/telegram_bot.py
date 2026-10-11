from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from telebot import asyncio_helper
from telebot.async_telebot import AsyncTeleBot

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_ROOT = PROJECT_ROOT / "sandbox"
if str(SANDBOX_ROOT) not in sys.path:
    sys.path.insert(0, str(SANDBOX_ROOT))

from mr_norm.apps.telegram_token_file import (
    get_telegram_token_from_env_or_file,
    validate_telegram_token,
)
from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.runtime.dialog_memory import (
    DialogSessionStore,
    DialogSourceReference,
    build_retrieval_query,
    build_session_key,
    parse_dialog_input,
)
from question_parser.router2_runtime import (
    DEFAULT_ANSWER_FALLBACK_MODELS,
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ROUTER_FALLBACK_MODELS,
    DEFAULT_ROUTER_MODEL,
    Router2Result,
    run_router2,
)

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_TELEGRAM_PROXY = "http://127.0.0.1:3066"


def _env_models(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return defaults
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _configure_proxy() -> None:
    # An explicitly empty TELEGRAM_PROXY disables the legacy local proxy.
    raw = (
        (os.getenv("TELEGRAM_PROXY") or "").strip()
        if "TELEGRAM_PROXY" in os.environ
        else DEFAULT_TELEGRAM_PROXY
    )
    if raw:
        asyncio_helper.proxy = raw
        safe = raw.rsplit("@", 1)[-1]
        logger.info("Telegram API proxy: %s", safe)


def _split_message(text: str, max_length: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    value = (text or "").strip()
    if len(value) <= max_length:
        return [value] if value else []
    chunks: list[str] = []
    remaining = value
    while len(remaining) > max_length:
        boundary = remaining.rfind("\n\n", 0, max_length)
        if boundary < max_length // 2:
            boundary = remaining.rfind("\n", 0, max_length)
        if boundary < max_length // 2:
            boundary = remaining.rfind(" ", 0, max_length)
        if boundary < max_length // 2:
            boundary = max_length
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _format_router2_result(result: Router2Result) -> str:
    lines: list[str] = []
    if result.answer.strip():
        lines.append(result.answer.strip())
    if result.citations:
        lines.extend(("", "Источники:"))
        for index, citation in enumerate(result.citations[:5], start=1):
            document = str(citation.get("doc_name") or "—")
            point = str(citation.get("point_number") or "—")
            lines.append(f"{index}. п. {point} — {document}")
    return "\n".join(lines).strip()


def _source_references(result: Router2Result) -> tuple[DialogSourceReference, ...]:
    evidence_by_chunk = {
        str(item.get("chunk_id") or ""): item
        for item in result.evidence
        if item.get("chunk_id")
    }
    references: dict[tuple[str, str], DialogSourceReference] = {}
    for item in (*result.citations, *result.evidence):
        evidence = evidence_by_chunk.get(str(item.get("chunk_id") or ""), {})
        doc_id = str(item.get("doc_id") or evidence.get("doc_id") or "").strip()
        doc_name = str(item.get("doc_name") or evidence.get("doc_name") or "").strip()
        point = str(item.get("point_number") or evidence.get("point_number") or "").strip()
        if point and point not in {"—", "-"} and (doc_id or doc_name):
            reference = DialogSourceReference(doc_id, doc_name, point)
            references[(doc_id or doc_name, point)] = reference
    return tuple(references.values())


class MRNormTelegramBot:
    """Telegram transport for the production Router2 pipeline."""

    def __init__(self, token: str) -> None:
        _configure_proxy()
        self.bot = AsyncTeleBot(token)
        self._config: IndexingConfig | None = None
        self._paths = ProjectPaths.from_root()
        self._keys_path: Path | None = self._paths.root / "keys"
        if not self._keys_path.is_file():
            self._keys_path = None
        self._dialog_sessions = DialogSessionStore()
        self._router2_pending: dict[str, dict[str, Any]] = {}
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        @self.bot.message_handler(content_types=["text"])
        async def route(message: Any) -> None:
            await self._dispatch(message)

        @self.bot.message_handler(content_types=["photo", "document", "audio", "video", "voice", "sticker"])
        async def unsupported(message: Any) -> None:
            await self.bot.reply_to(
                message,
                "Пока я обрабатываю только текст. Пришлите вопрос обычным сообщением.",
            )

    @staticmethod
    def _session_key(message: Any) -> str:
        chat_id = getattr(getattr(message, "chat", None), "id", 0)
        user_id = getattr(getattr(message, "from_user", None), "id", 0)
        return build_session_key(peer_id=chat_id, from_id=user_id)

    async def _dispatch(self, message: Any) -> None:
        raw = str(getattr(message, "text", "") or "").strip()
        if not raw:
            await self.bot.reply_to(message, "Пустое сообщение. Напишите вопрос или /help.")
            return

        parts = raw.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if command in {"/start", "/help"}:
                await self._welcome(message)
            elif command == "/status":
                await self._status(message)
            elif command in {"/new", "/reset"}:
                await self._reset(message)
            elif command == "/search":
                if not rest:
                    await self.bot.reply_to(message, "Укажите вопрос после /search.")
                else:
                    await self._process_dialog(message, rest)
            elif command.startswith("/"):
                await self.bot.reply_to(
                    message,
                    "Неизвестная команда. Доступны /help, /status, /new и /search.",
                )
            else:
                await self._process_dialog(message, raw)
        except Exception as exc:
            logger.exception("Telegram dispatch failed: %s", exc)
            await self.bot.reply_to(message, f"Ошибка обработки запроса: {str(exc)[:500]}")

    async def _welcome(self, message: Any) -> None:
        await self.bot.reply_to(
            message,
            "MR Norm — поиск по нормативным документам 📚⚡\n\n"
            "Напишите вопрос обычным сообщением. Поиск выполняет Router2 по базе Qdrant, "
            "а ответ формируется по найденным фрагментам.\n\n"
            "Команды:\n"
            "• /search <вопрос> — задать вопрос\n"
            "• /new — начать новый диалог\n"
            "• /status — состояние системы\n"
            "• /help — эта справка\n\n"
            "Чтобы продолжить предыдущий вопрос, начните сообщение с точки: .а какой срок?",
        )

    async def _status(self, message: Any) -> None:
        keys = "найден" if self._keys_path else "не найден"
        await self.bot.reply_to(
            message,
            "Статус MR Norm Telegram:\n\n"
            f"• Qdrant: {os.getenv('MR_NORM_QDRANT_COLLECTION', 'mr_norm_docs_bge_m3')}\n"
            "• Основной контур: Router2\n"
            f"• Router: {os.getenv('MR_NORM_ROUTER2_ROUTER_MODEL', DEFAULT_ROUTER_MODEL)}\n"
            f"• Answer: {os.getenv('MR_NORM_ROUTER2_ANSWER_MODEL', DEFAULT_ANSWER_MODEL)}\n"
            f"• LLM keys: {keys}\n\n"
            "Готов к поиску ✅",
        )

    async def _reset(self, message: Any) -> None:
        key = self._session_key(message)
        self._dialog_sessions.reset(key)
        self._router2_pending.pop(key, None)
        await self.bot.reply_to(message, "Новый диалог. Задайте вопрос 🙂")

    def _ensure_config(self) -> IndexingConfig:
        if self._config is None:
            self._config = IndexingConfig.from_env()
        return self._config

    async def _run_router2(
        self,
        query: str,
        *,
        selected_option: str | None = None,
        prior_plan: dict[str, Any] | None = None,
    ) -> Router2Result:
        return await asyncio.to_thread(
            run_router2,
            query,
            router_model=os.getenv("MR_NORM_ROUTER2_ROUTER_MODEL", DEFAULT_ROUTER_MODEL),
            answer_model=os.getenv("MR_NORM_ROUTER2_ANSWER_MODEL", DEFAULT_ANSWER_MODEL),
            router_fallback_models=_env_models(
                "MR_NORM_ROUTER2_ROUTER_FALLBACK_MODELS", DEFAULT_ROUTER_FALLBACK_MODELS
            ),
            answer_fallback_models=_env_models(
                "MR_NORM_ROUTER2_ANSWER_FALLBACK_MODELS", DEFAULT_ANSWER_FALLBACK_MODELS
            ),
            config=self._ensure_config(),
            keys_path=self._keys_path,
            selected_option=selected_option,
            prior_plan=prior_plan,
        )

    async def _process_dialog(self, message: Any, raw: str) -> None:
        key = self._session_key(message)
        query, is_new, reset_only = parse_dialog_input(raw)
        lock = self._dialog_sessions.lock_for(key)
        async with lock:
            if reset_only:
                self._dialog_sessions.reset(key)
                self._router2_pending.pop(key, None)
                await self.bot.reply_to(message, "Новый диалог. Задайте вопрос 🙂")
                return

            pending = self._router2_pending.get(key)
            if is_new and pending is None:
                self._dialog_sessions.reset(key)
            context = self._dialog_sessions.get(key)
            active_context = context if context.turns and not is_new else None
            retrieval_query = build_retrieval_query(query, active_context)
            await self._process_search(
                message,
                query,
                session_key=key,
                stored_user_text=str(pending["question"]) if pending else query,
                retrieval_query=str(pending["question"]) if pending else retrieval_query,
            )

    async def _process_search(
        self,
        message: Any,
        query: str,
        *,
        session_key: str,
        stored_user_text: str,
        retrieval_query: str,
    ) -> None:
        await self.bot.reply_to(message, "Ищу релевантные фрагменты… 🔎")
        pending = self._router2_pending.get(session_key)
        if pending:
            options = list((pending["plan"].get("clarification") or {}).get("option_ids") or [])
            selected = query.strip()
            if selected.isdigit() and 1 <= int(selected) <= len(options):
                selected = str(options[int(selected) - 1])
            result = await self._run_router2(
                str(pending["question"]),
                selected_option=selected,
                prior_plan=pending["plan"],
            )
        else:
            result = await self._run_router2(retrieval_query or query)

        if result.clarification:
            self._router2_pending[session_key] = {
                "question": result.question,
                "plan": result.plan,
            }
            lines = [str(result.clarification.get("question") or "Уточните значение:")]
            for index, option in enumerate(result.clarification.get("option_ids") or [], 1):
                lines.append(f"{index}. {option}")
            await self.bot.reply_to(message, "\n".join(lines))
            return

        self._router2_pending.pop(session_key, None)
        response = _format_router2_result(result)
        if not response:
            await self.bot.reply_to(
                message,
                "Релевантные фрагменты не найдены. Попробуйте переформулировать вопрос.",
            )
            return

        if stored_user_text.strip() and result.answer.strip():
            refs = _source_references(result)
            doc_ids = {ref.doc_id for ref in refs if ref.doc_id}
            self._dialog_sessions.append_turn(
                session_key,
                user_text=stored_user_text,
                answer=result.answer,
                retrieval_query=retrieval_query,
                resolved_doc_id=next(iter(doc_ids)) if len(doc_ids) == 1 else "",
                source_references=refs,
            )

        chunks = _split_message("Ответ MR Norm:\n\n" + response)
        for index, chunk in enumerate(chunks, start=1):
            suffix = f"\n\n(часть {index} из {len(chunks)})" if len(chunks) > 1 else ""
            await self.bot.reply_to(message, (chunk + suffix)[:TELEGRAM_MESSAGE_LIMIT])

    async def run(self) -> None:
        logger.info("Telegram polling запущен")
        await self.bot.polling(non_stop=True, interval=1, timeout=20, request_timeout=30)


async def main_async() -> None:
    token = get_telegram_token_from_env_or_file()
    validate_telegram_token(token)
    bot = MRNormTelegramBot(token)
    try:
        me = await bot.bot.get_me()
        logger.info("Telegram bot connected: @%s", getattr(me, "username", "unknown"))
        await bot.run()
    finally:
        await bot.bot.close_session()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Telegram bot stopped")


if __name__ == "__main__":
    main()
