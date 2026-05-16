from __future__ import annotations

import re

from mr_norm.retrieval.text_normalize import normalize_catalog_text

REGULATION_SCOPE_PATTERNS = (
    "что регулирует",
    "чем регулируется",
    "что устанавливает",
    "что определяет",
)

DOCUMENT_LOOKUP_PATTERNS = (
    "какой документ",
    "какие документы",
    "каков порядок",
    "какой порядок",
    "что такое",
    "кто отвечает",
    "как регулируются",
)

DOMAIN_TERM_HINTS: dict[str, tuple[str, ...]] = {
    "электроэнергет": (
        "об электроэнергетике",
        "федеральный закон об электроэнергетике",
        "электроэнергетика",
    ),
    "технологическ": (
        "технологическое присоединение",
        "постановление 861",
    ),
    "тариф": (
        "постановление 1178",
        "ценообразование электроэнергетик",
    ),
    "гарантирующ": (
        "гарантирующий поставщик",
        "постановление 442",
    ),
    "электробезопасн": (
        "охрана труда электроустанов",
        "903н",
    ),
    "релейн": (
        "релейная защита автоматика",
    ),
    "оперативно диспетчерск": (
        "оперативно диспетчерское управление",
        "системный оператор",
    ),
    "заземл": (
        "заземляющие устройства",
        "заземление",
    ),
}


def detect_query_intent(query: str) -> str:
    norm = normalize_catalog_text(query)
    if re.search(r"(?:п\.?|пункт\w*)\s*\d", norm) or re.search(r"\d+\.\d+(?:\.\d+)*", norm):
        return "point_lookup"
    if any(pattern in norm for pattern in REGULATION_SCOPE_PATTERNS):
        return "regulation_scope"
    if any(pattern in norm for pattern in DOCUMENT_LOOKUP_PATTERNS):
        return "document_lookup"
    return "factual"


def intent_search_terms(query: str, intent: str) -> list[str]:
    if intent != "document_lookup":
        return []
    norm = normalize_catalog_text(query)
    terms: list[str] = []
    for marker, hints in DOMAIN_TERM_HINTS.items():
        if marker in norm:
            for hint in hints:
                if hint not in terms:
                    terms.append(hint)
    return terms[:4]
