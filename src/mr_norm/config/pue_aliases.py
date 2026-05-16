"""Optional ПУЭ alias handling for query planning and catalog matching."""

from __future__ import annotations

import os
from typing import Any

from mr_norm.retrieval.text_normalize import normalize_catalog_text

PUE_ALIAS_KEY = "пуэ"
_ENV_ENABLE_PUE = "MR_NORM_ENABLE_PUE_ALIASES"

KNOWN_QUERY_ALIASES_ALL: dict[str, tuple[str, ...]] = {
    "пуэ": ("правила устройства электроустановок", "электроустановок"),
    "птэ": ("правила технической эксплуатации", "технической эксплуатации"),
    "озп": ("отопительный сезон", "готовности"),
}


def resolve_enable_pue_aliases(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    value = os.environ.get(_ENV_ENABLE_PUE, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def active_known_query_aliases(*, enable_pue_aliases: bool) -> dict[str, tuple[str, ...]]:
    if enable_pue_aliases:
        return dict(KNOWN_QUERY_ALIASES_ALL)
    return {key: value for key, value in KNOWN_QUERY_ALIASES_ALL.items() if key != PUE_ALIAS_KEY}


def active_topic_aliases(
    topic_aliases: list[dict[str, Any]],
    *,
    enable_pue_aliases: bool,
) -> list[dict[str, Any]]:
    if enable_pue_aliases:
        return topic_aliases
    pue_norm = normalize_catalog_text(PUE_ALIAS_KEY)
    return [
        topic
        for topic in topic_aliases
        if normalize_catalog_text(str(topic.get("phrase") or "")) != pue_norm
    ]


def is_pue_abbreviation_entry(entry: dict[str, str]) -> bool:
    return normalize_catalog_text(str(entry.get("abbreviation") or "")) == normalize_catalog_text(PUE_ALIAS_KEY)
