from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mr_norm.retrieval.document_catalog import normalize_catalog_text

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "config" / "knowledge"
DEFAULT_KNOWLEDGE_PATH = KNOWLEDGE_DIR / "document_knowledge_index.json"


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


@dataclass
class DocumentKnowledgeIndex:
    documents: list[KnowledgeDocument] = field(default_factory=list)
    abbreviations: list[dict[str, str]] = field(default_factory=list)
    terms: list[dict[str, str]] = field(default_factory=list)
    graph_abbreviations: list[dict[str, str]] = field(default_factory=list)
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
        graph_abbreviations=list(payload.get("graph_abbreviations") or []),
        topic_aliases=list(payload.get("topic_aliases") or []),
        source_path=str(knowledge_path),
    )


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

    for topic in knowledge.topic_aliases:
        phrase = normalize_catalog_text(str(topic.get("phrase") or ""))
        if not phrase:
            continue
        if phrase not in query_norm and not any(token in query_norm for token in phrase.split() if len(token) >= 5):
            continue
        positive = [normalize_catalog_text(item) for item in topic.get("doc_name_substrings") or []]
        negative = [normalize_catalog_text(item) for item in topic.get("negative_doc_name_substrings") or []]
        for document in knowledge.documents:
            doc_norm = normalize_catalog_text(document.doc_name)
            if negative and any(item in doc_norm for item in negative):
                continue
            if not positive or not any(item in doc_norm for item in positive):
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


def match_terms_in_query(query: str, knowledge: DocumentKnowledgeIndex, *, limit: int = 12) -> list[str]:
    query_norm = normalize_catalog_text(query)
    matched: list[str] = []
    for term in knowledge.terms:
        label = str(term.get("label") or "").strip()
        label_norm = normalize_catalog_text(label)
        if len(label_norm) >= 5 and label_norm in query_norm and label not in matched:
            matched.append(label)
        if len(matched) >= limit:
            break
    for topic in knowledge.topic_aliases:
        phrase = str(topic.get("phrase") or "").strip()
        phrase_norm = normalize_catalog_text(phrase)
        if phrase_norm and phrase_norm in query_norm and phrase not in matched:
            matched.append(phrase)
    for abbr in knowledge.abbreviations:
        abbreviation = str(abbr.get("abbreviation") or "").strip()
        abbr_norm = normalize_catalog_text(abbreviation)
        if abbr_norm and abbr_norm in query_norm:
            expansion = str(abbr.get("expansion") or "").strip()
            if expansion and expansion not in matched:
                matched.append(expansion)
        if len(matched) >= limit:
            break
    return matched[:limit]
