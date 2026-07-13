from __future__ import annotations

import re
from dataclasses import dataclass

from mr_norm.retrieval.contracts import RetrievedItem

_POINT_KEY_RE = re.compile(r"\s+")


def normalize_point_number(value: str) -> str:
    """Canonical point key: lowercase, underscores as dots, no spaces."""
    text = (value or "").strip().lower().replace("_", ".")
    return _POINT_KEY_RE.sub("", text)


def point_number_filter_variants(value: str) -> tuple[str, ...]:
    """Candidate payload values for exact point_number filter."""
    raw = (value or "").strip()
    if not raw:
        return ()
    normalized = normalize_point_number(raw)
    variants: list[str] = []
    for candidate in (raw, normalized, normalized.replace(".", "_")):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return tuple(variants)


def points_exact_match(expected: str, actual: str) -> bool:
    left = normalize_point_number(expected)
    right = normalize_point_number(actual)
    return bool(left and right and left == right)


def filter_items_by_exact_point(
    items: list[RetrievedItem],
    point_number: str,
) -> list[RetrievedItem]:
    return [item for item in items if points_exact_match(point_number, item.point_number)]


@dataclass(frozen=True)
class AssembledPointPart:
    chunk_id: str
    part_index: int
    text: str
    point_identity_key: str = ""


@dataclass(frozen=True)
class AssembledPoint:
    doc_id: str
    doc_name: str
    point_number: str
    text: str
    parts: tuple[AssembledPointPart, ...]
    total_parts: int
    is_complete: bool
    items: tuple[RetrievedItem, ...]


def _part_sort_key(item: RetrievedItem) -> tuple[int, str]:
    return (item.part_index, item.chunk_id)


def _identity_line_number(point_identity_key: str) -> int:
    key = (point_identity_key or "").strip()
    if not key or "::" not in key:
        return 10**9
    try:
        tail = key.rsplit("::", 1)[-1]
        return int(tail.split(":")[0])
    except ValueError:
        return 10**9


def select_point_identity_group(items: list[RetrievedItem]) -> tuple[list[RetrievedItem], bool]:
    if len(items) <= 1:
        return items, False

    keyed: dict[str, list[RetrievedItem]] = {}
    for item in items:
        key = (item.point_identity_key or "").strip()
        if key and "::" in key:
            keyed.setdefault(key, []).append(item)

    if len(keyed) <= 1:
        return items, False

    best_key = min(
        keyed,
        key=lambda key: (_identity_line_number(key), key),
    )
    return keyed[best_key], True


def assemble_point_parts(
    items: list[RetrievedItem],
    *,
    point_number: str,
) -> AssembledPoint | None:
    matched = filter_items_by_exact_point(items, point_number)
    if not matched:
        return None

    matched, collapsed = select_point_identity_group(matched)
    ordered = sorted(matched, key=_part_sort_key)
    total_parts = max((item.total_parts for item in ordered), default=1)
    if total_parts <= 0:
        total_parts = len(ordered)

    parts: list[AssembledPointPart] = []
    texts: list[str] = []
    for item in ordered:
        text = item.text or ""
        if text:
            texts.append(text)
        parts.append(
            AssembledPointPart(
                chunk_id=item.chunk_id,
                part_index=item.part_index,
                text=text.strip(),
                point_identity_key=item.point_identity_key,
            )
        )

    doc_id = next((item.doc_id for item in ordered if item.doc_id), "")
    doc_name = next((item.doc_name for item in ordered if item.doc_name), "")
    resolved_point = next((item.point_number for item in ordered if item.point_number), point_number)
    full_text = "".join(texts).strip()
    is_complete = len(ordered) >= total_parts

    return AssembledPoint(
        doc_id=doc_id,
        doc_name=doc_name,
        point_number=resolved_point,
        text=full_text,
        parts=tuple(parts),
        total_parts=total_parts,
        is_complete=is_complete,
        items=tuple(ordered),
    )
