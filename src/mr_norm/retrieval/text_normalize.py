from __future__ import annotations

import re

# Longest suffixes first for Russian term stemming (lightweight morphology).
_RUSSIAN_TERM_SUFFIXES = (
    "ением",
    "ениями",
    "овании",
    "ованием",
    "ения",
    "ении",
    "ение",
    "ными",
    "ным",
    "ная",
    "ное",
    "ные",
    "ной",
    "ных",
    "ого",
    "ому",
    "ему",
    "ими",
    "ами",
    "ями",
    "ах",
    "ях",
    "ов",
    "ев",
    "ий",
    "ый",
    "ая",
    "ое",
    "ые",
    "ие",
    "ом",
    "ем",
    "ам",
    "ям",
    "ую",
    "юю",
    "им",
    "ым",
    "ой",
    "ей",
    "а",
    "я",
    "е",
    "и",
    "ы",
    "у",
    "ю",
    "о",
)


def normalize_catalog_text(value: str) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def russian_term_stem(token: str) -> str:
    """Lightweight Russian stem for dictionary term matching (not full lemmatization)."""
    normalized = normalize_catalog_text(token)
    if len(normalized) <= 3:
        return normalized
    for suffix in _RUSSIAN_TERM_SUFFIXES:
        if len(normalized) > len(suffix) + 3 and normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized[: max(4, len(normalized) - 2)]


def phrase_stems(phrase: str, *, min_token_len: int = 4) -> tuple[str, ...]:
    return tuple(
        russian_term_stem(token)
        for token in normalize_catalog_text(phrase).split()
        if len(token) >= min_token_len
    )


def stems_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 4:
        return False
    return longer.startswith(shorter) or shorter.startswith(longer)


def morphology_phrase_matches_query(phrase: str, query: str, *, min_stems: int = 2) -> bool:
    """True when all significant stems of phrase appear in query (inflection-tolerant)."""
    phrase_norm = normalize_catalog_text(phrase)
    query_norm = normalize_catalog_text(query)
    if not phrase_norm or not query_norm:
        return False
    if phrase_norm in query_norm:
        return True

    phrase_stem_list = phrase_stems(phrase)
    if len(phrase_stem_list) < min_stems:
        return phrase_norm in query_norm

    query_stem_list = phrase_stems(query, min_token_len=3)
    if not query_stem_list:
        return False

    for phrase_stem in phrase_stem_list:
        if not any(stems_match(phrase_stem, query_stem) for query_stem in query_stem_list):
            return False
    return True
