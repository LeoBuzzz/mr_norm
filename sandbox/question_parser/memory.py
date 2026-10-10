"""Per-user session memory.

A new question does not keep the previous document, point, or conditions.
Confirmed point text stays available so the same point is not read twice.
Users do not share a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_TURNS = 10


@dataclass
class UserMemory:
    user_id: str
    turns: list[dict[str, str]] = field(default_factory=list)
    confirmed_doc_id: str | None = None
    confirmed_point: str | None = None
    conditions: list[dict[str, Any]] = field(default_factory=list)
    pending: Any = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan: dict[str, Any] | None = None

    def remember_turn(self, text: str) -> str:
        turn_id = f"u{len(self.turns) + 1}"
        self.turns.append({"turn_id": turn_id, "user_text": text})
        if len(self.turns) > MAX_TURNS:
            self.turns = self.turns[-MAX_TURNS :]
        return turn_id

    def context(self, *, inherit_document: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"turns": list(self.turns)}
        if inherit_document and self.confirmed_doc_id:
            payload["selected_doc_id"] = self.confirmed_doc_id
        return payload

    def cache_result(self, result: dict[str, Any]) -> None:
        if result.get("full_point_text") and result.get("text") and result.get("doc_id") and result.get("point_ref"):
            self.results[f"{result['doc_id']}:{result['point_ref']}"] = result
        for hit in result.get("hits") or []:
            if hit.get("full_point_text") and hit.get("text") and hit.get("doc_id") and hit.get("point_ref"):
                self.results[f"{hit['doc_id']}:{hit['point_ref']}"] = hit


@dataclass
class SessionStore:
    users: dict[str, UserMemory] = field(default_factory=dict)

    def for_user(self, user_id: str) -> UserMemory:
        key = user_id.strip()
        if key not in self.users:
            self.users[key] = UserMemory(key)
        return self.users[key]
