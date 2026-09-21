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
from mr_norm.retrieval.text_normalize import (
    morphology_phrase_matches_query,
    normalize_catalog_text,
)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "config" / "knowledge"
DEFAULT_KNOWLEDGE_PATH = KNOWLEDGE_DIR / "document_knowledge_index.json"
DEFAULT_ABBREVIATIONS_PATH = Path(__file__).resolve().parents[1] / "config" / "abbreviations.json"

# Same quality guard as rag_norm's knowledge-graph importer. The source list
# contains a few bibliography/reference rows that are not domain abbreviations.
_ABBR_BIBLIO_EXPANSION = re.compile(
    r"(Изд-во|ISBN|https?://|библиографическ|Шульц\s+Ю|с\.\s*\d+\s*\.|г\.\s*\d+\s*с\.)",
    re.IGNORECASE,
)
_ABBR_MORPH_MAX_CHARS = 100
_ABBR_MORPH_MAX_TOKENS = 5
_ABBR_COMMON_WORDS = frozenset(
    {
        "по",
        "на",
        "от",
        "из",
        "до",
        "за",
        "for",
        "and",
        "the",
        "of",
        "to",
        "in",
        "on",
        "or",
        "as",
        "an",
        "at",
        "is",
        "be",
        "by",
    }
)

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
    registry_key: str = ""

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
    morphology_terms: tuple[str, ...] = ()
    abbreviation_expansions: tuple[str, ...] = ()
    loose_terms: tuple[str, ...] = ()
    document_hints: tuple[str, ...] = ()

    def flat_terms(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self.exact_phrase_terms,
                    *self.morphology_terms,
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


def _load_external_abbreviations(path: Path = DEFAULT_ABBREVIATIONS_PATH) -> list[dict[str, str]]:
    """Load the copied rag_norm list and convert it to mr_norm's schema."""
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []

    result: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        abbreviation = str(row.get("abbreviation") or "").strip()
        if len(abbreviation) <= 1 or len(abbreviation) > 64:
            continue
        if re.fullmatch(r"[\d.]+", abbreviation) or abbreviation.upper() == "ABB":
            continue
        values = row.get("values") or []
        if not isinstance(values, list):
            continue
        for value in values:
            expansion = str(value or "").strip()[:400]
            if not expansion or _ABBR_BIBLIO_EXPANSION.search(expansion):
                continue
            result.append({"abbreviation": abbreviation, "expansion": expansion})
    return result


def _merge_abbreviations(
    base: list[dict[str, Any]],
    external: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Keep document-specific metadata while adding unique imported pairs."""
    merged = list(base)
    seen = {
        (
            normalize_catalog_text(str(item.get("abbreviation") or "")),
            normalize_catalog_text(str(item.get("expansion") or "")),
        )
        for item in merged
    }
    for item in external:
        key = (
            normalize_catalog_text(item["abbreviation"]),
            normalize_catalog_text(item["expansion"]),
        )
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _abbreviation_expansion_matches(expansion: str, query_norm: str) -> bool:
    """Match a full form exactly, with a bounded fallback for short phrases."""
    if not expansion or expansion in query_norm:
        return bool(expansion)
    if (
        len(expansion) <= _ABBR_MORPH_MAX_CHARS
        and len(expansion.split()) <= _ABBR_MORPH_MAX_TOKENS
    ):
        return morphology_phrase_matches_query(expansion, query_norm)
    return False


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
            registry_key=str(item.get("registry_key") or ""),
        )
        for item in payload.get("documents") or []
        if item.get("doc_id")
    ]
    abbreviations = list(payload.get("abbreviations") or [])
    # Explicit fixture/index paths remain self-contained for tests and tools;
    # the copied project-wide list is added only to the default runtime index.
    if path is None:
        abbreviations = _merge_abbreviations(abbreviations, _load_external_abbreviations())
    return DocumentKnowledgeIndex(
        documents=documents,
        abbreviations=abbreviations,
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


def prune_exact_phrases(phrases: list[str]) -> list[str]:
    if not phrases:
        return []
    items = [(phrase, normalize_catalog_text(phrase)) for phrase in phrases]
    kept: list[str] = []
    for phrase, norm in items:
        words = norm.split()
        if len(words) == 1 and norm in LOOSE_TOPIC_TOKENS:
            continue
        dominated = False
        for other_phrase, other_norm in items:
            if phrase == other_phrase:
                continue
            if len(other_norm.split()) > len(words) and norm in other_norm:
                dominated = True
                break
        if not dominated:
            kept.append(phrase)
    kept.sort(key=lambda item: len(normalize_catalog_text(item).split()), reverse=True)
    return kept


def primary_exact_phrase(phrases: tuple[str, ...] | list[str]) -> str:
    pruned = prune_exact_phrases(list(phrases))
    return pruned[0] if pruned else ""


COMMON_PHRASE_FILLERS = frozenset(
    {
        "правила",
        "технической",
        "эксплуатации",
        "электрических",
        "электроустановок",
        "электроэнергетике",
        "требования",
        "порядок",
        "станций",
        "сетей",
        "работы",
        "меры",
    }
)

MAX_REQUIRED_TOKENS = 3
MAX_AUTO_REQUIRED_TOKENS = 2
AUTO_REQUIRED_TOKEN_MIN_LEN = 5
SKIP_AUTO_REQUIRED_QUESTION_TYPES = frozenset({"point_lookup", "document_lookup"})


def phrase_required_tokens(phrase: str) -> tuple[str, ...]:
    phrase_norm = normalize_catalog_text(phrase)
    words = [token for token in phrase_norm.split() if len(token) >= 4]
    if len(words) > 5:
        distinctive = [token for token in words if token not in COMMON_PHRASE_FILLERS]
        if len(distinctive) >= 2:
            return tuple(distinctive[:MAX_REQUIRED_TOKENS])
        return ()

    distinctive = [token for token in words if token not in COMMON_PHRASE_FILLERS]
    if len(distinctive) >= 2:
        return tuple(distinctive[:MAX_REQUIRED_TOKENS])

    if len(words) >= 2:
        return tuple(words[:MAX_REQUIRED_TOKENS])

    tokens = _significant_phrase_tokens(phrase_norm)
    if len(tokens) >= 2:
        return tuple(tokens[:MAX_REQUIRED_TOKENS])
    if phrase_norm and len(phrase_norm) >= 8:
        return (phrase_norm,)
    return ()


def query_required_tokens(
    query: str,
    significant_words: list[str],
    *,
    question_type: str = "factual",
) -> tuple[str, ...]:
    """Pick up to 2 distinctive tokens for payload `must` when no exact phrase."""
    if (question_type or "").strip() in SKIP_AUTO_REQUIRED_QUESTION_TYPES:
        return ()
    query_norm = normalize_catalog_text(query)
    if not query_norm:
        return ()

    distinctive: list[str] = []
    seen: set[str] = set()
    for word in significant_words or []:
        for token in normalize_catalog_text(str(word)).split():
            if len(token) < AUTO_REQUIRED_TOKEN_MIN_LEN:
                continue
            if token in COMMON_PHRASE_FILLERS:
                continue
            if token not in query_norm:
                continue
            if token in seen:
                continue
            seen.add(token)
            distinctive.append(token)
    if len(distinctive) < 2:
        return ()
    return tuple(distinctive[:MAX_AUTO_REQUIRED_TOKENS])


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
        expansion = normalize_catalog_text(str(abbr.get("expansion") or ""))
        if abbreviation in _ABBR_COMMON_WORDS:
            continue
        abbreviation_match = bool(
            abbreviation and re.search(rf"\b{re.escape(abbreviation)}\b", query_norm)
        )
        expansion_match = _abbreviation_expansion_matches(expansion, query_norm)
        if len(abbreviation) < 2 or not (abbreviation_match or expansion_match):
            continue
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
    morphology: list[str] = []
    abbreviations: list[str] = []
    loose: list[str] = []
    doc_hints: list[str] = []
    abbreviation_groups: dict[str, tuple[str, list[str]]] = {}
    matched_abbreviation_keys: list[str] = []

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
        elif morphology_phrase_matches_query(label, query):
            _append_unique(morphology, label)
        elif label_norm in query_norm:
            _append_unique(loose, label)

    for topic in active_topic_aliases(knowledge.topic_aliases, enable_pue_aliases=enable_pue_aliases):
        phrase = str(topic.get("phrase") or "").strip()
        if phrase:
            if phrase_matches_query(phrase, query):
                _append_unique(exact_phrases, phrase)
            elif morphology_phrase_matches_query(phrase, query):
                _append_unique(morphology, phrase)
        for search_term in topic.get("search_terms") or []:
            term = str(search_term).strip()
            if not term:
                continue
            if phrase_matches_query(term, query):
                _append_unique(exact_phrases, term)
            elif morphology_phrase_matches_query(term, query):
                _append_unique(morphology, term)

    pue_norm = normalize_catalog_text(PUE_ALIAS_KEY)
    if pue_norm in query_norm:
        _append_unique(doc_hints, PUE_ALIAS_KEY)

    for abbr in knowledge.abbreviations:
        if not enable_pue_aliases and is_pue_abbreviation_entry(abbr):
            continue
        abbreviation = str(abbr.get("abbreviation") or "").strip()
        abbr_norm = normalize_catalog_text(abbreviation)
        expansion = str(abbr.get("expansion") or "").strip()
        expansion_norm = normalize_catalog_text(expansion)
        if abbr_norm and expansion:
            group = abbreviation_groups.setdefault(abbr_norm, (abbreviation, []))
            if expansion not in group[1]:
                group[1].append(expansion)
        if abbr_norm in _ABBR_COMMON_WORDS:
            continue
        abbreviation_match = bool(
            abbr_norm and re.search(rf"\b{re.escape(abbr_norm)}\b", query_norm)
        )
        expansion_match = _abbreviation_expansion_matches(expansion_norm, query_norm)
        if not abbr_norm or not (abbreviation_match or expansion_match):
            continue
        _append_unique(doc_hints, abbreviation)
        if abbr_norm not in matched_abbreviation_keys:
            matched_abbreviation_keys.append(abbr_norm)
        # Retrieval gets both spellings regardless of which spelling the user
        # used. The original question is retained separately by the planner.
        if abbreviation_match:
            _append_unique(abbreviations, expansion)
            _append_unique(abbreviations, abbreviation)
        else:
            _append_unique(abbreviations, abbreviation)
            _append_unique(abbreviations, expansion)

    # Several values may belong to one abbreviation (for example, the common
    # short form and the formal full name of ВЛ). Once any value matches, keep
    # the complete group so equivalent phrasings produce the same search set.
    for abbr_norm in matched_abbreviation_keys:
        abbreviation, expansions = abbreviation_groups[abbr_norm]
        if len(expansions) <= 1:
            continue
        variants_norm = {
            normalize_catalog_text(item)
            for item in (*expansions, abbreviation)
        }
        abbreviations[:] = [
            item
            for item in abbreviations
            if normalize_catalog_text(item) not in variants_norm
        ]
        for expansion in expansions:
            _append_unique(abbreviations, expansion)
        _append_unique(abbreviations, abbreviation)

    exact_phrases = prune_exact_phrases(exact_phrases)[:limit]
    morphology = [
        term
        for term in morphology
        if term not in exact_phrases
    ][:limit]
    abbreviations = abbreviations[:limit]
    loose = [
        term
        for term in loose
        if term not in exact_phrases and term not in morphology
    ][:limit]
    doc_hints = doc_hints[:limit]

    return QueryTermMatches(
        exact_phrase_terms=tuple(exact_phrases),
        morphology_terms=tuple(morphology),
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


def find_topic_alias_document_candidates(
    query: str,
    knowledge: DocumentKnowledgeIndex,
    *,
    enable_pue_aliases: bool = False,
) -> list[KnowledgeCandidate]:
    query_norm = normalize_catalog_text(query)
    if not query_norm or not knowledge.documents:
        return []

    ranked: list[KnowledgeCandidate] = []
    for topic in active_topic_aliases(knowledge.topic_aliases, enable_pue_aliases=enable_pue_aliases):
        phrase_raw = str(topic.get("phrase") or "")
        phrase = normalize_catalog_text(phrase_raw)
        if not phrase:
            continue
        search_terms = [str(item) for item in topic.get("search_terms") or ()]
        matched_phrase = (
            phrase_matches_query(phrase_raw, query)
            or morphology_phrase_matches_query(phrase_raw, query)
            or any(phrase_matches_query(term, query) for term in search_terms)
            or any(morphology_phrase_matches_query(term, query) for term in search_terms)
        )
        if not matched_phrase:
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
                    score=0.85,
                    reasons=(f"topic_alias:{phrase}",),
                    annotation=document.annotation,
                )
            )

    merged: dict[str, KnowledgeCandidate] = {}
    for candidate in ranked:
        current = merged.get(candidate.doc_id)
        if current is None or candidate.score > current.score:
            merged[candidate.doc_id] = candidate
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)


def find_abbreviation_document_candidates(
    query: str,
    knowledge: DocumentKnowledgeIndex,
    *,
    enable_pue_aliases: bool = False,
) -> list[KnowledgeCandidate]:
    """Resolve short labels via knowledge abbreviations (word-boundary match)."""
    query_norm = normalize_catalog_text(query)
    if not query_norm or not knowledge.abbreviations or not knowledge.documents:
        return []

    ranked: list[KnowledgeCandidate] = []
    for abbr in knowledge.abbreviations:
        if not enable_pue_aliases and is_pue_abbreviation_entry(abbr):
            continue
        abbreviation = normalize_catalog_text(str(abbr.get("abbreviation") or ""))
        if len(abbreviation) < 2:
            continue
        if abbreviation in _ABBR_COMMON_WORDS:
            continue
        expansion = normalize_catalog_text(str(abbr.get("expansion") or ""))
        abbreviation_match = bool(re.search(rf"\b{re.escape(abbreviation)}\b", query_norm))
        expansion_match = _abbreviation_expansion_matches(expansion, query_norm)
        if not (abbreviation_match or expansion_match):
            continue
        positive = [
            normalize_catalog_text(item)
            for item in abbr.get("doc_name_substrings") or ([expansion] if expansion else [])
        ]
        positive = [item for item in positive if item]
        if not positive:
            continue
        negative = [
            normalize_catalog_text(item) for item in abbr.get("negative_doc_name_substrings") or []
        ]
        for document in knowledge.documents:
            doc_norm = normalize_catalog_text(document.doc_name)
            if negative and any(item in doc_norm for item in negative if item):
                continue
            if not any(item in doc_norm for item in positive):
                continue
            ranked.append(
                KnowledgeCandidate(
                    doc_id=document.doc_id,
                    doc_name=document.doc_name,
                    score=0.92,
                    reasons=(f"abbreviation:{abbreviation}",),
                    annotation=document.annotation,
                )
            )

    merged: dict[str, KnowledgeCandidate] = {}
    for candidate in ranked:
        current = merged.get(candidate.doc_id)
        if current is None or candidate.score > current.score:
            merged[candidate.doc_id] = candidate
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)
