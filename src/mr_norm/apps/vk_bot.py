from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from vkbottle.api import API
from vkbottle.bot import Bot, Message
from vkbottle.polling.bot_polling import BotPolling

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_ROOT = PROJECT_ROOT / "sandbox"
if str(SANDBOX_ROOT) not in sys.path:
    sys.path.insert(0, str(SANDBOX_ROOT))

from mr_norm.apps.human_cli import MODE_PRESET_LABELS
from mr_norm.apps.vk_config import VKBotSettings, VKConfig
from mr_norm.apps.vk_longpoll_settings import ensure_longpoll_message_new
from mr_norm.apps.vk_token_file import get_community_token_from_env_or_file, oauth_user_token_url
from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.runtime.deep_research_memory import (
    DeepResearchSessionStore,
    format_candidates_prompt,
    parse_document_selection,
)
from mr_norm.runtime.dialog_memory import (
    DialogSourceReference,
    DialogSessionStore,
    build_retrieval_query,
    build_session_key,
    parse_dialog_input,
)
from mr_norm.skills.deep_research import (
    DeepResearchRequest,
    DeepResearchResult,
    build_pending_session,
    run_deep_research_broad,
    run_deep_research_deep_dive,
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

VK_ERROR_15_HELP = """
VK API: ошибка 15 (subcode 1133) — нет прав на groups.getLongPollServer.

Создайте ключ сообщества с правом «Управление сообществом» или задайте VK_USER_POLL_TOKEN
(пользовательский OAuth access token админа) в VK_token.txt.

Проверка: python -m mr_norm.apps.vk_token_probe
"""

VK_AUTH_ERROR_HELP = """
VK API: invalid access_token (код 5).

Получите пользовательский access_token через OAuth и добавьте в VK_token.txt:
VK_USER_POLL_TOKEN=...
"""


def _is_vk_error_15(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 15:
        return True
    return type(exc).__name__ == "VKAPIError_15"


def _is_vk_invalid_access_token(exc: BaseException) -> bool:
    if getattr(exc, "code", None) == 5:
        return True
    if type(exc).__name__ == "APIAuthError":
        return True
    low = str(exc).lower()
    return "invalid access_token" in low or "user authorization failed" in low


def _is_transient_connection_reset(exc: BaseException) -> bool:
    low = str(exc).lower()
    reason = getattr(exc, "reason", None)
    return bool(
        isinstance(exc, (ConnectionAbortedError, ConnectionResetError))
        or isinstance(reason, (ConnectionAbortedError, ConnectionResetError))
        or getattr(exc, "winerror", None) == 10054
        or getattr(reason, "winerror", None) == 10054
        or "10054" in low
        or "connection reset" in low
    )


def _make_api(token: str) -> API:
    ver = (os.getenv("VK_API_VERSION") or "").strip()
    if ver:

        class _V(API):
            API_VERSION = ver

        return _V(token)
    return API(token)


def _normalize_groups_get_by_id(response: object) -> list:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        groups = response.get("groups")
        if isinstance(groups, list):
            return groups
        if "id" in response:
            return [response]
    return []


class SplitPollBotPolling(BotPolling):
    """Long Poll через VK_USER_POLL_TOKEN, messages.send — токеном сообщества."""

    def __init__(
        self,
        api: API,
        *,
        poll_api: API,
        group_id: int | None = None,
        wait: int | None = None,
        rps_delay: int | None = None,
    ) -> None:
        super().__init__(api, group_id=group_id, wait=wait, rps_delay=rps_delay)
        self._poll_api = poll_api

    async def get_server(self) -> dict[str, Any]:
        if self.group_id is None:
            response = (await self.api.request("groups.getById", {}))["response"]
            groups = _normalize_groups_get_by_id(response)
            if not groups:
                raise RuntimeError(
                    "Не удалось определить group_id для Long Poll. Укажите VK_GROUP_ID (число)."
                )
            self.group_id = int(groups[0]["id"])
        return (
            await self._poll_api.request(
                "groups.getLongPollServer",
                {"group_id": self.group_id},
            )
        )["response"]


def _make_vk_bot(token: str) -> Bot:
    api = _make_api(token)
    gid_raw = (os.getenv("VK_GROUP_ID") or "").strip()
    if gid_raw.startswith("-"):
        gid_raw = gid_raw[1:]
    gid: int | None = int(gid_raw) if gid_raw.isdigit() else None
    if gid is not None:
        logger.info("Используется VK_GROUP_ID=%s для Long Poll", gid)

    poll_tok = (os.getenv("VK_USER_POLL_TOKEN") or "").strip().strip('"').strip("'")
    if poll_tok:
        reject = poll_tok.isdigit() or len(poll_tok) < 24
        if not reject and not poll_tok.startswith("vk1.") and len(poll_tok) < 80:
            reject = True
        if reject:
            poll_tok = ""
            logger.warning("VK_USER_POLL_TOKEN игнорируется — нужен полный OAuth user access token.")
            url = oauth_user_token_url()
            if url:
                logger.info("OAuth URL для user token: %s", url)
        if poll_tok:
            poll_api = _make_api(poll_tok)
            logger.info("VK_USER_POLL_TOKEN: Long Poll через пользовательский токен")
            polling = SplitPollBotPolling(api, poll_api=poll_api, group_id=gid)
            return Bot(api=api, polling=polling)

    if gid is not None:
        return Bot(api=api, polling=BotPolling(api, group_id=gid))
    return Bot(api=api)


def _plain(text: str) -> str:
    return (text or "").replace("**", "")


KNOWN_SLASH_COMMANDS = frozenset(
    {"/start", "/help", "/status", "/settings", "/mode", "/search"}
)


def parse_deep_research_query(raw: str) -> tuple[str | None, str]:
    """Extract deep-research query from `/ вопрос` or `/вопрос` forms.

    Returns:
        query: non-empty research question when input is a research prefix
        error: user-facing hint when input starts with `/` but is not a valid research query
    """
    text = (raw or "").strip()
    if not text.startswith("/"):
        return None, ""

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd in KNOWN_SLASH_COMMANDS:
        return None, ""

    if cmd == "/":
        if rest:
            return rest, ""
        return None, (
            "Укажите вопрос после /. "
            "Пример: /как оформляются диспетчерские заявки? "
            "или: / как оформляются диспетчерские заявки?"
        )

    query = text[1:].strip()
    if query:
        return query, ""
    return None, (
        "Укажите вопрос после /. "
        "Пример: /как оформляются диспетчерские заявки?"
    )


def _split_message(text: str, max_length: int) -> list[str]:
    if len(text) <= max_length:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(paragraph) > max_length:
            if current:
                parts.append(current.strip())
                current = ""
            for sentence in paragraph.split(". "):
                if len(current) + len(sentence) + 2 > max_length:
                    if current:
                        parts.append(current.strip())
                        current = sentence
                    else:
                        parts.append(sentence[:max_length])
                        current = sentence[max_length:]
                elif current:
                    current += ". " + sentence
                else:
                    current = sentence
        elif len(current) + len(paragraph) + 2 > max_length:
            if current:
                parts.append(current.strip())
            current = paragraph
        elif current:
            current += "\n\n" + paragraph
        else:
            current = paragraph
    if current:
        parts.append(current.strip())
    return parts


def _format_router2_for_vk(result: Router2Result) -> str:
    lines: list[str] = []
    answer = (result.answer or "").strip()
    if answer:
        lines.append(answer)
    if result.citations:
        lines.append("")
        lines.append("Источники:")
        for index, citation in enumerate(result.citations[:5], start=1):
            doc = str(citation.get("doc_name") or "—")
            point = str(citation.get("point_number") or "—")
            lines.append(f"{index}. п. {point} — {doc}")
    return _plain("\n".join(lines))


def _env_models(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return defaults
    return tuple(model.strip() for model in raw.split(",") if model.strip())


def _format_deep_research_broad_for_vk(result: DeepResearchResult) -> str:
    if result.stage == "no_evidence":
        return _plain(result.answer)
    if result.stage == "error":
        return _plain(result.answer)
    if result.stage != "awaiting_selection":
        return _plain(result.answer or "Глубокое исследование завершено.")
    header = "Глубокое исследование\n\n"
    if result.analysis and result.analysis.normalized_question:
        header += f"Вопрос: {result.analysis.normalized_question}\n\n"
    body = format_candidates_prompt(result.candidates)
    return _plain(header + body)


def _format_deep_research_memo_for_vk(result: DeepResearchResult) -> str:
    lines: list[str] = ["Аналитическая записка\n"]
    if result.analysis and result.analysis.normalized_question:
        lines.append(f"Тема: {result.analysis.normalized_question}\n")
    answer = (result.answer or "").strip()
    if answer:
        lines.append(answer)
    if result.citations:
        lines.append("")
        lines.append("Источники:")
        for index, citation in enumerate(result.citations[:10], start=1):
            doc = citation.doc_name or "—"
            point = citation.point_number or "—"
            lines.append(f"{index}. п. {point} — {doc}")
    return _plain("\n".join(lines))


def _research_llm_provider(mode_preset: str) -> str:
    if mode_preset == "ollama":
        return "ollama"
    return "polza"


def _install_vk_longpoll_update_logging() -> None:
    flag = os.environ.get("VK_BOT_DEBUG_UPDATES", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    try:
        from vkbottle.dispatch.base import Router

        original = Router.route

        async def wrapped(self, event, ctx_api):
            try:
                if isinstance(event, dict):
                    logger.info(
                        "VK Long Poll update: type=%r object_keys=%s",
                        event.get("type"),
                        list((event.get("object") or {}).keys())
                        if isinstance(event.get("object"), dict)
                        else type(event.get("object")).__name__,
                    )
            except Exception:
                logger.exception("VK_BOT_DEBUG_UPDATES: ошибка логирования update")
            return await original(self, event, ctx_api)

        Router.route = wrapped  # type: ignore[method-assign]
        logger.info("VK_BOT_DEBUG_UPDATES: включено логирование сырых update")
    except Exception:
        logger.exception("Не удалось установить VK_BOT_DEBUG_UPDATES hook")


class MRNormVKBot:
    """VK-бот для MR Norm (Router2 production pipeline)."""

    def __init__(self, token: str) -> None:
        self.bot = _make_vk_bot(token)
        self.settings = VKBotSettings()
        self._config: IndexingConfig | None = None
        self._paths = ProjectPaths.from_root()
        self._keys_path = self._paths.root / "keys"
        self._dialog_sessions = DialogSessionStore()
        self._research_sessions = DeepResearchSessionStore()
        self._router2_pending: dict[str, dict[str, Any]] = {}
        if not self._keys_path.is_file():
            self._keys_path = None

        gid_raw = (os.getenv("VK_GROUP_ID") or "").strip().lstrip("-")
        if gid_raw.isdigit():
            try:
                ensure_longpoll_message_new(token, int(gid_raw))
            except Exception as exc:
                logger.warning("Проверка Long Poll (message_new): %s", exc)

        self._setup_handlers()
        logger.info("MR Norm VK бот инициализирован")

    def _setup_handlers(self) -> None:
        @self.bot.on.message()
        async def route(message: Message) -> None:
            await self._dispatch(message)

    @staticmethod
    async def _vk_answer(message: Message, *args: Any, **kwargs: Any) -> Any:
        for attempt in range(3):
            try:
                return await message.answer(*args, **kwargs)
            except Exception as exc:
                if attempt < 2 and _is_transient_connection_reset(exc):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                logger.exception(
                    "VK messages.send не выполнен (peer_id=%s from_id=%s): %s",
                    getattr(message, "peer_id", None),
                    getattr(message, "from_id", None),
                    exc,
                )
                raise

    async def _dispatch(self, message: Message) -> None:
        raw = (message.text or "").strip()
        logger.info(
            "VK сообщение: peer_id=%s from_id=%s out=%s text_len=%s",
            message.peer_id,
            message.from_id,
            getattr(message, "out", None),
            len(raw),
        )
        if getattr(message, "out", False):
            return
        if not raw:
            if message.attachments:
                await self._vk_answer(
                    message,
                    "Пока обрабатываю только текстовые запросы. Напишите вопрос текстом.",
                )
            else:
                await self._vk_answer(
                    message,
                    "Пустое сообщение. Напишите вопрос или /help.",
                )
            return

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        try:
            if cmd in ("/start", "/help"):
                await self._cmd_welcome(message)
            elif cmd == "/status":
                await self._cmd_status(message)
            elif cmd == "/settings":
                await self._cmd_settings(message)
            elif cmd == "/set_limit":
                await self._vk_answer(
                    message,
                    _plain("Команда /set_limit удалена: Router2 сам управляет объёмом поиска."),
                )
            elif cmd == "/mode":
                await self._cmd_set_mode(message, rest)
            elif cmd == "/search":
                if not rest.strip():
                    await self._vk_answer(
                        message,
                        _plain("Укажите запрос. Пример: /search требования к диспетчерскому центру"),
                    )
                    return
                await self._process_dialog_message(message, rest.strip())
            elif cmd.startswith("/"):
                research_query, research_error = parse_deep_research_query(raw)
                if research_query:
                    await self._process_deep_research_start(message, research_query)
                elif research_error:
                    await self._vk_answer(message, _plain(research_error))
                else:
                    await self._vk_answer(
                        message,
                        _plain(
                            "Неизвестная команда. Доступны: /help, /status, /settings, /search, /mode"
                        ),
                    )
            else:
                await self._process_dialog_message(message, raw)
        except Exception as exc:
            logger.exception("Ошибка dispatch: %s", exc)
            await self._vk_answer(message, _plain(f"Ошибка: {exc}")[: VKConfig.MAX_MESSAGE_LENGTH])

    async def _cmd_welcome(self, message: Message) -> None:
        text = _plain(
            "MR Norm — поиск по нормативным документам\n\n"
            "Отправьте вопрос текстом или используйте /search <вопрос>.\n\n"
            "Команды:\n"
            "• /help — эта справка\n"
            "• /status — статус системы\n"
            "• /settings — текущие настройки\n"
            "• /mode <1|2|3> — модель для глубокого исследования\n\n"
            "Глубокое исследование:\n"
            "• / <вопрос> или /<вопрос> — широкий поиск, выбор документов, аналитическая записка\n"
            "  Пример: /как оформляются диспетчерские заявки?\n"
            "  Пример: / требования к хранению проектной документации\n\n"
            "Диалог:\n"
            "• .вопрос — продолжить текущий диалог\n"
            "• сообщение без точки — начать новый диалог\n"
            "• . — очистить диалог\n\n"
            "Пример: Какие требования к хранению проектной документации?"
        )
        await self._vk_answer(message, text)

    async def _cmd_status(self, message: Message) -> None:
        config_ok = "активна" if self._config is not None else "будет инициализирована при первом запросе"
        keys_ok = "найден" if self._keys_path else "не найден (keys в корне проекта)"
        text = _plain(
            "Статус MR Norm VK:\n\n"
            f"• Qdrant collection: {os.getenv('MR_NORM_QDRANT_COLLECTION', 'mr_norm_docs_bge_m3')}\n"
            f"• Конфиг индекса: {config_ok}\n"
            f"• LLM keys: {keys_ok}\n"
            "• Основной контур: Router2\n"
            f"• Router: {os.getenv('MR_NORM_ROUTER2_ROUTER_MODEL', DEFAULT_ROUTER_MODEL)}\n"
            f"• Answer: {os.getenv('MR_NORM_ROUTER2_ANSWER_MODEL', DEFAULT_ANSWER_MODEL)}\n\n"
            "Готов к поиску."
        )
        await self._vk_answer(message, text)

    async def _cmd_settings(self, message: Message) -> None:
        text = _plain(
            "Настройки:\n\n"
            "• Обычные вопросы: Router2 + Qdrant + финальная LLM\n"
            f"• Router: {os.getenv('MR_NORM_ROUTER2_ROUTER_MODEL', DEFAULT_ROUTER_MODEL)}\n"
            f"• Answer: {os.getenv('MR_NORM_ROUTER2_ANSWER_MODEL', DEFAULT_ANSWER_MODEL)}\n"
            f"• Глубокое исследование: {MODE_PRESET_LABELS.get(self.settings.mode_preset, '—')}"
        )
        await self._vk_answer(message, text)

    async def _cmd_set_mode(self, message: Message, rest: str) -> None:
        mapping = {"1": "deterministic", "2": "ollama", "3": "polza"}
        try:
            key = rest.strip().split()[0]
            preset = mapping.get(key)
            if not preset:
                await self._vk_answer(message, _plain("Укажите /mode 1, /mode 2 или /mode 3"))
                return
            self.settings.mode_preset = preset
            await self._vk_answer(
                message,
                _plain(f"Режим: {preset} ({MODE_PRESET_LABELS.get(preset, preset)})"),
            )
        except IndexError:
            await self._vk_answer(message, _plain("Пример: /mode 3"))

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
        config = self._ensure_config()
        return await asyncio.to_thread(
            run_router2,
            query,
            router_model=os.getenv("MR_NORM_ROUTER2_ROUTER_MODEL", DEFAULT_ROUTER_MODEL),
            answer_model=os.getenv("MR_NORM_ROUTER2_ANSWER_MODEL", DEFAULT_ANSWER_MODEL),
            router_fallback_models=_env_models(
                "MR_NORM_ROUTER2_ROUTER_FALLBACK_MODELS", DEFAULT_ROUTER_FALLBACK_MODELS,
            ),
            answer_fallback_models=_env_models(
                "MR_NORM_ROUTER2_ANSWER_FALLBACK_MODELS", DEFAULT_ANSWER_FALLBACK_MODELS,
            ),
            config=config,
            keys_path=self._keys_path,
            selected_option=selected_option,
            prior_plan=prior_plan,
        )

    async def _run_deep_research_broad(self, query: str) -> DeepResearchResult:
        config = self._ensure_config()
        request = DeepResearchRequest(
            query=query,
            llm_provider=_research_llm_provider(self.settings.mode_preset),
            profile="deep",
            broad_limit=40,
        )
        return await asyncio.to_thread(
            run_deep_research_broad,
            request,
            config,
            keys_path=self._keys_path,
        )

    async def _run_deep_research_deep_dive(
        self,
        session,
        selected_indices: tuple[int, ...],
    ) -> DeepResearchResult:
        config = self._ensure_config()
        return await asyncio.to_thread(
            run_deep_research_deep_dive,
            session,
            selected_indices,
            config,
            llm_provider=_research_llm_provider(self.settings.mode_preset),
            profile="deep",
            keys_path=self._keys_path,
        )

    async def _send_long_text(self, message: Message, *, header: str, body: str) -> None:
        hard = 4096
        parts = _split_message(body, max(hard - len(header) - 80, 500))
        first = (header + parts[0])[:hard]
        if len(parts) > 1:
            first += f"\n\n(часть 1 из {len(parts)})"
        await self._vk_answer(message, first)
        for index, part in enumerate(parts[1:], start=2):
            chunk = f"Часть {index} из {len(parts)}:\n\n{part}"
            await self._vk_answer(message, chunk[:hard])
            await asyncio.sleep(0.5)

    async def _process_deep_research_start(self, message: Message, query: str) -> None:
        session_key = build_session_key(
            peer_id=getattr(message, "peer_id", 0),
            from_id=getattr(message, "from_id", 0),
        )
        lock = self._research_sessions.lock_for(session_key)
        async with lock:
            self._research_sessions.clear(session_key)
            await self._vk_answer(message, "Запускаю глубокое исследование: анализ вопроса и первичный поиск...")
            try:
                result = await self._run_deep_research_broad(query)
            except Exception as exc:
                logger.exception("deep_research_start: %s", exc)
                await self._vk_answer(message, _plain(f"Ошибка глубокого исследования: {str(exc)[:500]}"))
                return

            if result.stage == "awaiting_selection":
                pending = build_pending_session(result, session_key=session_key)
                if pending is not None:
                    self._research_sessions.set(pending)

            response = _format_deep_research_broad_for_vk(result)
            if not response.strip():
                await self._vk_answer(message, _plain("Не удалось сформировать результат исследования."))
                return
            await self._send_long_text(message, header="", body=response)

    async def _process_research_selection(self, message: Message, raw: str) -> None:
        session_key = build_session_key(
            peer_id=getattr(message, "peer_id", 0),
            from_id=getattr(message, "from_id", 0),
        )
        lock = self._research_sessions.lock_for(session_key)
        async with lock:
            pending = self._research_sessions.get(session_key)
            if pending is None:
                await self._process_dialog_message(message, raw)
                return

            selected_indices, error = parse_document_selection(
                raw,
                max_index=len(pending.candidates),
            )
            if error:
                await self._vk_answer(
                    message,
                    _plain(f"{error}\n\n{format_candidates_prompt(pending.candidates)}"),
                )
                return

            await self._vk_answer(
                message,
                f"Углублённый анализ по документам: {', '.join(str(index) for index in selected_indices)}...",
            )
            try:
                result = await self._run_deep_research_deep_dive(pending, selected_indices)
            except Exception as exc:
                logger.exception("deep_research_selection: %s", exc)
                await self._vk_answer(message, _plain(f"Ошибка углублённого анализа: {str(exc)[:500]}"))
                return

            if result.stage == "completed":
                self._research_sessions.clear(session_key)

            response = _format_deep_research_memo_for_vk(result)
            if result.stage != "completed":
                response = _format_deep_research_broad_for_vk(result) if result.stage == "awaiting_selection" else _plain(result.answer)
            if not response.strip():
                await self._vk_answer(message, _plain("Не удалось сформировать аналитическую записку."))
                return
            await self._send_long_text(message, header="", body=response)

    async def _process_dialog_message(self, message: Message, raw: str) -> None:
        session_key = build_session_key(
            peer_id=getattr(message, "peer_id", 0),
            from_id=getattr(message, "from_id", 0),
        )
        research_sessions = getattr(self, "_research_sessions", None)
        query, is_new_dialog, reset_only = parse_dialog_input(raw)

        if reset_only:
            lock = self._dialog_sessions.lock_for(session_key)
            async with lock:
                self._dialog_sessions.reset(session_key)
                getattr(self, "_router2_pending", {}).pop(session_key, None)
                if research_sessions is not None:
                    research_sessions.clear(session_key)
                await self._vk_answer(message, _plain("Новый диалог. Задайте вопрос."))
            return

        if (
            research_sessions is not None
            and research_sessions.has_pending(session_key)
        ):
            await self._process_research_selection(message, query)
            return

        router2_pending = getattr(self, "_router2_pending", {}).get(session_key)
        if router2_pending is not None:
            lock = self._dialog_sessions.lock_for(session_key)
            async with lock:
                await self._process_search(
                    message,
                    query,
                    session_key=session_key,
                    stored_user_text=str(router2_pending["question"]),
                    retrieval_query=str(router2_pending["question"]),
                )
            return

        if is_new_dialog and research_sessions is not None:
            research_sessions.clear(session_key)

        lock = self._dialog_sessions.lock_for(session_key)
        async with lock:
            if is_new_dialog:
                self._dialog_sessions.reset(session_key)
                getattr(self, "_router2_pending", {}).pop(session_key, None)
            dialog_context = self._dialog_sessions.get(session_key)
            active_context = dialog_context if dialog_context.turns else None
            if not query.strip():
                await self._vk_answer(
                    message,
                    _plain("Пустое сообщение. Напишите вопрос или начните новый диалог через ."),
                )
                return
            await self._process_search(
                message,
                query,
                dialog_context=active_context,
                session_key=session_key,
                stored_user_text=query,
                retrieval_query=build_retrieval_query(query, active_context),
            )

    async def _process_search(
        self,
        message: Message,
        query: str,
        *,
        dialog_context=None,
        session_key: str = "",
        stored_user_text: str = "",
        retrieval_query: str = "",
    ) -> None:
        hard = 4096
        try:
            await self._vk_answer(message, "Ищу релевантные фрагменты...")
            if not hasattr(self, "_router2_pending"):
                self._router2_pending = {}
            pending_store = self._router2_pending
            pending = pending_store.get(session_key) if session_key else None
            if pending is not None:
                options = list((pending["plan"].get("clarification") or {}).get("option_ids") or [])
                selected_option = query.strip()
                if selected_option.isdigit() and 1 <= int(selected_option) <= len(options):
                    selected_option = str(options[int(selected_option) - 1])
                result = await self._run_router2(
                    str(pending["question"]),
                    selected_option=selected_option,
                    prior_plan=pending["plan"],
                )
            else:
                result = await self._run_router2(retrieval_query or query)

            if result.clarification:
                if session_key:
                    pending_store[session_key] = {
                        "question": result.question,
                        "plan": result.plan,
                    }
                lines = [str(result.clarification.get("question") or "Уточните значение:")]
                for index, option in enumerate(result.clarification.get("option_ids") or [], 1):
                    lines.append(f"{index}. {option}")
                await self._vk_answer(message, _plain("\n".join(lines)))
                return
            if session_key:
                pending_store.pop(session_key, None)

            logger.info(
                "Router2 status=%s router_model=%s answer_model=%s calls=%s warnings=%s",
                result.status,
                result.router_model_used,
                result.answer_model_used,
                len(result.calls),
                len(result.warnings),
            )
            response = _format_router2_for_vk(result)
            if not response.strip():
                await self._vk_answer(
                    message,
                    _plain("Релевантные фрагменты не найдены. Попробуйте переформулировать вопрос."),
                )
                return

            if session_key and stored_user_text.strip() and result.answer.strip():
                evidence_by_chunk = {
                    str(item.get("chunk_id") or ""): item
                    for item in result.evidence if item.get("chunk_id")
                }
                source_references_by_key: dict[tuple[str, str], DialogSourceReference] = {}
                for citation in result.citations:
                    evidence_item = evidence_by_chunk.get(str(citation.get("chunk_id") or ""), {})
                    doc_id = str(evidence_item.get("doc_id") or "").strip()
                    doc_name = str(citation.get("doc_name") or evidence_item.get("doc_name") or "").strip()
                    point_number = str(citation.get("point_number") or evidence_item.get("point_number") or "").strip()
                    if point_number and point_number not in {"—", "-"} and (doc_id or doc_name):
                        reference = DialogSourceReference(doc_id, doc_name, point_number)
                        source_references_by_key[(doc_id or doc_name, point_number)] = reference
                for evidence_item in result.evidence:
                    doc_id = str(evidence_item.get("doc_id") or "").strip()
                    doc_name = str(evidence_item.get("doc_name") or "").strip()
                    point_number = str(evidence_item.get("point_number") or "").strip()
                    if point_number and point_number not in {"—", "-"} and (doc_id or doc_name):
                        reference = DialogSourceReference(doc_id, doc_name, point_number)
                        source_references_by_key[(doc_id or doc_name, point_number)] = reference
                source_references = tuple(source_references_by_key.values())
                resolved_doc_ids = {ref.doc_id for ref in source_references if ref.doc_id}
                resolved_doc_id = next(iter(resolved_doc_ids)) if len(resolved_doc_ids) == 1 else ""
                self._dialog_sessions.append_turn(
                    session_key,
                    user_text=stored_user_text.strip(),
                    answer=result.answer,
                    retrieval_query=retrieval_query or stored_user_text.strip(),
                    prepared_plan=None,
                    resolved_doc_id=resolved_doc_id,
                    source_references=source_references,
                )

            header = "Ответ MR Norm:\n\n"
            parts = _split_message(response, max(hard - len(header) - 80, 500))
            first = (header + parts[0])[:hard]
            if len(parts) > 1:
                first += f"\n\n(часть 1 из {len(parts)})"
            await self._vk_answer(message, first)

            for index, part in enumerate(parts[1:], start=2):
                chunk = f"Часть {index} из {len(parts)}:\n\n{part}"
                await self._vk_answer(message, chunk[:hard])
                await asyncio.sleep(0.5)

            logger.info("VK ответ from_id=%s, частей=%s", message.from_id, len(parts))
        except Exception as exc:
            logger.exception("process_search: %s", exc)
            await self._vk_answer(message, _plain(f"Ошибка поиска: {str(exc)[:500]}"))

    def run(self) -> None:
        logger.info("VK Long Poll запущен. Ожидание сообщений...")
        try:
            self.bot.run_forever()
        except BaseException as exc:
            if _is_vk_error_15(exc):
                print(VK_ERROR_15_HELP, file=sys.stderr)
            elif _is_vk_invalid_access_token(exc):
                print(VK_AUTH_ERROR_HELP, file=sys.stderr)
            raise


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _configure_env_defaults() -> None:
    os.environ.setdefault("QDRANT_HOST", "localhost")
    os.environ.setdefault("QDRANT_PORT", "6333")
    os.environ.setdefault("MR_NORM_QDRANT_COLLECTION", "mr_norm_docs_bge_m3")
    os.environ.setdefault("RAG_EMBEDDING_DEVICE", "cpu")


def _reexec_under_python_exe_if_pythonw() -> None:
    if sys.platform != "win32":
        return
    exe = (sys.executable or "").lower()
    if not exe.endswith("pythonw.exe"):
        return
    here = Path(__file__).resolve()
    py = Path(sys.executable).with_name("python.exe")
    if not py.is_file():
        return
    code = subprocess.call([str(py), "-u", "-m", "mr_norm.apps.vk_bot", *sys.argv[1:]])
    raise SystemExit(code)


def main() -> None:
    _configure_logging()
    _configure_env_defaults()
    token = get_community_token_from_env_or_file()
    if not token:
        print("Не указан токен VK.", flush=True)
        print("Задайте VK_BOT_TOKEN или положите токен в VK_token.txt в корне проекта.", flush=True)
        sys.exit(1)

    print("Запуск MR Norm VK бота...", flush=True)
    print("Команды: /help, /status, /search", flush=True)
    print("Остановка: Ctrl+C", flush=True)
    print("-" * 50, flush=True)

    _install_vk_longpoll_update_logging()
    try:
        MRNormVKBot(token).run()
    except KeyboardInterrupt:
        print("\nVK бот остановлен")
    except BaseException as exc:
        if _is_vk_error_15(exc):
            print(VK_ERROR_15_HELP, file=sys.stderr)
        elif _is_vk_invalid_access_token(exc):
            print(VK_AUTH_ERROR_HELP, file=sys.stderr)
        logger.exception("main: %s", exc)
        print(f"Ошибка: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    _reexec_under_python_exe_if_pythonw()
    main()
