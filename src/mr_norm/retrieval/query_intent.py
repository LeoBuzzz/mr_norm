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

FREQUENCY_PATTERNS = (
    "как часто",
    "периодичност",
    "с какой периодичност",
    "частота осмотр",
)

_STRUCTURAL_REFERENCE_PATTERNS = (
    ("table", re.compile(r"\bтаблиц\w*\s*(?:№\s*|n\s*)?(\d+(?:[._]\d+)*)", re.IGNORECASE)),
    ("appendix", re.compile(r"(?i:\bприложен\w*\s*(?:№\s*|n\s*)?)(\d+(?:[._]\d+)*)")),
    (
        "appendix",
        re.compile(r"(?i:\bприложен\w*\s*(?:№\s*)?)([А-ЯЁA-Z])(?=\s*(?:$|[.,:;!?—–-]|к\b))"),
    ),
)
_APPENDIX_MENTION_PATTERN = re.compile(r"\bприложени(?:е|я|ю|ем|и)\b", re.IGNORECASE)

DOMAIN_TERM_HINTS: dict[str, tuple[str, ...]] = {
    "электроэнергет": (
        "об электроэнергетике",
        "федеральный закон об электроэнергетике",
        "35-фз",
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
    if any(pattern in norm for pattern in FREQUENCY_PATTERNS):
        return "frequency"
    if any(pattern in norm for pattern in DOCUMENT_LOOKUP_PATTERNS):
        return "document_lookup"
    return "factual"


def structural_reference_queries(query: str) -> list[str]:
    """Return exact search strings for explicitly named tables or appendices.

    Structural labels are retrieval targets, not point numbers. Keeping their
    short labels as separate queries lets payload and vector search find a
    table embedded in a larger point chunk without weakening document scope.
    """
    references: list[str] = []
    for kind, pattern in _STRUCTURAL_REFERENCE_PATTERNS:
        for match in pattern.finditer(query or ""):
            label = match.group(1).replace("_", ".")
            reference = f"таблица {label}" if kind == "table" else f"приложение {label}"
            if reference not in references:
                references.append(reference)
    if _APPENDIX_MENTION_PATTERN.search(query or "") and not any(
        reference.startswith("приложение ") for reference in references
    ):
        references.append("приложение")
    return references


_REGULATION_SUBJECT_RE = re.compile(
    r"(?:что|чем)\s+регулиру(?:ет|ется|ют|ются)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)


def _regulation_subject_terms(query: str) -> list[str]:
    match = _REGULATION_SUBJECT_RE.search(query.strip())
    if not match:
        return []
    subject = re.sub(r"\s+", " ", match.group(1).strip(" \"'«»"))
    if len(subject) < 8:
        return []
    return [subject]


def _domain_hint_terms(norm: str) -> list[str]:
    terms: list[str] = []
    for marker, hints in DOMAIN_TERM_HINTS.items():
        if marker in norm:
            for hint in hints:
                if hint not in terms:
                    terms.append(hint)
    return terms


def intent_search_terms(query: str, intent: str) -> list[str]:
    if intent not in {"document_lookup", "regulation_scope"}:
        return []
    norm = normalize_catalog_text(query)
    terms = _domain_hint_terms(norm)
    if intent == "regulation_scope":
        for subject in _regulation_subject_terms(query):
            if subject not in terms:
                terms.insert(0, subject)
    return terms[:4]
