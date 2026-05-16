from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.document_knowledge import phrase_required_tokens, primary_exact_phrase
from mr_norm.retrieval.text_normalize import normalize_catalog_text


def _item_text_blob(item: RetrievedItem) -> str:
    return normalize_catalog_text(
        " ".join(
            part
            for part in (
                item.doc_name,
                item.heading_path_text,
                item.text,
            )
            if part
        )
    )


def item_matches_required_tokens(item: RetrievedItem, required_tokens: tuple[str, ...]) -> bool:
    if not required_tokens:
        return True
    blob = _item_text_blob(item)
    return all(token in blob for token in required_tokens)


def rerank_items_for_exact_phrase(
    items: list[RetrievedItem],
    exact_phrase_terms: tuple[str, ...] | list[str],
    *,
    limit: int,
) -> list[RetrievedItem]:
    primary = primary_exact_phrase(exact_phrase_terms)
    if not primary:
        return items[:limit]
    required = phrase_required_tokens(primary)
    if not required:
        return items[:limit]

    matched: list[RetrievedItem] = []
    partial: list[RetrievedItem] = []
    rest: list[RetrievedItem] = []
    for item in items:
        blob = _item_text_blob(item)
        if all(token in blob for token in required):
            matched.append(item)
        elif any(token in blob for token in required):
            partial.append(item)
        else:
            rest.append(item)
    ranked = [*matched, *partial, *rest]
    return ranked[:limit]
