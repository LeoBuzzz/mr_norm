from __future__ import annotations

from dataclasses import dataclass


class VKConfig:
    """Конфигурация VK (сообщество / Long Poll)."""

    MAX_MESSAGE_LENGTH = 4000
    TYPING_DELAY = 1.0


@dataclass
class VKBotSettings:
    """Настройки ответа бота (на пользователя / глобально)."""

    limit: int = 10
    profile: str = "balanced"
    mode_preset: str = "polza"
