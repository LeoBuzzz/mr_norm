"""Keep the production MR Norm VK and Telegram transports running on Windows."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import time
from typing import Iterable

import psutil


PROJECT_DIR = Path(__file__).resolve().parents[3]
PYTHON_EXE = PROJECT_DIR / "venv312" / "Scripts" / "python.exe"
LOG_DIR = PROJECT_DIR / "tmp" / "service_bot"
SERVICE_LOG = LOG_DIR / "service_bot.log"
MUTEX_NAME = r"Global\MRNormServiceBot"
DEFAULT_INTERVAL_SECONDS = 60
RESTART_MARKER = LOG_DIR / "restart_bots.once"


@dataclass(frozen=True)
class BotSpec:
    name: str
    module: str
    log_name: str


BOT_SPECS = (
    BotSpec("VK", "mr_norm.apps.vk_bot", "vk_bot.log"),
    BotSpec("Telegram", "mr_norm.apps.telegram_bot", "telegram_bot.log"),
)


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        SERVICE_LOG,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[handler],
        force=True,
    )
    return logging.getLogger("mr_norm.service_bot")


def acquire_single_instance() -> object | bool | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    return (kernel32, handle)


def command_runs_module(command_line: Iterable[str], module: str) -> bool:
    parts = list(command_line)
    return any(
        parts[index] == "-m"
        and index + 1 < len(parts)
        and parts[index + 1] == module
        for index in range(len(parts))
    )


def bot_is_running(spec: BotSpec) -> bool:
    for process in psutil.process_iter(["cmdline"]):
        try:
            command_line = process.info["cmdline"] or []
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if command_runs_module(command_line, spec.module):
            return True
    return False


def restart_bots_if_requested(logger: logging.Logger) -> None:
    """Restart transports from the scheduled-task security context when requested."""
    if not RESTART_MARKER.is_file():
        return
    targets = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            command_line = process.info["cmdline"] or []
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if any(command_runs_module(command_line, spec.module) for spec in BOT_SPECS):
            targets.append(process)

    logger.warning("Запрошен управляемый перезапуск ботов; процессов: %s", len(targets))
    for process in targets:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            logger.exception("Не удалось остановить PID=%s", process.pid)
    _, alive = psutil.wait_procs(targets, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            logger.exception("Не удалось принудительно остановить PID=%s", process.pid)
    RESTART_MARKER.unlink(missing_ok=True)
    time.sleep(2)


def _bot_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "QDRANT_HOST": "localhost",
            "QDRANT_PORT": "6333",
            "MR_NORM_QDRANT_COLLECTION": "mr_norm_docs_bge_m3_rtf_v2_20261011",
            "RAG_EMBEDDING_DEVICE": "cpu",
            "MR_NORM_ENABLE_TAIL_POINT_FALLBACK": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def start_bot(spec: BotSpec, logger: logging.Logger) -> int:
    if not PYTHON_EXE.is_file():
        raise FileNotFoundError(f"Интерпретатор проекта не найден: {PYTHON_EXE}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    output_path = LOG_DIR / spec.log_name
    output = output_path.open("a", encoding="utf-8", buffering=1)
    try:
        process = subprocess.Popen(
            [str(PYTHON_EXE), "-u", "-m", spec.module],
            cwd=PROJECT_DIR,
            env=_bot_environment(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        output.close()
    logger.info("%s-бот запущен, PID=%s", spec.name, process.pid)
    return process.pid


def check_and_start_bots(logger: logging.Logger) -> None:
    for spec in BOT_SPECS:
        if bot_is_running(spec):
            logger.info("%s-бот уже работает.", spec.name)
            continue
        logger.warning("Процесс %s-бота не найден; запускаю.", spec.name)
        start_bot(spec, logger)


def main() -> int:
    logger = configure_logging()
    if os.name != "nt":
        logger.error("Этот сервис рассчитан на Windows.")
        return 2

    mutex = acquire_single_instance()
    if mutex is False:
        logger.info("Другой экземпляр сервисного бота уже работает; выход.")
        return 0

    interval = max(
        10,
        int(os.getenv("MR_NORM_SERVICE_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))),
    )
    logger.info(
        "Сервис запущен. Контролируемые боты: %s. Интервал: %s секунд.",
        ", ".join(spec.name for spec in BOT_SPECS),
        interval,
    )
    restart_bots_if_requested(logger)
    while True:
        try:
            check_and_start_bots(logger)
        except Exception:
            logger.exception("Ошибка проверки или запуска ботов.")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
