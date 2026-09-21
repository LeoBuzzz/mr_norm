from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mr_norm.retrieval.contracts import RetrievedItem
    from mr_norm.skills.deep_research import ResearchAnalysis, ResearchDocumentCandidate


@dataclass(frozen=True)
class PendingDeepResearchSession:
    session_key: str
    original_query: str
    analysis: "ResearchAnalysis"
    candidates: tuple["ResearchDocumentCandidate", ...]
    broad_evidence: tuple["RetrievedItem", ...]
    search_queries: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass
class DeepResearchSessionStore:
    _sessions: dict[str, PendingDeepResearchSession] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def lock_for(self, session_key: str) -> asyncio.Lock:
        key = session_key.strip()
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def get(self, session_key: str) -> PendingDeepResearchSession | None:
        return self._sessions.get(session_key.strip())

    def set(self, session: PendingDeepResearchSession) -> None:
        self._sessions[session.session_key.strip()] = session

    def clear(self, session_key: str) -> None:
        self._sessions.pop(session_key.strip(), None)

    def has_pending(self, session_key: str) -> bool:
        return session_key.strip() in self._sessions


_ALL_SELECTION = re.compile(r"^(?:все|all)$", re.IGNORECASE)
_NUMBER_SPLIT = re.compile(r"[,;\s]+")


def parse_document_selection(
    raw: str,
    *,
    max_index: int,
) -> tuple[tuple[int, ...], str]:
    """Parse user document selection.

    Returns:
        indices: 1-based candidate indices, sorted unique
        error: non-empty when selection is invalid
    """
    text = (raw or "").strip()
    if not text:
        return (), "Укажите номера документов через запятую или «все»."
    if _ALL_SELECTION.match(text):
        if max_index <= 0:
            return (), "Нет документов для выбора."
        return tuple(range(1, max_index + 1)), ""

    parts = [part.strip() for part in _NUMBER_SPLIT.split(text) if part.strip()]
    if not parts:
        return (), "Укажите номера документов через запятую или «все»."

    indices: list[int] = []
    seen: set[int] = set()
    for part in parts:
        if not part.isdigit():
            return (), f"Неверный номер: «{part}». Используйте цифры через запятую или «все»."
        value = int(part)
        if value < 1 or value > max_index:
            return (), f"Номер {value} вне диапазона 1–{max_index}."
        if value not in seen:
            seen.add(value)
            indices.append(value)
    if not indices:
        return (), "Не выбран ни один документ."
    return tuple(sorted(indices)), ""


def format_candidates_prompt(candidates: tuple["ResearchDocumentCandidate", ...]) -> str:
    lines = [
        "Первичный поиск завершён. Выберите документы для углублённого анализа.",
        "Ответьте номерами через запятую (например: 1, 3) или «все».",
        "",
    ]
    for candidate in candidates:
        points = ", ".join(f"п.{point}" for point in candidate.point_numbers[:8])
        if len(candidate.point_numbers) > 8:
            points += " …"
        points_part = f" ({points})" if points else ""
        lines.append(
            f"{candidate.index}. {candidate.doc_name}{points_part}\n"
            f"   фрагментов: {candidate.fragment_count}; {candidate.reason}"
        )
    return "\n".join(lines)
