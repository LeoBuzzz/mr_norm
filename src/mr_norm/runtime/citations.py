from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import Citation


def _normalize_citation(raw: Mapping[str, Any] | Citation) -> Citation:
    if isinstance(raw, Citation):
        return raw
    chunk_id = str(raw.get("chunk_id") or "").strip()
    return Citation(
        chunk_id=chunk_id,
        doc_name=str(raw.get("doc_name") or "").strip(),
        point_number=str(raw.get("point_number") or "").strip(),
    )


def validate_citations(
    evidence: Sequence[RetrievedItem],
    citations: Sequence[Mapping[str, Any] | Citation],
) -> tuple[list[Citation], list[str]]:
    evidence_by_chunk_id = {item.chunk_id: item for item in evidence if item.chunk_id}
    valid: list[Citation] = []
    warnings: list[str] = []

    for index, raw in enumerate(citations):
        citation = _normalize_citation(raw)
        label = f"citation[{index}]"

        if not citation.chunk_id:
            warnings.append(f"{label}: missing chunk_id")
            continue

        item = evidence_by_chunk_id.get(citation.chunk_id)
        if item is None:
            warnings.append(f"{label}: unknown chunk_id {citation.chunk_id!r}")
            continue

        if citation.doc_name and citation.doc_name != item.doc_name:
            warnings.append(
                f"{label}: doc_name {citation.doc_name!r} does not match evidence {item.doc_name!r}"
            )
            continue

        if citation.point_number and citation.point_number != item.point_number:
            warnings.append(
                f"{label}: point_number {citation.point_number!r} "
                f"does not match evidence {item.point_number!r}"
            )
            continue

        valid.append(
            Citation(
                chunk_id=item.chunk_id,
                doc_name=item.doc_name,
                point_number=item.point_number,
            )
        )

    return valid, warnings
