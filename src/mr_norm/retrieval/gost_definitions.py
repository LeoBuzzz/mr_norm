"""Prefetch term definitions from GOST R 57114 for query planning and final evidence."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.document_catalog import extract_point_number_hint, normalize_catalog_text
from mr_norm.retrieval.filters import build_filter_spec
from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient

GOST_REGISTRY_KEY = "gostr_57114_29122022"
GOST_DOC_ID = "doc_4745dec28ca589e1"

GOST_TERM_STOP_WORDS: frozenset[str] = frozenset(
    {
        "это",
        "что",
        "такое",
        "как",
        "для",
        "при",
        "про",
        "кто",
        "чем",
        "кого",
        "кому",
        "расскажи",
        "опиши",
        "расшифруй",
        "составь",
        "напиши",
        "дай",
        "дайте",
        "скажи",
        "скажите",
        "назови",
        "укажи",
        "перечисли",
        "означает",
        "называют",
        "относят",
        "требования",
        "определение",
        "определения",
        "термин",
        "термина",
        "термины",
        "понятие",
        "всесторонне",
        "рассмотреть",
        "вопросы",
        "план",
        "ответа",
        "какая",
        "какой",
        "какое",
        "какие",
        "какую",
        "где",
        "когда",
        "зачем",
        "почему",
        "работа",
        "группу",
        "группой",
        "группе",
        "как",
        "же",
        "бы",
        "лишь",
        "ведь",
        "либо",
        "или",
        "ли",
    }
)

DEFINITION_INDICATORS = ("это", "определение", "определяется", "означает", "называется")
GOST_PREFETCH_MARKERS = (
    "гост",
    "термин",
    "определ",
    "что такое",
    "кто такой",
    "кто такая",
    "кто такие",
    "что называется",
    "означает",
    "расшифруй",
)
GOST_KNOWN_TERM_HINTS = (
    "оперативный персонал",
    "диспетчерский персонал",
    "объект диспетчеризации",
    "энергосистема",
)


class QueryEmbedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class GostSnippet:
    keyword: str
    text: str
    doc_name: str
    point_number: str
    chunk_id: str
    doc_id: str
    score: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GostSnippet:
        return cls(
            keyword=str(payload.get("keyword") or ""),
            text=str(payload.get("text") or ""),
            doc_name=str(payload.get("doc_name") or ""),
            point_number=str(payload.get("point_number") or ""),
            chunk_id=str(payload.get("chunk_id") or ""),
            doc_id=str(payload.get("doc_id") or GOST_DOC_ID),
            score=payload.get("score"),
        )

    def to_retrieved_item(self) -> RetrievedItem:
        return RetrievedItem(
            chunk_id=self.chunk_id,
            doc_id=self.doc_id,
            doc_name=self.doc_name,
            point_number=self.point_number,
            text=self.text,
            score=self.score if self.score is not None else 1.0,
            source_tool="gost_definition",
            matched={"gost_keyword": self.keyword},
        )


def should_prefetch_gost(query: str, filters: dict[str, Any] | None = None) -> bool:
    if extract_point_number_hint(query):
        return False
    if str((filters or {}).get("point_number") or "").strip():
        return False
    norm = normalize_catalog_text(query)
    if not norm:
        return False
    if any(marker in norm for marker in GOST_PREFETCH_MARKERS):
        return True
    return any(normalize_catalog_text(term) in norm for term in GOST_KNOWN_TERM_HINTS)


def _refine_gost_search_terms(terms: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms:
        value = (term or "").strip().lower().strip("\"'«»()[]")
        if len(value) < 3 or value in GOST_TERM_STOP_WORDS:
            continue
        if not any(ch.isalnum() for ch in value):
            continue
        if value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    multi = [item for item in cleaned if " " in item]
    out: list[str] = []
    for item in cleaned:
        if " " not in item:
            drop = any(item in phrase.split() for phrase in multi)
            if drop:
                continue
        out.append(item)
    return out[:6]


def extract_gost_search_terms(query: str) -> list[str]:
    clean_query = re.sub(r"[^\w\s]", " ", query or "")
    words = [word for word in clean_query.split() if word]
    terms: list[str] = []
    for word in words:
        token = word.lower().strip()
        if len(token) > 3 and token not in GOST_TERM_STOP_WORDS and token.isalpha():
            if token not in terms:
                terms.append(token)
    for index in range(len(words) - 1):
        phrase = f"{words[index].lower()} {words[index + 1].lower()}"
        if len(phrase) > 5 and not any(part in GOST_TERM_STOP_WORDS for part in phrase.split()):
            if phrase not in terms:
                terms.append(phrase)
    refined = _refine_gost_search_terms(terms)
    extra: list[str] = []
    joined = " ".join(refined)
    if "оператив" in joined and "персонал" in joined and "оперативный персонал" not in joined:
        extra.append("оперативный персонал")
    return list(dict.fromkeys([*refined, *extra]))[:8]


def _term_token_roots(term: str) -> list[str]:
    roots: list[str] = []
    for token in normalize_catalog_text(term).split():
        if len(token) < 4:
            continue
        roots.append(token[: max(5, len(token) - 2)])
    return roots


def _term_matches_text(term: str, text: str) -> bool:
    text_norm = normalize_catalog_text(text)
    term_norm = normalize_catalog_text(term)
    if term_norm and term_norm in text_norm:
        return True
    roots = _term_token_roots(term)
    if not roots:
        return False
    return all(root in text_norm for root in roots)


def _extract_inline_point_for_term(text: str, canonical_term: str) -> str:
    term_norm = re.escape(normalize_catalog_text(canonical_term))
    match = re.search(rf"(\d+(?:\.\d+)*)\s+{term_norm}\s*:", normalize_catalog_text(text))
    if match:
        return match.group(1)
    roots = _term_token_roots(canonical_term)
    if len(roots) >= 2:
        pattern = (
            rf"(\d+(?:\.\d+)*)\s+[^\n:{{}}]{{0,80}}{re.escape(roots[0])}"
            rf"[^\n:{{}}]{{0,80}}{re.escape(roots[1])}\s*:"
        )
        match = re.search(pattern, normalize_catalog_text(text))
        if match:
            return match.group(1)
    return ""


def _looks_like_definition(text: str, term: str) -> bool:
    preview = (text or "").lower()[:400]
    if any(marker in preview for marker in DEFINITION_INDICATORS):
        return True
    if _term_matches_text(term, text):
        return True
    words = normalize_catalog_text(term).split()
    if len(words) >= 2:
        nominative = f"{words[0][:8]} {words[1][:6]}"
        if nominative.strip() and nominative in normalize_catalog_text(text):
            return True
    return False


def _rank_snippet(snippet: GostSnippet, canonical_term: str, expected_point: str) -> float:
    score = float(snippet.score or 0.0)
    inline = _extract_inline_point_for_term(snippet.text, canonical_term)
    if _term_matches_text(canonical_term, snippet.text):
        score += 2.0
    if expected_point and inline.startswith(str(expected_point)):
        score += 3.0
    elif inline:
        score += 1.0
    return score


def fetch_gost_definitions(
    query: str,
    config: IndexingConfig,
    embedder: QueryEmbedder,
    *,
    client: QdrantRetrievalClient | None = None,
    doc_id: str = GOST_DOC_ID,
    limit_per_term: int = 5,
    max_snippets: int = 4,
    canonical_hint: str = "",
    expected_point_hint: str = "",
) -> list[GostSnippet]:
    terms = extract_gost_search_terms(query)
    if not terms:
        return []

    client = client or QdrantRetrievalClient(config)
    filter_spec = build_filter_spec({"doc_id": doc_id})
    per_term_best: dict[str, GostSnippet] = {}
    seen_text_keys: set[str] = set()

    for term in terms:
        term_candidates: list[GostSnippet] = []
        query_variants = (f"определение {term}", term, f"{term} это")
        vectors = embedder.encode(list(query_variants))
        for query_text, vector in zip(query_variants, vectors, strict=True):
            items = client.vector_search(
                vector,
                filter_spec,
                limit=limit_per_term,
                source_tool="gost_definition",
            )
            for item in items:
                if not _looks_like_definition(item.text, term):
                    continue
                text_key = item.text[:120]
                if text_key in seen_text_keys:
                    continue
                seen_text_keys.add(text_key)
                canonical = canonical_hint or term
                term_candidates.append(
                    GostSnippet(
                        keyword=term,
                        text=item.text,
                        doc_name=item.doc_name,
                        point_number=item.point_number
                        or _extract_inline_point_for_term(item.text, canonical),
                        chunk_id=item.chunk_id,
                        doc_id=item.doc_id or doc_id,
                        score=item.score,
                    )
                )
        if term_candidates:
            canonical = canonical_hint or term
            best = max(
                term_candidates,
                key=lambda snippet: _rank_snippet(snippet, canonical, expected_point_hint),
            )
            per_term_best[term] = best

    snippets = list(per_term_best.values())
    if canonical_hint:
        snippets.sort(
            key=lambda snippet: _rank_snippet(snippet, canonical_hint, expected_point_hint),
            reverse=True,
        )
    else:
        snippets.sort(key=lambda item: float(item.score or 0.0), reverse=True)
    return snippets[:max_snippets]


def merge_gost_into_evidence(
    items: list[RetrievedItem],
    snippets: list[GostSnippet],
) -> list[RetrievedItem]:
    if not snippets:
        return items
    front = [snippet.to_retrieved_item() for snippet in snippets]
    seen = {item.chunk_id for item in front if item.chunk_id}
    merged = list(front)
    for item in items:
        if item.chunk_id and item.chunk_id in seen:
            continue
        merged.append(item)
    return merged


def gost_snippet_in_top_evidence(
    items: list[RetrievedItem],
    snippets: list[GostSnippet],
    *,
    top_n: int = 5,
) -> bool:
    """True when a prefetched GOST chunk was already in the top-N retrieval pool."""
    if not items or not snippets:
        return False
    gost_chunk_ids = {snippet.chunk_id for snippet in snippets if snippet.chunk_id}
    if not gost_chunk_ids:
        return False
    return any(item.chunk_id in gost_chunk_ids for item in items[:top_n])


def enrich_tool_queries_with_gost(
    tool_queries: dict[str, list[str]],
    snippets: list[GostSnippet],
    *,
    original_query: str,
) -> dict[str, list[str]]:
    if not snippets:
        return tool_queries
    merged = {tool: list(values) for tool, values in tool_queries.items()}
    terms = list(dict.fromkeys(snippet.keyword for snippet in snippets))
    for term in terms:
        for tool in ("vector", "payload"):
            bucket = merged.setdefault(tool, [])
            for candidate in (term, f"определение {term}", original_query.strip()):
                if candidate and candidate not in bucket:
                    bucket.append(candidate)
            merged[tool] = bucket[:4]
    return merged


_gost_embedder: Any | None = None
_gost_client: QdrantRetrievalClient | None = None


def prefetch_gost_snippets(
    query: str,
    config: IndexingConfig,
    filters: dict[str, Any] | None = None,
) -> list[GostSnippet]:
    if not should_prefetch_gost(query, filters):
        return []
    global _gost_embedder, _gost_client
    if _gost_embedder is None or _gost_client is None:
        from mr_norm.indexing.qdrant_adapter import SentenceTransformerEmbedder

        _gost_embedder = SentenceTransformerEmbedder(config)
        _gost_client = QdrantRetrievalClient(config)
    return fetch_gost_definitions(
        query,
        config,
        _gost_embedder,
        client=_gost_client,
    )
