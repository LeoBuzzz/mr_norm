from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from mr_norm.runtime.contracts import PreparedQueryPlan
from mr_norm.runtime.pipeline_features import query_has_explicit_doc_reference
from mr_norm.retrieval.document_catalog import extract_document_label_hint, extract_point_number_hints

MAX_DIALOG_TURNS = 10
MAX_ANSWER_CHARS_FOR_LLM = 1500
MAX_USER_CHARS_FOR_LLM = 500

FOLLOW_UP_MARKERS = re.compile(
    r"\b("
    r"там|тут|этот|эта|это|тот|та|те|"
    r"как\s+часто|подробнее|ещё|еще|"
    r"а\s+в|а\s+по|тот\s+же|тот\s+же\s+пункт|"
    r"этот\s+пункт|этом\s+пункте|в\s+этом\s+документе"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DialogTurn:
    user_text: str
    answer: str
    retrieval_query: str = ""
    resolved_doc_id: str = ""
    resolved_doc_names: tuple[str, ...] = ()
    point_number_hints: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    resolve_unambiguous: bool = False

    def to_llm_dict(self) -> dict[str, Any]:
        answer = self.answer.strip()
        if len(answer) > MAX_ANSWER_CHARS_FOR_LLM:
            answer = answer[: MAX_ANSWER_CHARS_FOR_LLM].rstrip() + "…"
        user = self.user_text.strip()
        if len(user) > MAX_USER_CHARS_FOR_LLM:
            user = user[: MAX_USER_CHARS_FOR_LLM].rstrip() + "…"
        payload: dict[str, Any] = {
            "user": user,
            "assistant": answer,
        }
        if self.resolved_doc_names:
            payload["resolved_doc_names"] = list(self.resolved_doc_names)
        if self.point_number_hints:
            payload["point_number_hints"] = list(self.point_number_hints)
        if self.concepts:
            payload["concepts"] = list(self.concepts)
        return payload


@dataclass(frozen=True)
class DialogContext:
    session_key: str = ""
    turns: tuple[DialogTurn, ...] = ()

    @property
    def last_turn(self) -> DialogTurn | None:
        return self.turns[-1] if self.turns else None

    @property
    def last_resolved_doc_id(self) -> str:
        turn = self.last_turn
        return turn.resolved_doc_id if turn else ""

    @property
    def last_resolved_doc_names(self) -> tuple[str, ...]:
        turn = self.last_turn
        return turn.resolved_doc_names if turn else ()

    @property
    def last_resolve_unambiguous(self) -> bool:
        turn = self.last_turn
        return bool(turn and turn.resolve_unambiguous)

    def to_llm_turns(self, *, limit: int = 5) -> list[dict[str, Any]]:
        recent = self.turns[-limit:] if limit > 0 else self.turns
        return [turn.to_llm_dict() for turn in recent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "turns": [turn.to_llm_dict() for turn in self.turns],
        }


@dataclass
class DialogSessionStore:
    max_turns: int = MAX_DIALOG_TURNS
    _sessions: dict[str, DialogContext] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def lock_for(self, session_key: str) -> asyncio.Lock:
        key = session_key.strip()
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def get(self, session_key: str) -> DialogContext:
        key = session_key.strip()
        return self._sessions.get(key) or DialogContext(session_key=key)

    def reset(self, session_key: str) -> None:
        key = session_key.strip()
        self._sessions.pop(key, None)

    def append_turn(
        self,
        session_key: str,
        *,
        user_text: str,
        answer: str,
        retrieval_query: str = "",
        prepared_plan: PreparedQueryPlan | None = None,
        resolved_doc_id: str = "",
    ) -> DialogContext:
        key = session_key.strip()
        current = self.get(key)
        plan = prepared_plan
        doc_id = resolved_doc_id.strip()
        doc_names: tuple[str, ...] = ()
        point_hints: tuple[str, ...] = ()
        concepts: tuple[str, ...] = ()
        resolve_unambiguous = False

        if plan is not None:
            doc_names = tuple(plan.resolved_doc_names)
            point_hints = tuple(plan.point_number_hints)
            concepts = tuple(plan.concepts)
            resolve_unambiguous = bool(
                plan.resolved_doc_names
                and not plan.ambiguous
                and len(plan.resolved_doc_names) == 1
            )
            if not doc_id:
                catalog_id = str(plan.document_resolution.catalog_id or "").strip()
                if catalog_id and not catalog_id.startswith("knowledge:"):
                    doc_id = catalog_id

        turn = DialogTurn(
            user_text=user_text.strip(),
            answer=answer.strip(),
            retrieval_query=retrieval_query.strip() or user_text.strip(),
            resolved_doc_id=doc_id,
            resolved_doc_names=doc_names,
            point_number_hints=point_hints,
            concepts=concepts,
            resolve_unambiguous=resolve_unambiguous,
        )
        turns = (*current.turns, turn)
        if len(turns) > self.max_turns:
            turns = turns[-self.max_turns :]
        updated = DialogContext(session_key=key, turns=turns)
        self._sessions[key] = updated
        return updated


def parse_dialog_input(raw: str) -> tuple[str, bool, bool]:
    """Parse user text.

    Returns:
        query: text to send to norm lookup (without leading reset dot)
        is_new_dialog: True when leading dot starts a fresh dialog
        reset_only: True when message is only "." (clear session, no search)
    """
    text = (raw or "").strip()
    if not text.startswith("."):
        return text, False, False
    rest = text[1:].strip()
    if not rest:
        return "", True, True
    return rest, True, False


def build_session_key(*, peer_id: int | str, from_id: int | str) -> str:
    return f"{peer_id}:{from_id}"


def is_follow_up_query(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    if FOLLOW_UP_MARKERS.search(text):
        return True
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    return word_count <= 6


def build_retrieval_query(query: str, dialog_context: DialogContext | None) -> str:
    text = (query or "").strip()
    if not text or dialog_context is None or not dialog_context.turns:
        return text
    if not is_follow_up_query(text):
        return text
    last_turn = dialog_context.last_turn
    if last_turn is None or not last_turn.user_text.strip():
        return text
    return f"{last_turn.user_text.strip()} {text}".strip()


def query_has_explicit_document_or_point(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    if query_has_explicit_doc_reference(text):
        return True
    if extract_point_number_hints(text):
        return True
    if extract_document_label_hint(text):
        return True
    return False


def apply_dialog_document_inheritance(
    query: str,
    filters: dict[str, Any],
    dialog_context: DialogContext | None,
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(filters or {})
    warnings: list[str] = []
    if merged.get("doc_id") or str(merged.get("doc_name") or "").strip():
        return merged, warnings
    if dialog_context is None or not dialog_context.turns:
        return merged, warnings
    if not dialog_context.last_resolve_unambiguous:
        return merged, warnings
    if query_has_explicit_document_or_point(query):
        return merged, warnings
    doc_id = dialog_context.last_resolved_doc_id
    if not doc_id:
        return merged, warnings
    merged["doc_id"] = doc_id
    warnings.append("dialog:inherited_document_context")
    return merged, warnings
