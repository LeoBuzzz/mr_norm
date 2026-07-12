from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.config.pue_aliases import active_known_query_aliases, resolve_enable_pue_aliases
from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCandidate,
    extract_order_numbers,
    find_catalog_candidates,
    is_generic_tech_reg_doc_name,
    is_junk_catalog_hit,
    load_default_document_catalog,
    normalize_catalog_text,
    resolve_by_order_number_hint,
    resolve_by_partial_order_hint,
)
from mr_norm.retrieval.document_knowledge import (
    find_abbreviation_document_candidates,
    find_topic_alias_document_candidates,
    load_document_knowledge,
)
from mr_norm.retrieval.knowledge_catalog_mapping import (
    KnowledgeCatalogLink,
    load_knowledge_catalog_mapping,
)
from mr_norm.runtime.pipeline_features import query_has_explicit_doc_reference

AMBIGUITY_SCORE_GAP = 0.08
EXACT_SCORE_THRESHOLD = 0.85
PROBABLE_SCORE_THRESHOLD = 0.55
LABEL_PROBABLE_SCORE_THRESHOLD = 0.30
DIRECT_TITLE_SCORE_THRESHOLD = 0.72
DIRECT_TITLE_MIN_WORDS = 3

QUOTED_TITLE_PATTERN = re.compile(r"«([^»]{8,})»")


@dataclass(frozen=True)
class DocumentResolveCandidate:
    doc_id: str
    doc_name: str
    score: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class DocumentResolveResult:
    found: bool = False
    doc_id: str = ""
    doc_name: str = ""
    status: str = "not_found"
    confidence: float = 0.0
    mention_surface: str = ""
    mention_kind: str = ""
    candidates: tuple[DocumentResolveCandidate, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "status": self.status,
            "confidence": round(self.confidence, 4),
            "mention_surface": self.mention_surface,
            "mention_kind": self.mention_kind,
            "candidates": [item.to_dict() for item in self.candidates],
            "evidence": dict(self.evidence),
            "warnings": list(self.warnings),
        }


TOPIC_ALIAS_SCORE = 0.85


def _knowledge_view(candidate) -> DocumentResolveCandidate:
    return DocumentResolveCandidate(
        doc_id=candidate.doc_id,
        doc_name=candidate.doc_name,
        score=candidate.score,
        reasons=candidate.reasons,
    )


def _catalog_id_for_knowledge_doc(
    knowledge_doc_id: str,
    catalog: DocumentCatalog,
    knowledge_links: dict[str, KnowledgeCatalogLink],
) -> str:
    if catalog.by_doc_id().get(knowledge_doc_id) or catalog.by_id().get(knowledge_doc_id):
        return knowledge_doc_id
    link = knowledge_links.get(knowledge_doc_id)
    if link and link.catalog_id:
        return link.catalog_id
    return knowledge_doc_id


def _merge_label_candidates(
    catalog_candidates: list[DocumentCandidate],
    topic_candidates: list,
    *,
    catalog: DocumentCatalog,
    knowledge_links: dict[str, KnowledgeCatalogLink],
) -> list[DocumentCandidate]:
    merged: dict[str, DocumentCandidate] = {
        candidate.catalog_id: candidate for candidate in catalog_candidates
    }
    for topic in topic_candidates:
        catalog_id = _catalog_id_for_knowledge_doc(topic.doc_id, catalog, knowledge_links)
        entry = catalog.by_doc_id().get(catalog_id) or catalog.by_id().get(catalog_id)
        doc_name = entry.doc_name if entry is not None else topic.doc_name
        candidate = DocumentCandidate(
            catalog_id=catalog_id,
            doc_name=doc_name,
            score=topic.score,
            reasons=topic.reasons,
        )
        current = merged.get(catalog_id)
        if current is None or candidate.score > current.score:
            merged[catalog_id] = candidate
    ranked = sorted(merged.values(), key=lambda item: item.score, reverse=True)
    filtered: list[DocumentCandidate] = []
    for candidate in ranked:
        entry = catalog.by_id().get(candidate.catalog_id) or catalog.by_doc_id().get(candidate.catalog_id)
        if entry is not None and is_junk_catalog_hit(entry, candidate.reasons):
            continue
        if is_generic_tech_reg_doc_name(candidate.doc_name) and all(
            reason.startswith("alias_match:") for reason in candidate.reasons
        ):
            continue
        filtered.append(candidate)
    return filtered


def _resolve_from_order_hint(
    query: str,
    catalog: DocumentCatalog,
) -> DocumentResolveResult | None:
    hit = resolve_by_order_number_hint(query, catalog)
    if hit is None:
        return None
    names, catalog_id, confidence, ambiguous, reasons = hit
    candidates = tuple(
        DocumentResolveCandidate(
            doc_id=catalog_id,
            doc_name=names[0] if names else "",
            score=confidence,
            reasons=tuple(reasons),
        )
        for _ in [0]
    )
    if ambiguous or not catalog_id or not names:
        return DocumentResolveResult(
            found=False,
            status="ambiguous",
            confidence=confidence,
            mention_surface=query.strip(),
            mention_kind="order_number",
            candidates=candidates,
            evidence={"resolver": "order_number", "reasons": list(reasons)},
            warnings=("document_resolve:order_number_ambiguous",),
        )
    return DocumentResolveResult(
        found=True,
        doc_id=catalog_id,
        doc_name=names[0],
        status="exact",
        confidence=confidence,
        mention_surface=query.strip(),
        mention_kind="order_number",
        candidates=candidates,
        evidence={"resolver": "order_number", "reasons": list(reasons)},
    )


def _candidate_view(candidate: DocumentCandidate) -> DocumentResolveCandidate:
    return DocumentResolveCandidate(
        doc_id=candidate.catalog_id,
        doc_name=candidate.doc_name,
        score=candidate.score,
        reasons=candidate.reasons,
    )


def _catalog_entry_for_id(catalog: DocumentCatalog, doc_id: str):
    if not doc_id:
        return None
    return catalog.by_doc_id().get(doc_id) or catalog.by_id().get(doc_id)


def _order_number_conflict(query: str, catalog: DocumentCatalog, doc_id: str) -> bool:
    entry = _catalog_entry_for_id(catalog, doc_id)
    if entry is None:
        return False
    query_numbers = extract_order_numbers(query)
    if not query_numbers:
        return False
    if not entry.order_numbers:
        return False
    return not any(number in entry.order_numbers for number in query_numbers)


def detect_document_mention(
    query: str,
    *,
    enable_pue_aliases: bool = False,
) -> tuple[bool, str, str]:
    text = (query or "").strip()
    if not text:
        return False, "", ""

    if query_has_explicit_doc_reference(text):
        return True, text, "explicit_reference"

    quoted = QUOTED_TITLE_PATTERN.search(text)
    if quoted:
        return True, quoted.group(1).strip(), "title"

    norm = normalize_catalog_text(text)
    for alias_key in active_known_query_aliases(enable_pue_aliases=enable_pue_aliases):
        if re.search(rf"\b{re.escape(alias_key)}\b", norm):
            return True, alias_key, "abbreviation"

    return False, "", ""


def _looks_like_direct_title_query(query: str, candidates: list[DocumentCandidate]) -> bool:
    if not candidates:
        return False
    top = candidates[0]
    if top.score < DIRECT_TITLE_SCORE_THRESHOLD:
        return False
    if "explicit_doc_name" in top.reasons:
        return True
    word_count = len(normalize_catalog_text(query).split())
    return word_count >= DIRECT_TITLE_MIN_WORDS


def _resolve_from_partial_order(
    query: str,
    catalog: DocumentCatalog,
) -> DocumentResolveResult | None:
    partial = resolve_by_partial_order_hint(query, catalog)
    if partial is None:
        return None
    names, catalog_id, confidence, ambiguous, reasons = partial
    candidates = tuple(
        DocumentResolveCandidate(
            doc_id=catalog_id,
            doc_name=names[0] if names else "",
            score=confidence,
            reasons=tuple(reasons),
        )
        for _ in [0]
    )
    if ambiguous or not catalog_id or not names:
        return DocumentResolveResult(
            found=False,
            status="ambiguous",
            confidence=confidence,
            mention_surface=query.strip(),
            mention_kind="order_number",
            candidates=candidates,
            evidence={"resolver": "partial_order", "reasons": list(reasons)},
            warnings=("document_resolve:partial_order_ambiguous",),
        )
    return DocumentResolveResult(
        found=True,
        doc_id=catalog_id,
        doc_name=names[0],
        status="exact",
        confidence=confidence,
        mention_surface=query.strip(),
        mention_kind="order_number",
        candidates=candidates,
        evidence={"resolver": "partial_order", "reasons": list(reasons)},
    )


def _resolve_from_candidates(
    query: str,
    catalog: DocumentCatalog,
    candidates: list[DocumentCandidate],
    *,
    mention_surface: str,
    mention_kind: str,
    min_probable_score: float = PROBABLE_SCORE_THRESHOLD,
) -> DocumentResolveResult:
    views = [_candidate_view(item) for item in candidates]
    if not candidates:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            mention_surface=mention_surface,
            mention_kind=mention_kind,
            warnings=("document_resolve:no_catalog_candidates",),
        )

    top = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    gap = top.score - second_score

    if top.catalog_id == "explicit_unverified":
        return DocumentResolveResult(
            found=False,
            status="not_found",
            confidence=top.score,
            mention_surface=mention_surface or query.strip(),
            mention_kind=mention_kind or "title",
            candidates=tuple(views[:5]),
            evidence={"reasons": list(top.reasons)},
            warnings=("document_resolve:unverified_doc_name",),
        )

    if _order_number_conflict(query, catalog, top.catalog_id):
        return DocumentResolveResult(
            found=False,
            status="ambiguous",
            confidence=top.score,
            mention_surface=mention_surface,
            mention_kind=mention_kind,
            candidates=tuple(views[:5]),
            evidence={"reasons": list(top.reasons), "conflict": "order_number_mismatch"},
            warnings=("document_resolve:order_number_conflict",),
        )

    if len(candidates) > 1 and gap < AMBIGUITY_SCORE_GAP and top.score < EXACT_SCORE_THRESHOLD:
        return DocumentResolveResult(
            found=False,
            status="ambiguous",
            confidence=top.score,
            mention_surface=mention_surface,
            mention_kind=mention_kind,
            candidates=tuple(views[:5]),
            evidence={"top_score": top.score, "gap": round(gap, 4), "reasons": list(top.reasons)},
            warnings=("document_resolve:ambiguous_candidates",),
        )

    if top.score >= EXACT_SCORE_THRESHOLD or "explicit_doc_name" in top.reasons:
        status = "exact"
    elif top.score >= min_probable_score:
        status = "probable"
    else:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            confidence=top.score,
            mention_surface=mention_surface,
            mention_kind=mention_kind,
            candidates=tuple(views[:5]),
            evidence={"top_score": top.score, "reasons": list(top.reasons)},
            warnings=("document_resolve:below_threshold",),
        )

    return DocumentResolveResult(
        found=True,
        doc_id=top.catalog_id,
        doc_name=top.doc_name,
        status=status,
        confidence=top.score,
        mention_surface=mention_surface or query.strip(),
        mention_kind=mention_kind,
        candidates=tuple(views[:5]),
        evidence={"top_score": top.score, "gap": round(gap, 4), "reasons": list(top.reasons)},
    )


def resolve_document(
    query_or_name: str,
    *,
    catalog: DocumentCatalog | None = None,
    project_paths: ProjectPaths | None = None,
    enable_pue_aliases: bool | None = None,
) -> DocumentResolveResult:
    text = (query_or_name or "").strip()
    if not text:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            warnings=("document_resolve:empty_query",),
        )

    catalog = catalog or load_default_document_catalog(project_paths)
    pue_aliases = resolve_enable_pue_aliases(enable_pue_aliases)

    partial_result = _resolve_from_partial_order(text, catalog)
    if partial_result is not None:
        return partial_result

    candidates = find_catalog_candidates(
        text,
        catalog,
        limit=8,
        enable_pue_aliases=pue_aliases,
    )
    has_mention, mention_surface, mention_kind = detect_document_mention(
        text,
        enable_pue_aliases=pue_aliases,
    )
    direct_title = _looks_like_direct_title_query(text, candidates)

    if not has_mention and not direct_title:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            candidates=tuple(_candidate_view(item) for item in candidates[:5]),
            warnings=("document_resolve:no_document_mention",),
        )

    if direct_title and not mention_kind:
        mention_kind = "title"
        mention_surface = text

    return _resolve_from_candidates(
        text,
        catalog,
        candidates,
        mention_surface=mention_surface,
        mention_kind=mention_kind,
    )


def resolve_document_by_name(
    doc_name: str,
    *,
    catalog: DocumentCatalog | None = None,
    project_paths: ProjectPaths | None = None,
) -> DocumentResolveResult:
    text = (doc_name or "").strip()
    if not text:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            warnings=("document_resolve:empty_doc_name",),
        )
    catalog = catalog or load_default_document_catalog(project_paths)
    candidates = find_catalog_candidates(
        text,
        catalog,
        explicit_doc_name=text,
        limit=3,
    )
    return _resolve_from_candidates(
        text,
        catalog,
        candidates,
        mention_surface=text,
        mention_kind="title",
    )


def resolve_document_from_label(
    label: str,
    *,
    catalog: DocumentCatalog | None = None,
    project_paths: ProjectPaths | None = None,
    enable_pue_aliases: bool | None = None,
) -> DocumentResolveResult:
    """Treat input as a document label (short name, alias, order number) without query mention heuristics."""
    text = (label or "").strip()
    if not text:
        return DocumentResolveResult(
            found=False,
            status="not_found",
            warnings=("document_resolve:empty_label",),
        )

    catalog = catalog or load_default_document_catalog(project_paths)
    pue_aliases = resolve_enable_pue_aliases(enable_pue_aliases)
    knowledge = load_document_knowledge()
    knowledge_links = load_knowledge_catalog_mapping()

    partial_result = _resolve_from_partial_order(text, catalog)
    if partial_result is not None and partial_result.found:
        return partial_result

    order_result = _resolve_from_order_hint(text, catalog)
    if order_result is not None and order_result.found:
        return order_result

    abbr_candidates = find_abbreviation_document_candidates(
        text,
        knowledge,
        enable_pue_aliases=pue_aliases,
    )
    if abbr_candidates:
        abbr_merged = _merge_label_candidates(
            [],
            abbr_candidates,
            catalog=catalog,
            knowledge_links=knowledge_links,
        )
        abbr_result = _resolve_from_candidates(
            text,
            catalog,
            abbr_merged,
            mention_surface=text,
            mention_kind="abbreviation",
            min_probable_score=LABEL_PROBABLE_SCORE_THRESHOLD,
        )
        if abbr_result.found:
            return abbr_result

    by_name = resolve_document_by_name(text, catalog=catalog, project_paths=project_paths)
    if by_name.found:
        return by_name

    catalog_candidates = find_catalog_candidates(
        text,
        catalog,
        limit=8,
        enable_pue_aliases=pue_aliases,
    )
    topic_candidates = find_topic_alias_document_candidates(
        text,
        knowledge,
        enable_pue_aliases=pue_aliases,
    )
    candidates = _merge_label_candidates(
        catalog_candidates,
        [*abbr_candidates, *topic_candidates],
        catalog=catalog,
        knowledge_links=knowledge_links,
    )
    if candidates and any(reason.startswith("abbreviation:") for reason in candidates[0].reasons):
        mention_kind = "abbreviation"
    elif topic_candidates and candidates and any(
        reason.startswith("topic_alias:") for reason in candidates[0].reasons
    ):
        mention_kind = "topic_alias"
    else:
        mention_kind = "label"
    return _resolve_from_candidates(
        text,
        catalog,
        candidates,
        mention_surface=text,
        mention_kind=mention_kind,
        min_probable_score=LABEL_PROBABLE_SCORE_THRESHOLD,
    )
