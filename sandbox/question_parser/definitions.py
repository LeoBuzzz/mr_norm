"""Search definitions by the lemma key used in probe/term-definitions.jsonl."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymorphy3

from question_parser.model_route import load_documents

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS_PATH = ROOT / "probe" / "term-definitions.jsonl"
TOKEN_RE = re.compile(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{1,}")
_MORPH = pymorphy3.MorphAnalyzer()
_LEMMA_CACHE: dict[str, str] = {}


def lemma_key(term: str) -> str:
    """Same key as scripts/extract_definitions.lemma_key."""
    parts = []
    for token in TOKEN_RE.findall(term):
        cached = _LEMMA_CACHE.get(token)
        if cached is None:
            if re.fullmatch(r"[А-ЯЁ]{2,8}", token):
                cached = token
            else:
                cached = _MORPH.parse(token)[0].normal_form.replace("ё", "е")
            _LEMMA_CACHE[token] = cached
        parts.append(cached)
    return " ".join(parts)


def load_definitions(path: Path | None = None) -> list[dict[str, Any]]:
    source = path or DEFINITIONS_PATH
    cards = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cards.append(json.loads(line))
    return cards


def stamp_document_ids(path: Path | None = None, documents: dict[str, Any] | None = None) -> int:
    """Write doc_id onto every definition by matching the corpus filename."""
    source = path or DEFINITIONS_PATH
    catalog = documents if documents is not None else load_documents()
    by_filename = {item["filename"]: item["id"] for item in catalog["documents"]}
    cards = load_definitions(source)
    stamped = 0
    for card in cards:
        for item in card["definitions"]:
            doc_id = by_filename.get(item["file"])
            if not doc_id:
                raise KeyError(item["file"])
            item["doc_id"] = doc_id
            stamped += 1
    lines = [json.dumps(card, ensure_ascii=False) for card in cards]
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stamped


def _contains_span(query_tokens: list[str], card_tokens: list[str]) -> bool:
    if not card_tokens or len(card_tokens) > len(query_tokens):
        return False
    width = len(card_tokens)
    return any(query_tokens[index : index + width] == card_tokens for index in range(len(query_tokens) - width + 1))


@lru_cache(maxsize=1)
def _cached_cards() -> tuple[dict[str, Any], ...]:
    return tuple(load_definitions())


def definition_search(*, query: str, doc_id: str | None = None, cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return every definition of the longest matching term.

    Several documents may define one term. All of those definitions stay in
    hits. A shorter term inside the same words is not added beside it.
    """
    source = cards if cards is not None else list(_cached_cards())
    query_tokens = lemma_key(query).split()
    matched = []
    for card in source:
        card_tokens = str(card.get("lemma") or "").split()
        if _contains_span(query_tokens, card_tokens):
            matched.append(card)
    if matched:
        longest = max(len(str(card.get("lemma") or "").split()) for card in matched)
        matched = [card for card in matched if len(str(card.get("lemma") or "").split()) == longest]
    hits = []
    for card in matched:
        for item in card.get("definitions") or []:
            if doc_id and item.get("doc_id") != doc_id:
                continue
            hits.append(
                {
                    "doc_id": item.get("doc_id"),
                    "point_ref": item.get("locator"),
                    "term": item.get("term") or card.get("term"),
                    "text": item.get("definition"),
                    "file": item.get("file"),
                    "full_point_text": True,
                    "branch": "definition_search",
                }
            )
    return {
        "status": "ok" if hits else "not_found",
        "doc_id": doc_id,
        "point_ref": None,
        "text": hits[0]["text"] if len(hits) == 1 else None,
        "full_point_text": len(hits) == 1,
        "hits": hits,
    }
