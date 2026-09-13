from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from mr_norm.retrieval.document_catalog import normalize_catalog_text

try:
    from natasha import MorphVocab
except ImportError:  # pragma: no cover - optional runtime enhancement
    MorphVocab = None


@lru_cache(maxsize=1)
def load_document_alias_registry() -> tuple[dict[str, Any], ...]:
    path = Path(__file__).with_name("document_alias_registry.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(item for item in data.get("entries", []) if isinstance(item, dict))


@lru_cache(maxsize=1)
def _morph_vocab():
    return MorphVocab() if MorphVocab is not None else None


def normalize_document_phrase(value: str) -> str:
    """Normalize case, spelling and Russian inflection for alias comparison."""
    text = normalize_catalog_text(value)
    vocab = _morph_vocab()
    if vocab is None:
        return text
    tokens = re.findall(r"[a-zа-яё0-9]+", text, flags=re.IGNORECASE)
    normalized: list[str] = []
    for token in tokens:
        if token.isdigit() or (token.isupper() and len(token) <= 6):
            normalized.append(token)
            continue
        try:
            forms = vocab(token)
            normalized.append(forms[0].normal if forms else token)
        except Exception:  # pragma: no cover - keep resolver available on bad token
            normalized.append(token)
    return " ".join(normalized)


def resolve_canonical_document_label(
    label: str,
    *,
    catalog: Any,
) -> tuple[str, str, str, tuple[str, ...]] | None:
    text = normalize_document_phrase(label)
    if not text:
        return None

    number_tokens = set(re.findall(r"(?<!\d)(\d{1,4}н?)(?!\d)", text))
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for entry in load_document_alias_registry():
        aliases = [normalize_document_phrase(str(alias)) for alias in entry.get("aliases", [])]
        exact_aliases = [alias for alias in aliases if alias and (text == alias or alias in text)]
        if exact_aliases:
            candidates.append((max(map(len, exact_aliases)), entry, "alias"))

        for number in entry.get("act_numbers", []):
            if str(number) not in number_tokens:
                continue
            kinds = [normalize_document_phrase(str(kind)) for kind in entry.get("act_kinds", [])]
            if kinds and not any(kind in text for kind in kinds):
                continue
            candidates.append((1000 + len(str(number)), entry, "number"))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, entry, reason = candidates[0]
    doc_id = str(entry.get("doc_id") or "")
    if entry.get("status") == "catalog_gap":
        return "", "", "catalog_gap", (reason,)
    catalog_entry = catalog.by_doc_id().get(doc_id) or catalog.by_id().get(doc_id)
    if catalog_entry is None:
        return None
    return doc_id, catalog_entry.doc_name, "exact", (reason,)
