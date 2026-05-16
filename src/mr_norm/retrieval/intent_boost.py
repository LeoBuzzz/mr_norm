from __future__ import annotations

import re

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.query_intent import detect_query_intent
from mr_norm.retrieval.text_normalize import normalize_catalog_text

LEGAL_ACT_MARKERS = (
    "федеральный закон",
    "постановление правительства",
    "постановление",
    "приказ мин",
    "приказ",
    "фз ",
    "№",
    "правила технической",
    "правила устройства",
)

LOW_PRIORITY_FOR_DOC_LOOKUP = (
    "гост р",
    "гост ",
    "сто ",
    "жилищный кодекс",
    "профессиональный стандарт",
)

FEDERAL_LAW_QUERY_MARKERS = (
    "какой документ",
    "какие документы",
    "устанавливает общие",
)

SUBORDINATE_ACT_MARKERS = (
    "правила технологического функционирования",
    "об утверждении правил",
)


def _item_blob(item: RetrievedItem) -> str:
    return normalize_catalog_text(
        " ".join(part for part in (item.doc_name, item.heading_path_text, item.text) if part)
    )


def _query_focus_tokens(query_norm: str) -> list[str]:
    tokens: list[str] = []
    for marker in (
        "электроэнергетик",
        "технологическ",
        "тариф",
        "гарантирующ",
        "заземл",
        "релейн",
        "диспетчерск",
        "электробезопасн",
    ):
        if marker in query_norm:
            tokens.append(marker)
    return tokens


def _intent_rank_key(item: RetrievedItem, intent: str, query_norm: str) -> tuple[int, int, float]:
    blob = _item_blob(item)
    priority = 1
    if intent in {"document_lookup", "regulation_scope"}:
        if any(marker in blob for marker in LEGAL_ACT_MARKERS):
            priority = 0
        elif any(marker in blob for marker in LOW_PRIORITY_FOR_DOC_LOOKUP) and "гост" not in query_norm:
            priority = 2
        focus_hits = sum(1 for token in _query_focus_tokens(query_norm) if token in blob)
        if focus_hits:
            priority = max(0, priority - focus_hits)
        if intent == "regulation_scope":
            subject_match = re.search(
                r"(?:что|чем)\s+регулиру(?:ет|ется|ют|ются)\s+(.+?)(?:\?|$)",
                query_norm,
            )
            if subject_match:
                subject_norm = normalize_catalog_text(subject_match.group(1))
                if subject_norm and subject_norm in blob:
                    priority = 0
        if intent == "document_lookup" and any(
            marker in query_norm for marker in FEDERAL_LAW_QUERY_MARKERS
        ):
            if "35-фз" in blob or "об электроэнергетике" in blob:
                priority = 0
            elif any(marker in blob for marker in SUBORDINATE_ACT_MARKERS) and "35-фз" not in blob:
                priority = max(priority, 2)
    score = float(item.score) if item.score is not None else 0.0
    return priority, -score, item.chunk_id or ""


def rerank_items_for_intent(
    items: list[RetrievedItem],
    query: str,
    *,
    limit: int,
) -> list[RetrievedItem]:
    intent = detect_query_intent(query)
    if intent == "factual" or not items:
        return items[:limit]
    query_norm = normalize_catalog_text(query)
    ranked = sorted(items, key=lambda item: _intent_rank_key(item, intent, query_norm))
    return ranked[:limit]
