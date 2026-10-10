"""Token tables and SourceRef checks.

A SourceRef is turn_id@first:last, inclusive token ordinals starting at 1.
Topic text must stay literal: a locator is rejected, not rewritten.
"""

from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(r"\d+(?:\.\d+)+|[A-Za-zА-ЯЁа-яё0-9]+(?:-[A-Za-zА-ЯЁа-яё0-9]+)*")
LOCATOR_RE = re.compile(r"(?<![\w@])[^\s@]+@\d+:\d+(?!\d)")
SOURCE_REF_RE = re.compile(r"([^@]+)@(\d+):(\d+)")


class DraftValidationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_draft") -> None:
        super().__init__(message)
        self.code = code


def build_source_manifest(query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = [{"turn_id": "current", "text": query}]
    for turn in (context or {}).get("turns", []):
        rows.append({"turn_id": str(turn["turn_id"]), "text": str(turn.get("user_text", ""))})
    seen: set[str] = set()
    tokens: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    for row in rows:
        turn_id, text = row["turn_id"], row["text"]
        if not turn_id or turn_id in seen:
            raise DraftValidationError(f"duplicate or empty source turn ID: {turn_id!r}")
        seen.add(turn_id)
        sources[turn_id] = text
        tokens[turn_id] = [
            {"ordinal": index, "start": match.start(), "end": match.end(), "text": match.group(0)}
            for index, match in enumerate(TOKEN_RE.finditer(text), 1)
        ]
    return {"sources": sources, "tokens": tokens}


def extract_source_ref(ref: Any, manifest: dict[str, Any]) -> dict[str, str]:
    if not isinstance(ref, str):
        raise DraftValidationError("SourceRef must be a string")
    match = SOURCE_REF_RE.fullmatch(ref)
    if not match:
        raise DraftValidationError(f"malformed SourceRef: {ref!r}")
    turn_id, first, last = match.group(1), int(match.group(2)), int(match.group(3))
    table = manifest["tokens"].get(turn_id)
    if table is None or first < 1 or last < first or last > len(table):
        raise DraftValidationError(f"SourceRef is outside available tokens: {ref!r}")
    source = manifest["sources"][turn_id]
    quote = source[table[first - 1]["start"] : table[last - 1]["end"]]
    return {"turn_id": turn_id, "quote": quote}


def ref_for_span(turn_id: str, first: int, last: int) -> str:
    return f"{turn_id}@{first}:{last}"
