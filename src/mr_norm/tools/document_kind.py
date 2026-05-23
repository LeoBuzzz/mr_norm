"""Классификация типа документа по doc_name (лимиты начала для аннотаций)."""

from __future__ import annotations

from typing import Literal

DocumentKind = Literal["law", "gov_decree", "order", "gost", "other"]

OPENING_RECORDS_BY_KIND: dict[DocumentKind, int] = {
    "law": 12,
    "gov_decree": 12,
    "order": 10,
    "gost": 18,
    "other": 8,
}


def document_priority(doc_name: str) -> int:
    u = doc_name.upper()
    if "ФЕДЕРАЛЬНЫЙ ЗАКОН" in u or " ЗАКОН" in u or u.startswith("ЗАКОН"):
        return 1
    if "ПОСТАНОВЛЕНИЕ" in u and "ПРАВИТЕЛЬСТВ" in u:
        return 2
    if "РАСПОРЯЖЕНИЕ" in u and "ПРАВИТЕЛЬСТВ" in u:
        return 2
    if "ПРИКАЗ" in u:
        return 3
    if "ГОСТ" in u:
        return 4
    return 5


def document_kind(doc_name: str) -> DocumentKind:
    return {
        1: "law",
        2: "gov_decree",
        3: "order",
        4: "gost",
        5: "other",
    }[document_priority(doc_name)]


def opening_record_limit(doc_name: str) -> int:
    return OPENING_RECORDS_BY_KIND[document_kind(doc_name)]
