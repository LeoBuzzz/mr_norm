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

from mr_norm.apps.human_cli import (
    HumanCliOptions,
    MODE_PRESET_LABELS,
    build_norm_lookup_request,
)
from mr_norm.apps.vk_config import VKBotSettings, VKConfig
from mr_norm.apps.vk_longpoll_settings import ensure_longpoll_message_new
from mr_norm.apps.vk_token_file import get_community_token_from_env_or_file, oauth_user_token_url
from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.skills.norm_lookup import NormLookupResult, run_norm_lookup

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


def _format_norm_lookup_for_vk(result: NormLookupResult) -> str:
    lines: list[str] = []
    answer = (result.answer or "").strip()
    if answer:
        lines.append(answer)
    if result.citations:
        lines.append("")
        lines.append("Источники:")
        for index, citation in enumerate(result.citations[:5], start=1):
            doc = citation.doc_name or "—"
            point = citation.point_number or "—"
            lines.append(f"{index}. п. {point} — {doc}")
    return _plain("\n".join(lines))


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
    """VK-бот для MR Norm (norm_lookup pipeline)."""

    def __init__(self, token: str) -> None:
        self.bot = _make_vk_bot(token)
        self.settings = VKBotSettings()
        self._config: IndexingConfig | None = None
        self._paths = ProjectPaths.from_root()
        self._keys_path = self._paths.root / "keys"
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
        try:
            return await message.answer(*args, **kwargs)
        except Exception as exc:
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
                await self._cmd_set_limit(message, rest)
            elif cmd == "/mode":
                await self._cmd_set_mode(message, rest)
            elif cmd == "/search":
                if not rest.strip():
                    await self._vk_answer(
                        message,
                        _plain("Укажите запрос. Пример: /search требования к диспетчерскому центру"),
                    )
                    return
                await self._process_search(message, rest.strip())
            elif cmd.startswith("/"):
                await self._vk_answer(
                    message,
                    _plain("Неизвестная команда. Доступны: /help, /status, /settings, /search, /set_limit, /mode"),
                )
            else:
                await self._process_search(message, raw)
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
            "• /set_limit <1–40> — лимит фрагментов\n"
            "• /mode <1|2|3> — режим: 1 deterministic, 2 ollama, 3 polza\n\n"
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
            f"• Режим: {MODE_PRESET_LABELS.get(self.settings.mode_preset, self.settings.mode_preset)}\n"
            f"• limit={self.settings.limit}, profile={self.settings.profile}\n\n"
            "Готов к поиску."
        )
        await self._vk_answer(message, text)

    async def _cmd_settings(self, message: Message) -> None:
        text = _plain(
            "Настройки:\n\n"
            f"• limit: {self.settings.limit}\n"
            f"• profile: {self.settings.profile}\n"
            f"• mode: {self.settings.mode_preset} "
            f"({MODE_PRESET_LABELS.get(self.settings.mode_preset, '—')})\n\n"
            "Изменить: /set_limit 10, /mode 3"
        )
        await self._vk_answer(message, text)

    async def _cmd_set_limit(self, message: Message, rest: str) -> None:
        try:
            value = rest.strip().split()[0]
            count = int(value)
            if 1 <= count <= 40:
                self.settings.limit = count
                await self._vk_answer(message, _plain(f"Лимит фрагментов: {count}"))
            else:
                await self._vk_answer(message, _plain("Число должно быть от 1 до 40"))
        except (IndexError, ValueError):
            await self._vk_answer(message, _plain("Пример: /set_limit 10"))

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

    async def _run_norm_lookup(self, query: str) -> NormLookupResult:
        options = HumanCliOptions(
            query=query,
            mode_preset=self.settings.mode_preset,
            no_doc_filter=True,
            limit=self.settings.limit,
            profile=self.settings.profile,
        )
        request = build_norm_lookup_request(options)
        config = self._ensure_config()
        return await asyncio.to_thread(
            run_norm_lookup,
            request,
            config,
            keys_path=self._keys_path,
            project_paths=self._paths,
        )

    async def _process_search(self, message: Message, query: str) -> None:
        hard = 4096
        try:
            await self._vk_answer(message, "Ищу релевантные фрагменты...")
            result = await self._run_norm_lookup(query)
            response = _format_norm_lookup_for_vk(result)
            if not response.strip():
                await self._vk_answer(
                    message,
                    _plain("Релевантные фрагменты не найдены. Попробуйте переформулировать вопрос."),
                )
                return

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
