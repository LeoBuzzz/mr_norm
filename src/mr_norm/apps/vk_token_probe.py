from __future__ import annotations

import json
import os
import sys

import requests

from mr_norm.apps.vk_token_file import get_community_token_from_env_or_file

API = "https://api.vk.com/method/"


def _vk(method: str, token: str, **params: str | int) -> dict:
    ver = (os.getenv("VK_API_VERSION") or "5.199").strip()
    data = {"access_token": token, "v": ver, **{k: str(v) for k, v in params.items()}}
    response = requests.post(API + method, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def main() -> None:
    token = get_community_token_from_env_or_file()
    if not token:
        print("Нет токена: VK_token.txt или VK_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)

    print("Проверка токена (первые 20 символов):", repr(token[:20]) + "...")
    print()

    payload = _vk("groups.getById", token)
    print("groups.getById:", json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
    if "error" in payload:
        print("\nОшибка getById: проверьте токен сообщества.", file=sys.stderr)
        sys.exit(2)

    resp = payload.get("response")
    if isinstance(resp, list):
        groups = resp
    elif isinstance(resp, dict):
        groups = resp.get("groups") or ([resp] if "id" in resp else [])
    else:
        groups = []

    gid_env = (os.getenv("VK_GROUP_ID") or "").strip().lstrip("-")
    if gid_env.isdigit():
        gid = int(gid_env)
    elif groups:
        gid = int(groups[0]["id"])
    else:
        print("Не удалось определить group_id.", file=sys.stderr)
        sys.exit(3)

    print(f"\nПроверка groups.getLongPollServer для group_id={gid} (токен сообщества)...")
    lp = _vk("groups.getLongPollServer", token, group_id=gid)
    print(json.dumps(lp, ensure_ascii=False, indent=2)[:2000])

    poll_tok = (os.getenv("VK_USER_POLL_TOKEN") or "").strip()
    if "error" in lp and poll_tok:
        print("\nПовтор с VK_USER_POLL_TOKEN...")
        lp2 = _vk("groups.getLongPollServer", poll_tok, group_id=gid)
        print(json.dumps(lp2, ensure_ascii=False, indent=2)[:2000])
        if "error" not in lp2:
            print("\nOK: Long Poll доступен через VK_USER_POLL_TOKEN.")
            return

    if "error" in lp:
        print("\nLong Poll недоступен с токеном сообщества. См. VK_ERROR_15_HELP в vk_bot.py.", file=sys.stderr)
        sys.exit(4)

    print("\nOK: Long Poll доступен.")


if __name__ == "__main__":
    main()
