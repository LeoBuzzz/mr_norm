"""Document and term candidates taken from this repository's definition list.

The parser may use only these ids. It does not invent a document or a meaning.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymorphy3

TOKEN_RE = re.compile(r"\d+(?:\.\d+)+|[A-Za-zА-ЯЁа-яё0-9]+(?:-[A-Za-zА-ЯЁа-яё0-9]+)*")
_MORPH = pymorphy3.MorphAnalyzer()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def lemma_tokens(text: str) -> tuple[str, ...]:
    lemmas = []
    for token in TOKEN_RE.findall(text):
        if re.fullmatch(r"[А-ЯЁA-Z]{2,8}", token):
            lemmas.append(token.lower())
            continue
        parsed = _MORPH.parse(token)[0]
        lemmas.append(parsed.normal_form.replace("ё", "е"))
    return tuple(lemmas)


@dataclass
class DocumentCard:
    doc_id: str
    name: str


@dataclass
class Meaning:
    meaning_id: str
    label: str
    lemma: str
    variants: list[str] = field(default_factory=list)


@dataclass
class Catalog:
    documents: list[DocumentCard]
    meanings: list[Meaning]
    keys: dict[tuple[str, ...], list[str]]

    def meaning(self, meaning_id: str) -> Meaning | None:
        return next((item for item in self.meanings if item.meaning_id == meaning_id), None)

    def document(self, doc_id: str) -> DocumentCard | None:
        return next((item for item in self.documents if item.doc_id == doc_id), None)


def _load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=1)
def load_catalog(root: str | None = None) -> Catalog:
    base = Path(root) if root else repo_root()
    rows = _load_rows(base / "probe" / "term-definitions.jsonl")
    names = sorted({item["file"] for row in rows for item in row["definitions"]})
    documents = [DocumentCard(f"doc-{index:03d}", name) for index, name in enumerate(names, 1)]
    meanings: list[Meaning] = []
    keys: dict[tuple[str, ...], list[str]] = {}
    for index, row in enumerate(rows, 1):
        meaning_id = f"term-{index:04d}"
        variants = {row["term"]}
        for item in row["definitions"]:
            variants.add(item["term"])
            variants.update(item.get("aliases") or [])
        meaning = Meaning(meaning_id, row["term"], row["lemma"], sorted(variants))
        meanings.append(meaning)
        for variant in variants:
            key = lemma_tokens(variant)
            if not key:
                continue
            bucket = keys.setdefault(key, [])
            if meaning_id not in bucket:
                bucket.append(meaning_id)
    return Catalog(documents, meanings, keys)


def match_documents(query: str, catalog: Catalog) -> list[DocumentCard]:
    """Return every document that shares the strongest explicit cue in the question."""
    text = _norm(query)
    scored: list[tuple[int, DocumentCard]] = []
    gost = re.search(r"гост\s*р?\s*(\d{4,5})", text)
    law = re.search(r"(\d+)\s*-?\s*фз", text)
    for document in catalog.documents:
        name = _norm(document.name)
        score = 0
        if "пуэ" in text and "пуэ" in name:
            score += 2
        if gost and gost.group(1) in name and "гост" in name:
            score += 5
        if law and law.group(1) in name and "фз" in name:
            score += 5
        if score:
            scored.append((score, document))
    if not scored:
        return []
    best = max(score for score, _document in scored)
    return [document for score, document in scored if score == best]


def match_meanings(query: str, catalog: Catalog) -> tuple[list[Meaning], list[Meaning]]:
    """Longest non-overlapping matches. The second list is meanings tied on one span."""
    tokens = lemma_tokens(query)
    chosen: list[tuple[int, int, str]] = []
    ambiguous: list[str] = []
    index = 0
    while index < len(tokens):
        found = None
        for size in range(min(4, len(tokens) - index), 0, -1):
            key = tuple(tokens[index : index + size])
            hits = catalog.keys.get(key) or []
            if not hits:
                continue
            found = (index, index + size, hits)
            break
        if not found:
            index += 1
            continue
        start, end, hits = found
        if len(hits) > 1:
            ambiguous.extend(hits)
        else:
            chosen.append((start, end, hits[0]))
        index = end
    meanings = []
    seen = set()
    for _start, _end, meaning_id in chosen:
        if meaning_id in seen:
            continue
        seen.add(meaning_id)
        item = catalog.meaning(meaning_id)
        if item:
            meanings.append(item)
    ambiguous_meanings = []
    for meaning_id in ambiguous:
        item = catalog.meaning(meaning_id)
        if item and item not in ambiguous_meanings:
            ambiguous_meanings.append(item)
    return meanings, ambiguous_meanings
