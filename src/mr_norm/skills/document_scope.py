from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mr_norm.retrieval.document_catalog import DocumentCatalog, load_default_document_catalog
from mr_norm.skills.document_resolve import resolve_document_from_label


_ACT_NUMBER = r"[0-9]{1,5}(?:\.[0-9]+)?(?:-[0-9]{4})?[а-яa-z]?"
_GOST_NUMBER = r"[0-9]{1,5}(?:\.[0-9]+)?(?:-[0-9]{4})?"
_ACT_PREFIX = r"(?:приказ\w*|постановлен\w*|распоряжен\w*)"
_MENTION = re.compile(
    rf"(?i)(?:фз\s*[-–]?|{_ACT_PREFIX})\s*(?:№|n[oº]?\.?)?\s*{_ACT_NUMBER}|"
    rf"{_ACT_PREFIX}(?:\s+\w+){{1,8}}?\s*(?:№|n[oº]?\.?)\s*{_ACT_NUMBER}|"
    rf"{_ACT_PREFIX}(?:\s+\w+){{1,8}}?\s+{_ACT_NUMBER}|"
    rf"гост(?:\s+р)?\s*(?:№|n[oº]?\.?)?\s*{_GOST_NUMBER}|"
    rf"(?:№|n[oº]?\.?)\s*{_ACT_NUMBER}\s*(?:фз|гост|приказ\w*|постановлен\w*)"
)
_EXCLUDE = re.compile(r"(?i)(?:кроме|исключая|за\s+исключением|не\s+включая|не\s+в|без\s+(?:учета|учёта))")


@dataclass(frozen=True)
class DocumentScope:
    include_doc_ids: tuple[str, ...] = ()
    exclude_doc_ids: tuple[str, ...] = ()
    include_doc_names: tuple[str, ...] = ()
    exclude_doc_names: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def explicit(self) -> bool:
        return bool(self.include_doc_ids or self.exclude_doc_ids)

    def to_filters(self) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if self.include_doc_ids:
            filters["doc_id"] = list(self.include_doc_ids)
        elif self.include_doc_names:
            filters["doc_name"] = list(self.include_doc_names)
        if self.exclude_doc_ids:
            filters["exclude_doc_id"] = list(self.exclude_doc_ids)
        elif self.exclude_doc_names:
            filters["exclude_doc_name"] = list(self.exclude_doc_names)
        return filters


def resolve_document_scope(query: str, *, catalog: DocumentCatalog | None = None) -> DocumentScope:
    text = (query or "").strip()
    if not text:
        return DocumentScope()
    catalog = catalog or load_default_document_catalog()
    mentions: list[tuple[int, int, str]] = [(m.start(), m.end(), m.group(0)) for m in _MENTION.finditer(text)]

    from mr_norm.config.document_alias_registry import load_document_alias_registry

    lowered = text.casefold()
    alias_mentions: list[tuple[int, int, str]] = []
    for entry in load_document_alias_registry():
        for alias in entry.get("aliases", []):
            alias_text = str(alias).strip()
            if len(alias_text) < 3:
                continue
            alias_lowered = alias_text.casefold()
            for match in re.finditer(re.escape(alias_lowered), lowered):
                alias_mentions.append((match.start(), match.end(), alias_text))

    # Prefer the most specific phrase when aliases overlap.  For example,
    # ``ПТЭ`` is also a substring of ``ПТЭ потребителей``; retaining both
    # would incorrectly add the stations/networks PTE to an exclusion that
    # names only consumer PTE.
    occupied: list[tuple[int, int]] = []
    for start, end, surface in sorted(
        alias_mentions,
        key=lambda item: (item[0], -(item[1] - item[0])),
    ):
        if any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied):
            continue
        mentions.append((start, end, surface))
        occupied.append((start, end))

    included: list[str] = []
    excluded: list[str] = []
    include_names: list[str] = []
    exclude_names: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, bool]] = set()
    for start, _end, surface in sorted(mentions, key=lambda item: (item[0], -(item[1] - item[0]))):
        result = resolve_document_from_label(surface, catalog=catalog)
        if not result.found or not result.doc_id:
            continue
        prefix = text[max(0, start - 80) : start]
        excluded_here = bool(_EXCLUDE.search(prefix))
        key = (result.doc_id, excluded_here)
        if key in seen:
            continue
        seen.add(key)
        target = excluded if excluded_here else included
        names = exclude_names if excluded_here else include_names
        if result.doc_id not in target:
            target.append(result.doc_id)
            names.append(result.doc_name)

    if included and excluded:
        warnings.append("document_scope:include_and_exclude_both_present")
    return DocumentScope(tuple(included), tuple(excluded), tuple(include_names), tuple(exclude_names), tuple(warnings))
