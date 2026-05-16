from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mr_norm.config.pue_aliases import (
    PUE_ALIAS_KEY,
    active_topic_aliases,
    is_pue_abbreviation_entry,
)
from mr_norm.retrieval.text_normalize import normalize_catalog_text

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "config" / "knowledge"
DEFAULT_KNOWLEDGE_PATH = KNOWLEDGE_DIR / "document_knowledge_index.json"

# Tokens that alone must not activate a multi-word topic alias.
LOOSE_TOPIC_TOKENS = frozenset(
    {
        "напряжение",
        "напряжением",
        "безопасность",
        "безопасно",
        "безопасные",
        "работы",
        "правила",
        "требования",
        "электроустановках",
        "электроустановок",
        "электроустановки",
        "режима",
        "режим",
        "управление",
        "управления",
        "защита",
        "автоматика",
    }
)

PUE_DOC_MARKERS = (
    "правила устройства электроустановок",
    "электроустановок",
)


@dataclass(frozen=True)
class KnowledgeDocument:
    doc_id: str
    doc_name: str
    annotation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeCandidate:
    doc_id: str
    doc_name: str
    score: float
    reasons: tuple[str, ...] = ()
    annotation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "annotation": self.annotation,
        }


@dataclass(frozen=True)
class QueryTermMatches:
    exact_phrase_terms: tuple[str, ...] = ()
    abbreviation_expansions: tuple[str, ...] = ()
    loose_terms: tuple[str, ...] = ()
    document_hints: tuple[str, ...] = ()

    def flat_terms(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self.exact_phrase_terms,
                    *self.abbreviation_expansions,
                    *self.loose_terms,
                    *self.document_hints,
                ]
            )
        )


@dataclass
class DocumentKnowledgeIndex:
    documents: list[KnowledgeDocument] = field(default_factory=list)
    abbreviations: list[dict[str, str]] = field(default_factory=list)
    terms: list[dict[str, str]] = field(default_factory=list)
    topic_aliases: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""

    def by_doc_id(self) -> dict[str, KnowledgeDocument]:
        return {doc.doc_id: doc for doc in self.documents if doc.doc_id}

    def by_doc_name(self) -> dict[str, KnowledgeDocument]:
        return {doc.doc_name: doc for doc in self.documents if doc.doc_name}


def load_document_knowledge(path: Path | None = None) -> DocumentKnowledgeIndex:
    knowledge_path = path or DEFAULT_KNOWLEDGE_PATH
    if not knowledge_path.is_file():
        return DocumentKnowledgeIndex(source_path=str(knowledge_path))

    payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    documents = [
        KnowledgeDocument(
            doc_id=str(item.get("doc_id") or ""),
            doc_name=str(item.get("doc_name") or ""),
            annotation=str(item.get("annotation") or ""),
        )
        for item in payload.get("documents") or []
        if item.get("doc_id")
    ]
    return DocumentKnowledgeIndex(
        documents=documents,
        abbreviations=list(payload.get("abbreviations") or []),
        terms=list(payload.get("terms") or []),
        topic_aliases=list(payload.get("topic_aliases") or []),
        source_path=str(knowledge_path),
    )


def _significant_phrase_tokens(phrase_norm: str) -> list[str]:
    return [
        token
        for token in phrase_norm.split()
        if len(token) >= 4 and token not in LOOSE_TOPIC_TOKENS
    ]


def phrase_matches_query(phrase: str, query: str) -> bool:
    phrase_norm = normalize_catalog_text(phrase)
    query_norm = normalize_catalog_text(query)
    if not phrase_norm or not query_norm:
        return False
    if phrase_norm in query_norm:
        return True
    tokens = _significant_phrase_tokens(phrase_norm)
    if len(tokens) < 2:
        return phrase_norm in query_norm
    return all(token in query_norm for token in tokens)


def phrase_required_tokens(phrase: str) -> tuple[str, ...]:
    phrase_norm = normalize_catalog_text(phrase)
    words = [token for token in phrase_norm.split() if len(token) >= 3]
    if len(words) >= 2:
        return tuple(words)
    tokens = _significant_phrase_tokens(phrase_norm)
    if len(tokens) >= 2:
        return tuple(tokens)
    if phrase_norm:
        return (phrase_norm,)
    return ()


def is_pue_document_name(doc_name: str) -> bool:
    doc_norm = normalize_catalog_text(doc_name)
    return any(marker in doc_norm for marker in PUE_DOC_MARKERS)


def _token_overlap(query_norm: str, text_norm: str) -> float:
    query_tokens = [token for token in query_norm.split() if len(token) >= 4]
    if not query_tokens:
        return 0.0
    hits = sum(1 for token in query_tokens if token in text_norm)
    return hits / len(query_tokens)


def _score_document(query_norm: str, document: KnowledgeDocument) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    doc_name_norm = normalize_catalog_text(document.doc_name)
    annotation_norm = normalize_catalog_text(document.annotation)

    if query_norm and query_norm in doc_name_norm:
        score += 0.45
        reasons.append("doc_name_contains_query")
    if doc_name_norm and doc_name_norm in query_norm:
        score += 0.35
        reasons.append("query_contains_doc_name")

    overlap = _token_overlap(query_norm, doc_name_norm)
    if overlap >= 0.5:
        score += overlap * 0.5
        reasons.append(f"doc_name_token_overlap:{overlap:.2f}")

    if query_norm and query_norm in annotation_norm:
        score += 0.35
        reasons.append("annotation_contains_query")

    annotation_overlap = _token_overlap(query_norm, annotation_norm)
    if annotation_overlap >= 0.45:
        score += annotation_overlap * 0.45
        reasons.append(f"annotation_token_overlap:{annotation_overlap:.2f}")

    ratio = SequenceMatcher(None, query_norm, doc_name_norm).ratio() if query_norm and doc_name_norm else 0.0
    if ratio >= 0.55:
        score += ratio * 0.25
        reasons.append(f"fuzzy_doc_name:{ratio:.2f}")

    return score, reasons


def find_knowledge_candidates(
    query: str,
    knowledge: DocumentKnowledgeIndex,
    *,
    limit: int = 12,
    enable_pue_aliases: bool = False,
) -> list[KnowledgeCandidate]:
    query_norm = normalize_catalog_text(query)
    if not query_norm or not knowledge.documents:
        return []

    ranked: list[KnowledgeCandidate] = []
    for document in knowledge.documents:
        score, reasons = _score_document(query_norm, document)
        if score <= 0:
            continue
        ranked.append(
            KnowledgeCandidate(
                doc_id=document.doc_id,
                doc_name=document.doc_name,
                score=score,
                reasons=tuple(reasons),
                annotation=document.annotation,
            )
        )

    for topic in active_topic_aliases(knowledge.topic_aliases, enable_pue_aliases=enable_pue_aliases):
        phrase_raw = str(topic.get("phrase") or "")
        phrase = normalize_catalog_text(phrase_raw)
        if not phrase:
            continue
        if not phrase_matches_query(phrase_raw, query):
            continue
        positive = [normalize_catalog_text(item) for item in topic.get("doc_name_substrings") or []]
        negative = [normalize_catalog_text(item) for item in topic.get("negative_doc_name_substrings") or []]
        mentions_pue = normalize_catalog_text(PUE_ALIAS_KEY) in query_norm
        for document in knowledge.documents:
            doc_norm = normalize_catalog_text(document.doc_name)
            if negative and any(item in doc_norm for item in negative):
                continue
            if not positive or not any(item in doc_norm for item in positive):
                continue
            if (
                not enable_pue_aliases
                and not mentions_pue
                and is_pue_document_name(document.doc_name)
            ):
                continue
            ranked.append(
                KnowledgeCandidate(
                    doc_id=document.doc_id,
                    doc_name=document.doc_name,
                    score=0.72,
                    reasons=(f"topic_alias:{phrase}",),
                    annotation=document.annotation,
                )
            )

    for abbr in knowledge.abbreviations:
        if not enable_pue_aliases and is_pue_abbreviation_entry(abbr):
            continue
        abbreviation = normalize_catalog_text(str(abbr.get("abbreviation") or ""))
        if len(abbreviation) < 2 or abbreviation not in query_norm:
            continue
        expansion = normalize_catalog_text(str(abbr.get("expansion") or ""))
        if not expansion:
            continue
        for document in knowledge.documents:
            doc_norm = normalize_catalog_text(document.doc_name + " " + document.annotation)
            if expansion[:40] in doc_norm or _token_overlap(expansion, doc_norm) >= 0.35:
                ranked.append(
                    KnowledgeCandidate(
                        doc_id=document.doc_id,
                        doc_name=document.doc_name,
                        score=0.48,
                        reasons=(f"abbreviation:{abbreviation}",),
                        annotation=document.annotation,
                    )
                )

    for term in knowledge.terms:
        label = normalize_catalog_text(str(term.get("label") or ""))
        if len(label) < 5 or label not in query_norm:
            continue
        for document in knowledge.documents:
            doc_norm = normalize_catalog_text(document.doc_name + " " + document.annotation)
            if label in doc_norm:
                ranked.append(
                    KnowledgeCandidate(
                        doc_id=document.doc_id,
                        doc_name=document.doc_name,
                        score=0.42,
                        reasons=(f"term:{label}",),
                        annotation=document.annotation,
                    )
                )

    merged: dict[str, KnowledgeCandidate] = {}
    for candidate in ranked:
        current = merged.get(candidate.doc_id)
        if current is None or candidate.score > current.score:
            merged[candidate.doc_id] = candidate
        elif current and candidate.doc_id in merged:
            merged_reasons = list(dict.fromkeys((*merged[candidate.doc_id].reasons, *candidate.reasons)))
            merged[candidate.doc_id] = KnowledgeCandidate(
                doc_id=merged[candidate.doc_id].doc_id,
                doc_name=merged[candidate.doc_id].doc_name,
                score=max(merged[candidate.doc_id].score, candidate.score),
                reasons=tuple(merged_reasons),
                annotation=merged[candidate.doc_id].annotation,
            )

    result = sorted(merged.values(), key=lambda item: item.score, reverse=True)
    return result[:limit]


def match_query_terms(
    query: str,
    knowledge: DocumentKnowledgeIndex | None = None,
    *,
    limit: int = 12,
    enable_pue_aliases: bool = False,
) -> QueryTermMatches:
    knowledge = knowledge or load_document_knowledge()
    query_norm = normalize_catalog_text(query)
    exact_phrases: list[str] = []
    abbreviations: list[str] = []
    loose: list[str] = []
    doc_hints: list[str] = []

    def _append_unique(bucket: list[str], value: str) -> None:
        if value and value not in bucket:
            bucket.append(value)

    for term in knowledge.terms:
        label = str(term.get("label") or "").strip()
        if not label:
            continue
        label_norm = normalize_catalog_text(label)
        if len(label_norm) < 5:
            continue
        if phrase_matches_query(label, query):
            _append_unique(exact_phrases, label)
        elif label_norm in query_norm:
            _append_unique(loose, label)

    for topic in active_topic_aliases(knowledge.topic_aliases, enable_pue_aliases=enable_pue_aliases):
        phrase = str(topic.get("phrase") or "").strip()
        if phrase and phrase_matches_query(phrase, query):
            _append_unique(exact_phrases, phrase)
        for search_term in topic.get("search_terms") or []:
            term = str(search_term).strip()
            if term and phrase_matches_query(term, query):
                _append_unique(exact_phrases, term)

    pue_norm = normalize_catalog_text(PUE_ALIAS_KEY)
    if pue_norm in query_norm:
        _append_unique(doc_hints, PUE_ALIAS_KEY)

    for abbr in knowledge.abbreviations:
        if not enable_pue_aliases and is_pue_abbreviation_entry(abbr):
            continue
        abbreviation = str(abbr.get("abbreviation") or "").strip()
        abbr_norm = normalize_catalog_text(abbreviation)
        if not abbr_norm or abbr_norm not in query_norm:
            continue
        _append_unique(doc_hints, abbreviation)
        expansion = str(abbr.get("expansion") or "").strip()
        if expansion:
            _append_unique(abbreviations, expansion)

    exact_phrases = exact_phrases[:limit]
    abbreviations = abbreviations[:limit]
    loose = [term for term in loose if term not in exact_phrases][:limit]
    doc_hints = doc_hints[:limit]

    return QueryTermMatches(
        exact_phrase_terms=tuple(exact_phrases),
        abbreviation_expansions=tuple(abbreviations),
        loose_terms=tuple(loose),
        document_hints=tuple(doc_hints),
    )


def match_terms_in_query(
    query: str,
    knowledge: DocumentKnowledgeIndex,
    *,
    limit: int = 12,
    enable_pue_aliases: bool = False,
) -> list[str]:
    matches = match_query_terms(
        query,
        knowledge,
        limit=limit,
        enable_pue_aliases=enable_pue_aliases,
    )
    return matches.flat_terms()[:limit]
