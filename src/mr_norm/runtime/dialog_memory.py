from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from mr_norm.retrieval.document_catalog import extract_document_label_hint, extract_point_number_hints
from mr_norm.retrieval.point_assemble import points_exact_match
from mr_norm.runtime.contracts import Citation, PreparedQueryPlan
from mr_norm.runtime.pipeline_features import query_has_explicit_doc_reference

MAX_DIALOG_TURNS = 10
MAX_ANSWER_CHARS_FOR_LLM = 1500
MAX_USER_CHARS_FOR_LLM = 500

FOLLOW_UP_MARKERS = re.compile(
    r"\b("
    r"там|тут|этот|эта|это|тот|та|те|"
    r"как\s+часто|подробнее|ещё|еще|"
    r"дай\s+текст|текст\s+п\.?|"
    r"а\s+в|а\s+по|тот\s+же|тот\s+же\s+пункт|"
    r"этот\s+пункт|этом\s+пункте|в\s+этом\s+документе"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DialogSourceReference:
    doc_id: str
    doc_name: str
    point_number: str

    def to_dict(self) -> dict[str, str]:
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "point_number": self.point_number,
        }


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
    source_references: tuple[DialogSourceReference, ...] = ()

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
        if self.source_references:
            payload["source_references"] = [ref.to_dict() for ref in self.source_references]
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
        source_references: tuple[DialogSourceReference, ...] = (),
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
            source_references=source_references,
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
    if extract_point_number_hints(text):
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


def query_has_explicit_document(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    if extract_document_label_hint(text):
        return True

    point_hints = extract_point_number_hints(text)
    if point_hints:
        remainder = text
        for hint in point_hints:
            remainder = re.sub(rf"\b{re.escape(hint)}\b", " ", remainder)
        remainder = re.sub(
            r"(?i)(?:дай\s+текст|текст|пункт\w*|п\.?|подпункт\w*|статья|ст\.|раздел|приложение)\s*",
            " ",
            remainder,
        )
        remainder = re.sub(r"\s+", " ", remainder).strip(" .,;:")
        if not remainder:
            return False
        return query_has_explicit_doc_reference(remainder)

    return query_has_explicit_doc_reference(text)


def query_has_explicit_point(query: str) -> bool:
    return bool(extract_point_number_hints(query or ""))


def query_has_explicit_document_or_point(query: str) -> bool:
    return query_has_explicit_document(query) or query_has_explicit_point(query)


def _normalize_source_reference(
    *,
    doc_id: str,
    doc_name: str,
    point_number: str,
) -> DialogSourceReference | None:
    point = (point_number or "").strip()
    if not point or point in {"—", "-"}:
        return None
    doc_key = (doc_id or "").strip() or (doc_name or "").strip()
    if not doc_key:
        return None
    return DialogSourceReference(
        doc_id=(doc_id or "").strip(),
        doc_name=(doc_name or "").strip(),
        point_number=point,
    )


def extract_source_references_from_citations(
    *,
    citations: tuple[Citation, ...] | list[Citation],
    evidence_by_chunk_id: dict[str, Any] | None = None,
) -> tuple[DialogSourceReference, ...]:
    evidence_by_chunk_id = evidence_by_chunk_id or {}
    collected: dict[tuple[str, str], DialogSourceReference] = {}
    for citation in citations:
        item = evidence_by_chunk_id.get(citation.chunk_id)
        doc_id = str(getattr(item, "doc_id", "") or "").strip()
        doc_name = str(citation.doc_name or getattr(item, "doc_name", "") or "").strip()
        point_number = str(citation.point_number or getattr(item, "point_number", "") or "").strip()
        ref = _normalize_source_reference(
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_number,
        )
        if ref is None:
            continue
        key = (ref.doc_id or ref.doc_name, ref.point_number)
        collected[key] = ref
    return tuple(collected.values())


def extract_source_references_from_evidence(
    evidence: tuple[Any, ...] | list[Any],
) -> tuple[DialogSourceReference, ...]:
    collected: dict[tuple[str, str], DialogSourceReference] = {}
    for item in evidence:
        ref = _normalize_source_reference(
            doc_id=str(getattr(item, "doc_id", "") or ""),
            doc_name=str(getattr(item, "doc_name", "") or ""),
            point_number=str(getattr(item, "point_number", "") or ""),
        )
        if ref is None:
            continue
        key = (ref.doc_id or ref.doc_name, ref.point_number)
        collected[key] = ref
    return tuple(collected.values())


def merge_source_references(
    *groups: tuple[DialogSourceReference, ...],
) -> tuple[DialogSourceReference, ...]:
    collected: dict[tuple[str, str], DialogSourceReference] = {}
    for group in groups:
        for ref in group:
            key = (ref.doc_id or ref.doc_name, ref.point_number)
            if ref.doc_id:
                collected[key] = ref
            elif key not in collected:
                collected[key] = ref
    return tuple(collected.values())


def _matching_prior_sources(
    *,
    point_number: str,
    dialog_context: DialogContext | None,
) -> list[DialogSourceReference]:
    if dialog_context is None or not dialog_context.turns:
        return []
    matches: dict[tuple[str, str], DialogSourceReference] = {}
    for turn in reversed(dialog_context.turns):
        for ref in turn.source_references:
            if points_exact_match(point_number, ref.point_number):
                key = (ref.doc_id or ref.doc_name, ref.point_number)
                matches[key] = ref
        if matches:
            break
    return list(matches.values())


def _inherit_document_filter(
    *,
    query: str,
    merged: dict[str, Any],
    dialog_context: DialogContext | None,
    warnings: list[str],
) -> dict[str, Any]:
    if merged.get("doc_id") or str(merged.get("doc_name") or "").strip():
        return merged
    if dialog_context is None or not dialog_context.turns:
        return merged
    if not dialog_context.last_resolve_unambiguous:
        return merged
    if query_has_explicit_document(query):
        return merged
    doc_id = dialog_context.last_resolved_doc_id
    if not doc_id:
        return merged
    merged["doc_id"] = doc_id
    warnings.append("dialog:inherited_document_context")
    return merged


def apply_dialog_followup_filters(
    query: str,
    filters: dict[str, Any],
    dialog_context: DialogContext | None,
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(filters or {})
    warnings: list[str] = []
    point_hints = extract_point_number_hints(query)

    if point_hints and dialog_context is not None and dialog_context.turns:
        if query_has_explicit_document(query):
            return merged, warnings

        point_number = point_hints[0]
        prior_matches = _matching_prior_sources(
            point_number=point_number,
            dialog_context=dialog_context,
        )
        if len(prior_matches) == 1:
            ref = prior_matches[0]
            if ref.doc_id:
                merged["doc_id"] = ref.doc_id
            elif ref.doc_name:
                merged["doc_name"] = ref.doc_name
            merged["point_number"] = point_number
            warnings.append("dialog:resolved_document_from_prior_citation")
            return merged, warnings
        if len(prior_matches) > 1:
            warnings.append("dialog:ambiguous_prior_point_sources")
            merged["point_number"] = point_number
            return merged, warnings

        merged = _inherit_document_filter(
            query=query,
            merged=merged,
            dialog_context=dialog_context,
            warnings=warnings,
        )
        if merged.get("doc_id") or merged.get("doc_name"):
            merged["point_number"] = point_number
        return merged, warnings

    merged = _inherit_document_filter(
        query=query,
        merged=merged,
        dialog_context=dialog_context,
        warnings=warnings,
    )
    return merged, warnings


def apply_dialog_document_inheritance(
    query: str,
    filters: dict[str, Any],
    dialog_context: DialogContext | None,
) -> tuple[dict[str, Any], list[str]]:
    return apply_dialog_followup_filters(query, filters, dialog_context)
