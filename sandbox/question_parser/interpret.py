"""Deterministic reading of one question into a semantic draft.

This is the no-model backend. A later model backend must return the same draft
shape. Several writings of one known meaning stay one meaning. Two documents
or two meanings stay unresolved.
"""

from __future__ import annotations

import re
from typing import Any

from question_parser.catalog import Catalog, Meaning, lemma_tokens, match_documents, match_meanings
from question_parser.sources import TOKEN_RE, build_source_manifest, ref_for_span

POINT_CUE = re.compile(r"(?:пункт\w*|п\.)\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
DEFINE_CUES = ("что такое", "определение", "дай определение")
COMPARE_CUES = ("сравни", "сравните", "отличие", "чем отличается")
CONCLUDE_CUES = ("сделай вывод", "можно ли считать", "применимо ли")
SUMMARIZE_CUES = ("кратко перескажи", "обзор темы", "суммируй")
ALL_CUES = ("все упоминания", "все случаи", "везде где")
QUANTITY_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:кв|квт|мвт)\b", re.IGNORECASE)
EXCLUSION_RE = re.compile(r"(?:кроме|за исключением|без)\s+([A-Za-zА-ЯЁа-яё0-9-]+)", re.IGNORECASE)


def interpret(query: str, catalog: Catalog, context: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    manifest = build_source_manifest(query, context)
    tokens = manifest["tokens"]["current"]
    folded = query.lower().replace("ё", "е")
    operation = _operation(folded)
    documents = match_documents(query, catalog)
    meanings, ambiguous_meanings = match_meanings(query, catalog)
    dialog = _dialog(folded, context)
    scope_changed = _scope_changed(context, documents)
    complexity = "complex" if _is_complex(operation, documents, ambiguous_meanings, scope_changed) else "simple"
    draft = _draft(query, tokens, operation, documents, meanings, ambiguous_meanings, dialog, folded)
    return complexity, draft, manifest


def _operation(folded: str) -> str:
    if any(cue in folded for cue in DEFINE_CUES):
        return "define"
    if any(cue in folded for cue in COMPARE_CUES):
        return "compare"
    if any(cue in folded for cue in CONCLUDE_CUES):
        return "conclude"
    if any(cue in folded for cue in SUMMARIZE_CUES):
        return "summarize"
    if POINT_CUE.search(folded):
        return "text"
    if any(cue in folded for cue in ALL_CUES):
        return "all"
    return "search"


def _is_complex(operation: str, documents: list, ambiguous: list[Meaning], scope_changed: bool) -> bool:
    if operation in {"define", "compare", "conclude", "summarize"}:
        return True
    if len(documents) > 1 or ambiguous:
        return True
    return scope_changed


def dialog_action(query: str, context: dict[str, Any] | None) -> str:
    """Dialog move of one new utterance. A new question does not look at the previous scope."""
    folded = query.lower().replace("ё", "е")
    return _dialog(folded, context)


def _dialog(folded: str, context: dict[str, Any] | None) -> str:
    if not context or not context.get("turns"):
        return "new"
    if folded.startswith(("нет", "не ", "исправь")):
        return "correct"
    if folded.startswith(("а ", "и еще", "в этом", "там ")):
        return "continue"
    return "new"


def _scope_changed(context: dict[str, Any] | None, documents: list) -> bool:
    selected = (context or {}).get("selected_doc_id")
    if not selected or len(documents) != 1:
        return False
    return documents[0].doc_id != selected


def _draft(query, tokens, operation, documents, meanings, ambiguous_meanings, dialog, folded) -> dict[str, Any]:
    span = _whole_span(tokens)
    task: dict[str, Any] = {"operation": operation, "source": [span]}
    if operation in {"search", "all"}:
        task["topic"] = _topic(query, meanings)
        task["document"] = documents[0].doc_id if len(documents) == 1 else "corpus"
    elif operation == "text":
        point = _point_ref(tokens)
        task["document"] = documents[0].doc_id if len(documents) == 1 else "unresolved"
        if point:
            task["point"] = point
        task["topic"] = _topic(query, meanings)
    else:
        task["topic"] = _topic(query, meanings)
        task["document"] = documents[0].doc_id if len(documents) == 1 else "unresolved"
    term_rows = []
    for meaning in meanings:
        ref = _meaning_ref(tokens, meaning) or span
        term_rows.append({"source": ref, "meaning": meaning.meaning_id})
    if term_rows:
        task["terms"] = term_rows
    conditions = _conditions(tokens, folded)
    if conditions:
        task["conditions"] = conditions
    uncertain = []
    if len(documents) > 1:
        cue = _token_ref(tokens, "пуэ") or _token_ref(tokens, "гост") or span
        uncertain.append(
            {
                "field": "document",
                "source": cue,
                "task": 1,
                "candidates": [item.doc_id for item in documents],
            }
        )
        task["document"] = "unresolved"
    if ambiguous_meanings:
        uncertain.append(
            {
                "field": "term",
                "source": span,
                "task": 1,
                "candidates": [item.meaning_id for item in ambiguous_meanings],
            }
        )
    draft: dict[str, Any] = {"dialog": dialog, "tasks": [task]}
    if uncertain:
        draft["uncertain"] = uncertain
    return draft


def _whole_span(tokens: list[dict]) -> str:
    if not tokens:
        return "current@1:1"
    return ref_for_span("current", 1, tokens[-1]["ordinal"])


def _topic(query: str, meanings: list[Meaning]) -> str:
    if meanings:
        return meanings[0].label
    return query.strip()


def _point_ref(tokens: list[dict]) -> str | None:
    for index, token in enumerate(tokens):
        marker = token["text"].lower().replace("ё", "е")
        if marker.startswith("пункт") or marker in {"п", "п."}:
            if index + 1 < len(tokens) and re.fullmatch(r"\d+(?:\.\d+)*", tokens[index + 1]["text"]):
                ordinal = tokens[index + 1]["ordinal"]
                return ref_for_span("current", ordinal, ordinal)
    for token in tokens:
        if re.fullmatch(r"\d+(?:\.\d+)+", token["text"]):
            return ref_for_span("current", token["ordinal"], token["ordinal"])
    return None


def _token_ref(tokens: list[dict], text: str) -> str | None:
    for token in tokens:
        if token["text"].lower().replace("ё", "е") == text:
            return ref_for_span("current", token["ordinal"], token["ordinal"])
    return None


def _meaning_ref(tokens: list[dict], meaning: Meaning) -> str | None:
    query_lemmas = lemma_tokens(" ".join(token["text"] for token in tokens))
    # Ordinals follow the manifest tokens, which use the same tokenizer.
    for variant in sorted(meaning.variants, key=len, reverse=True):
        key = lemma_tokens(variant)
        if not key:
            continue
        for start in range(0, len(query_lemmas) - len(key) + 1):
            if query_lemmas[start : start + len(key)] == key:
                return ref_for_span("current", start + 1, start + len(key))
    return None


def _conditions(tokens: list[dict], folded: str) -> list[dict[str, str]]:
    conditions = []
    for token in tokens:
        if QUANTITY_RE.fullmatch(token["text"].replace(" ", "")) or QUANTITY_RE.search(folded) and token["text"].lower() in {"кв", "квт", "мвт"}:
            number = _previous_number(tokens, token["ordinal"])
            if number:
                conditions.append(
                    {
                        "source": ref_for_span("current", number["ordinal"], token["ordinal"]),
                        "kind": "quantity",
                        "polarity": "include",
                    }
                )
            break
    exclusion = EXCLUSION_RE.search(folded)
    if exclusion:
        word = exclusion.group(1)
        for token in tokens:
            if token["text"].lower().replace("ё", "е") == word.lower().replace("ё", "е"):
                conditions.append(
                    {
                        "source": ref_for_span("current", token["ordinal"], token["ordinal"]),
                        "kind": "exclusion",
                        "polarity": "exclude",
                    }
                )
                break
    return conditions


def _previous_number(tokens: list[dict], ordinal: int) -> dict | None:
    for token in tokens:
        if token["ordinal"] == ordinal - 1 and re.fullmatch(r"\d+(?:[.,]\d+)?", token["text"]):
            return token
    return None
