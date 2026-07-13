from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest
from mr_norm.retrieval.document_catalog import extract_document_label_hint, extract_point_number_hints
from mr_norm.retrieval.point_assemble import (
    AssembledPointPart,
    assemble_point_parts,
    filter_items_by_exact_point,
    point_number_filter_variants,
    select_point_identity_group,
)
from mr_norm.retrieval.tools.point import run_point_tool
from mr_norm.runtime.contracts import Citation
from mr_norm.skills.document_resolve import (
    DocumentResolveResult,
    resolve_document,
    resolve_document_from_label,
)

POINT_FETCH_LIMIT = 20


@dataclass(frozen=True)
class PointLookupRequest:
    query: str = ""
    doc_id: str = ""
    doc_name: str = ""
    point_number: str = ""
    point_numbers: tuple[str, ...] = ()
    limit: int = POINT_FETCH_LIMIT
    trace_id: str = "point_lookup"


@dataclass(frozen=True)
class PointLookupPart:
    chunk_id: str
    part_index: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PointLookupResult:
    found: bool = False
    doc_id: str = ""
    doc_name: str = ""
    point_number: str = ""
    text: str = ""
    answer: str = ""
    status: str = "not_found"
    parts: tuple[PointLookupPart, ...] = ()
    citations: tuple[Citation, ...] = ()
    evidence: tuple[RetrievedItem, ...] = ()
    warnings: tuple[str, ...] = ()
    document_resolve: DocumentResolveResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "point_number": self.point_number,
            "text": self.text,
            "answer": self.answer,
            "status": self.status,
            "parts": [part.to_dict() for part in self.parts],
            "citations": [citation.to_dict() for citation in self.citations],
            "evidence": [asdict(item) for item in self.evidence],
            "warnings": list(self.warnings),
            "document_resolve": self.document_resolve.to_dict() if self.document_resolve else None,
        }


def _resolve_point_numbers(
    *,
    query: str,
    explicit_point: str,
    explicit_points: tuple[str, ...],
) -> tuple[str, ...]:
    numbers = tuple(part.strip() for part in explicit_points if part.strip())
    if numbers:
        return numbers
    if explicit_point.strip():
        return (explicit_point.strip(),)
    return tuple(extract_point_number_hints(query))


def _resolve_document_for_point_lookup(
    *,
    query: str,
    doc_id: str,
    doc_name: str,
    project_paths: ProjectPaths | None,
) -> tuple[str, str, DocumentResolveResult | None, list[str]]:
    if doc_id or doc_name:
        return doc_id, doc_name, None, []

    warnings: list[str] = []
    document_resolve = resolve_document(
        query,
        project_paths=project_paths,
    )
    if not document_resolve.found:
        label = extract_document_label_hint(query)
        if label:
            document_resolve = resolve_document_from_label(
                label,
                project_paths=project_paths,
            )
            if document_resolve.found:
                warnings.append("point_lookup:resolved_document_from_label")

    if document_resolve.found and document_resolve.doc_id:
        return document_resolve.doc_id, document_resolve.doc_name, document_resolve, warnings
    if document_resolve.found:
        return doc_id, document_resolve.doc_name, document_resolve, warnings
    return doc_id, doc_name, document_resolve, warnings


def _fetch_point_items(
    *,
    doc_id: str,
    doc_name: str,
    point_number: str,
    config: IndexingConfig,
    limit: int,
    trace_id: str,
) -> tuple[list[RetrievedItem], list[str]]:
    warnings: list[str] = []
    collected: dict[str, RetrievedItem] = {}
    for variant in point_number_filter_variants(point_number):
        filters: dict[str, Any] = {"point_number": variant}
        if doc_id:
            filters["doc_id"] = doc_id
        elif doc_name:
            filters["doc_name"] = doc_name
        result = run_point_tool(
            ToolRequest(
                query=variant,
                filters=filters,
                limit=limit,
                profile="fast",
                trace_id=trace_id,
            ),
            config,
        )
        warnings.extend(result.warnings)
        exact = filter_items_by_exact_point(list(result.items), point_number)
        for item in exact:
            if item.chunk_id and item.chunk_id not in collected:
                collected[item.chunk_id] = item
        if exact:
            break
    return list(collected.values()), warnings


def _format_verbatim_answer(doc_name: str, point_number: str, text: str) -> str:
    header_doc = doc_name.strip() or "—"
    header_point = point_number.strip() or "—"
    body = text.strip()
    if not body:
        return ""
    return f"п. {header_point} — {header_doc}\n\n{body}"


def _parts_view(parts: tuple[AssembledPointPart, ...]) -> tuple[PointLookupPart, ...]:
    return tuple(
        PointLookupPart(
            chunk_id=part.chunk_id,
            part_index=part.part_index,
            text=part.text,
        )
        for part in parts
    )


def _citations_from_items(items: tuple[RetrievedItem, ...]) -> tuple[Citation, ...]:
    citations: list[Citation] = []
    seen: set[str] = set()
    for item in items:
        if not item.chunk_id or item.chunk_id in seen:
            continue
        seen.add(item.chunk_id)
        citations.append(
            Citation(
                chunk_id=item.chunk_id,
                doc_name=item.doc_name,
                point_number=item.point_number,
            )
        )
    return tuple(citations)


def _lookup_single_point(
    *,
    doc_id: str,
    doc_name: str,
    point_number: str,
    config: IndexingConfig,
    limit: int,
    trace_id: str,
    document_resolve: DocumentResolveResult | None,
    prior_warnings: tuple[str, ...] = (),
) -> PointLookupResult:
    warnings = list(prior_warnings)
    items, fetch_warnings = _fetch_point_items(
        doc_id=doc_id,
        doc_name=doc_name,
        point_number=point_number,
        config=config,
        limit=limit,
        trace_id=trace_id,
    )
    warnings.extend(fetch_warnings)

    if not items:
        return PointLookupResult(
            status="not_found",
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_number,
            warnings=tuple(dict.fromkeys([*warnings, "point_lookup:no_point_chunks"])),
            document_resolve=document_resolve,
        )

    distinct_points = {
        item.point_number
        for item in items
        if item.point_number
    }
    if len(distinct_points) > 1:
        return PointLookupResult(
            status="ambiguous",
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_number,
            evidence=tuple(items[:3]),
            warnings=tuple(dict.fromkeys([*warnings, "point_lookup:multiple_point_numbers"])),
            document_resolve=document_resolve,
        )

    _, collapsed = select_point_identity_group(filter_items_by_exact_point(items, point_number))
    if collapsed:
        warnings.append("point_lookup:collapsed_point_identity_groups")

    assembled = assemble_point_parts(items, point_number=point_number)
    if assembled is None or not assembled.text.strip():
        return PointLookupResult(
            status="not_found",
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_number,
            warnings=tuple(dict.fromkeys([*warnings, "point_lookup:empty_assembled_text"])),
            document_resolve=document_resolve,
        )

    status = "exact" if assembled.is_complete else "partial"
    answer = _format_verbatim_answer(assembled.doc_name, assembled.point_number, assembled.text)
    if status == "partial":
        warnings.append("point_lookup:partial_point_text")

    return PointLookupResult(
        found=True,
        doc_id=assembled.doc_id or doc_id,
        doc_name=assembled.doc_name or doc_name,
        point_number=assembled.point_number,
        text=assembled.text,
        answer=answer,
        status=status,
        parts=_parts_view(assembled.parts),
        citations=_citations_from_items(assembled.items),
        evidence=assembled.items,
        warnings=tuple(dict.fromkeys(warnings)),
        document_resolve=document_resolve,
    )


def lookup_point(
    request: PointLookupRequest,
    *,
    config: IndexingConfig | None = None,
    project_paths: ProjectPaths | None = None,
) -> PointLookupResult:
    query = (request.query or "").strip()
    doc_id = (request.doc_id or "").strip()
    doc_name = (request.doc_name or "").strip()
    point_numbers = _resolve_point_numbers(
        query=query,
        explicit_point=request.point_number,
        explicit_points=request.point_numbers,
    )
    warnings: list[str] = []

    if not point_numbers:
        return PointLookupResult(
            status="not_found",
            warnings=("point_lookup:missing_point_number",),
        )

    document_resolve: DocumentResolveResult | None = None
    if not doc_id and not doc_name:
        doc_id, doc_name, document_resolve, resolve_warnings = _resolve_document_for_point_lookup(
            query=query,
            doc_id=doc_id,
            doc_name=doc_name,
            project_paths=project_paths,
        )
        warnings.extend(resolve_warnings)
        if not doc_id and not doc_name:
            return PointLookupResult(
                status="not_found",
                point_number=", ".join(point_numbers),
                warnings=tuple(
                    dict.fromkeys(
                        [
                            *warnings,
                            "point_lookup:document_not_found",
                            *(document_resolve.warnings if document_resolve else ()),
                        ]
                    )
                ),
                document_resolve=document_resolve,
            )

    indexing_config = config or IndexingConfig.from_env()
    lookup_limit = max(1, min(request.limit, POINT_FETCH_LIMIT))
    trace_id = request.trace_id or "point_lookup"

    if len(point_numbers) == 1:
        return _lookup_single_point(
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_numbers[0],
            config=indexing_config,
            limit=lookup_limit,
            trace_id=trace_id,
            document_resolve=document_resolve,
            prior_warnings=tuple(warnings),
        )

    answers: list[str] = []
    texts: list[str] = []
    parts: list[PointLookupPart] = []
    citations: list[Citation] = []
    evidence: list[RetrievedItem] = []
    statuses: list[str] = []
    resolved_doc_id = doc_id
    resolved_doc_name = doc_name

    for point_number in point_numbers:
        single = _lookup_single_point(
            doc_id=doc_id,
            doc_name=doc_name,
            point_number=point_number,
            config=indexing_config,
            limit=lookup_limit,
            trace_id=trace_id,
            document_resolve=document_resolve,
            prior_warnings=tuple(warnings),
        )
        warnings.extend(single.warnings)
        if not single.found or not single.text.strip():
            return PointLookupResult(
                status="not_found",
                doc_id=resolved_doc_id,
                doc_name=resolved_doc_name,
                point_number=", ".join(point_numbers),
                warnings=tuple(
                    dict.fromkeys(
                        [
                            *warnings,
                            f"point_lookup:missing_point_{point_number}",
                        ]
                    )
                ),
                document_resolve=document_resolve,
            )
        answers.append(single.answer)
        texts.append(single.text)
        parts.extend(single.parts)
        citations.extend(single.citations)
        evidence.extend(single.evidence)
        statuses.append(single.status)
        resolved_doc_id = single.doc_id or resolved_doc_id
        resolved_doc_name = single.doc_name or resolved_doc_name

    combined_status = "partial" if "partial" in statuses else "exact"
    return PointLookupResult(
        found=True,
        doc_id=resolved_doc_id,
        doc_name=resolved_doc_name,
        point_number=", ".join(point_numbers),
        text="\n\n".join(texts),
        answer="\n\n".join(answers),
        status=combined_status,
        parts=tuple(parts),
        citations=tuple(citations),
        evidence=tuple(evidence),
        warnings=tuple(dict.fromkeys(warnings)),
        document_resolve=document_resolve,
    )
