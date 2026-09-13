"""Deterministic prototype for canonical document resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path


REGISTRY = json.loads(Path(__file__).with_name("canonical_alias_registry.json").read_text(encoding="utf-8"))


def normalize(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[№#]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def resolve_canonical(query: str) -> dict:
    text = normalize(query)
    exact = []
    for entry in REGISTRY["entries"]:
        aliases = [normalize(alias) for alias in entry["aliases"]]
        if any(alias == text or alias in text for alias in aliases):
            exact.append((len(max(aliases, key=len)), entry, "alias"))

    number_hits = re.findall(r"(?<!\d)(\d{1,4}(?:н)?)(?!\d)", text)
    for entry in REGISTRY["entries"]:
        for number in entry["act_numbers"]:
            if number in number_hits:
                kind_ok = not entry["act_kinds"] or any(kind in text for kind in entry["act_kinds"])
                if kind_ok:
                    exact.append((1000 + len(number), entry, "number"))

    if not exact:
        return {"found": False, "status": "not_found", "reason": "no_canonical_match"}
    exact.sort(key=lambda item: item[0], reverse=True)
    _, entry, reason = exact[0]
    if entry.get("status") == "catalog_gap":
        return {"found": False, "status": "catalog_gap", "reason": reason, "aliases": entry["aliases"]}
    return {"found": True, "status": "exact", "doc_id": entry["doc_id"], "reason": reason}
