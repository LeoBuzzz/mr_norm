from __future__ import annotations

import os
import re
from pathlib import Path

from mr_norm.config.paths import find_project_root

_KV_LINE = re.compile(
    r"^(VK_GROUP_ID|VK_USER_POLL_TOKEN|VK_API_VERSION|VK_APP_ID|VK_SERVICE_TOKEN|VK_APP_SERVICE_TOKEN|"
    r"VK_PROTECTED_KEY|VK_APP_PROTECTED_KEY|VK_USER_ID)\s*=\s*(.+)$"
)
_ID_LINE = re.compile(r"^ID:\s*(\d+)\s*$", re.IGNORECASE)


def default_token_path() -> Path:
    return find_project_root() / "VK_token.txt"


def _strip_secret_val(val: str) -> str:
    s = val.strip().strip('"').strip("'")
    if "access_token=" in s:
        part = s.split("access_token=", 1)[1]
        s = part.split("&", 1)[0].strip()
    return s


def apply_vk_token_file(path: Path | None = None) -> str:
    """
    Читает VK_token.txt, подставляет в os.environ отсутствующие KEY=VALUE.
    Возвращает токен сообщества — первая непустая строка, не распознанная как KEY=VALUE.
    """
    base = path or default_token_path()
    raw = base.read_text(encoding="utf-8")
    community = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m_id = _ID_LINE.match(s)
        if m_id:
            os.environ.setdefault("VK_APP_ID", m_id.group(1))
            continue
        m = _KV_LINE.match(s)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "VK_USER_POLL_TOKEN":
                val = _strip_secret_val(val)
            if val:
                if key in ("VK_SERVICE_TOKEN", "VK_APP_SERVICE_TOKEN"):
                    os.environ.setdefault("VK_SERVICE_TOKEN", val)
                    os.environ.setdefault("VK_APP_SERVICE_TOKEN", val)
                elif key in ("VK_PROTECTED_KEY", "VK_APP_PROTECTED_KEY"):
                    os.environ.setdefault("VK_PROTECTED_KEY", val)
                    os.environ.setdefault("VK_APP_PROTECTED_KEY", val)
                else:
                    os.environ.setdefault(key, val)
            continue
        if not community:
            community = s
    return community.strip()


def get_community_token_from_env_or_file() -> str:
    token = (os.getenv("VK_BOT_TOKEN") or os.getenv("VK_TOKEN") or "").strip()
    if token:
        return token
    path = default_token_path()
    try:
        return apply_vk_token_file(path)
    except FileNotFoundError:
        return ""


def oauth_user_token_url() -> str:
    app_id = (os.getenv("VK_APP_ID") or "").strip()
    if not app_id:
        return ""
    return (
        "https://oauth.vk.com/authorize?"
        f"client_id={app_id}&display=page&redirect_uri=https://oauth.vk.com/blank.html"
        "&scope=offline,groups&response_type=token&v=5.199"
    )
