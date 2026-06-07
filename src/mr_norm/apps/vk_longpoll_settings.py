from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

API = "https://api.vk.com/method/"


def _post(method: str, token: str, **params: str | int) -> dict:
    ver = (os.getenv("VK_API_VERSION") or "5.199").strip()
    data = {"access_token": token, "v": ver, **{k: str(v) for k, v in params.items()}}
    response = requests.post(API + method, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def ensure_longpoll_message_new(token: str, group_id: int) -> None:
    """Включает Long Poll message_new, если событие выключено."""
    try:
        payload = _post("groups.getLongPollSettings", token, group_id=group_id)
    except Exception as exc:
        logger.warning("groups.getLongPollSettings не вызван: %s", exc)
        return
    if "error" in payload:
        logger.warning("groups.getLongPollSettings: %s", payload.get("error"))
        return
    resp = payload.get("response") or {}
    version = resp.get("api_version") or resp.get("version")
    if version is not None:
        logger.info("Long Poll settings: api_version=%s is_enabled=%s", version, resp.get("is_enabled"))
    events = resp.get("events") or {}
    if events.get("message_new") in (True, 1, "1"):
        logger.info(
            "Long Poll: событие message_new включено (is_enabled=%s)",
            resp.get("is_enabled"),
        )
        return
    logger.warning(
        "Long Poll: событие message_new выключено — пробую groups.setLongPollSettings(..., message_new=1)."
    )
    payload2 = _post(
        "groups.setLongPollSettings",
        token,
        group_id=group_id,
        enabled=1,
        message_new=1,
    )
    if "error" in payload2:
        logger.warning(
            "Не удалось включить message_new: %s. "
            "Включите вручную в настройках сообщества → Работа с API → Long Poll API.",
            payload2.get("error"),
        )
        return
    logger.info("Long Poll: включено событие message_new (groups.setLongPollSettings).")
