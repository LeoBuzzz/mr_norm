from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.chunker import (
    duplicate_identity_count,
    load_chunks,
    missing_payload_keys,
    normalize_for_hash,
    stable_id,
)
from mr_norm.tools.rtf_processor import atomic_write_json


def percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return float(ordered[idx])


def has_service_markers(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(re.search(r"(^|\n)\s*//", stripped)) or "***" in stripped or bool(re.search(r"(^|\n)\s*\\\s*$", stripped))


def looks_truncated(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.endswith(":") or stripped.endswith("(") or stripped.endswith(",")


def chunk_quality_score(chunk: dict[str, Any]) -> int:
    payload = chunk.get("payload") or {}
    text = chunk.get("text") or ""
    score = 100
    if not payload.get("doc_name"):
        score -= 25
    if not payload.get("heading_path_text"):
        score -= 20
    if len(text.strip()) < 20:
        score -= 20
    if has_service_markers(text):
        score -= 15
    if looks_truncated(text):
        score -= 15
    if not payload.get("point_number") and has_point_structure(chunk):
        score -= 10
    if len(text) > 1800:
        score -= 10
    if missing_payload_keys(chunk):
        score -= 20
    return max(0, score)


def has_point_structure(chunk: dict[str, Any]) -> bool:
    text = chunk.get("text") or ""
    return bool(re.search(r"^\s*(\{?\d+(?:[\.\-]\d+)*\}?|пункт)", text, re.IGNORECASE))


@dataclass
class ChunkQualityReporter:
    paths: ProjectPaths

    def report(self, chunks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if chunks is None:
            chunks = load_chunks(self.paths.chunks_json)
        report = build_quality_report(chunks)
        self.paths.ensure_output_dirs()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.paths.reports_dir / f"chunk_quality_{timestamp}.json"
        atomic_write_json(path, report)
        report["report_path"] = str(path)
        return report


def build_quality_report(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [chunk.get("payload") or {} for chunk in chunks]
    filenames = [payload.get("filename", "") for payload in payloads]
    docs = sorted(set(name for name in filenames if name))
    lengths = [len(chunk.get("text") or "") for chunk in chunks]
    token_estimates = [int(payload.get("token_estimate") or max(1, len(chunks[i].get("text", "")) // 4)) for i, payload in enumerate(payloads)]
    missing_key_chunks = sum(1 for chunk in chunks if missing_payload_keys(chunk))
    score_rows = [
        {
            "score": chunk_quality_score(chunk),
            "chunk_id": chunk.get("chunk_id"),
            "filename": (chunk.get("payload") or {}).get("filename"),
            "point_number": (chunk.get("payload") or {}).get("point_number"),
            "text_preview": (chunk.get("text") or "")[:240],
        }
        for chunk in chunks
    ]
    score_rows.sort(key=lambda row: row["score"])
    headings_by_doc: dict[str, set[str]] = {}
    for payload in payloads:
        filename = payload.get("filename") or ""
        headings_by_doc.setdefault(filename, set()).update(payload.get("headings") or [])
    doc_ids_by_filename = {
        payload.get("filename"): payload.get("doc_id")
        for payload in payloads
        if payload.get("filename") and payload.get("doc_id")
    }
    point_ids = [payload.get("point_id") for payload in payloads if payload.get("point_id")]
    doc_metadata = build_document_metadata_quality(payloads)
    return {
        "schema_version": "mr_chunk_quality_v1",
        "documents": {
            "documents_total": len(docs),
            "documents_processed_ok": len(docs),
            "documents_failed": 0,
            "metadata_confidence_distribution": dict(Counter(payload.get("metadata_confidence") or "(empty)" for payload in payloads)),
            "chunks_without_doc_name": sum(1 for payload in payloads if not payload.get("doc_name")),
            "duplicate_doc_ids": duplicates(list(doc_ids_by_filename.values())),
            "metadata_quality": doc_metadata,
        },
        "structure": {
            "headings_per_document": {filename: len(values) for filename, values in sorted(headings_by_doc.items())},
            "documents_without_headings": sum(1 for values in headings_by_doc.values() if not values),
            "chunks_without_heading_path_text": sum(1 for payload in payloads if not payload.get("heading_path_text")),
            "suspicious_long_headings": sum(
                1 for payload in payloads for heading in payload.get("headings", []) if len(heading) > 220
            ),
        },
        "points": {
            "points_detected": len(set(payload.get("point_id") for payload in payloads if payload.get("point_id"))),
            "chunks_with_point_number": sum(1 for payload in payloads if payload.get("point_number")),
            "chunks_without_point_number": sum(1 for payload in payloads if not payload.get("point_number")),
            "duplicate_point_ids": duplicates(point_ids),
            "split_chunks": sum(1 for payload in payloads if payload.get("is_split")),
            "incomplete_point_chunks": sum(1 for payload in payloads if payload.get("is_complete_point") is False),
        },
        "chunks": {
            "chunks_total": len(chunks),
            "avg_chars": mean(lengths) if lengths else 0,
            "p50_chars": median(lengths) if lengths else 0,
            "p95_chars": percentile(lengths, 0.95),
            "avg_token_estimate": mean(token_estimates) if token_estimates else 0,
            "p50_token_estimate": median(token_estimates) if token_estimates else 0,
            "p95_token_estimate": percentile(token_estimates, 0.95),
            "chunks_under_80_chars": sum(1 for length in lengths if length < 80),
            "chunks_over_target": sum(1 for length in lengths if length > 1600),
            "chunks_with_service_markers": sum(1 for chunk in chunks if has_service_markers(chunk.get("text") or "")),
            "chunks_look_truncated": sum(1 for chunk in chunks if looks_truncated(chunk.get("text") or "")),
            "duplicate_doc_point_text": duplicate_identity_count(chunks),
        },
        "retrieval_readiness": {
            "chunks_missing_required_payload_keys": missing_key_chunks,
            "required_payload_keys_coverage": 1.0 if not chunks else (len(chunks) - missing_key_chunks) / len(chunks),
            "empty_text_chunks": sum(1 for chunk in chunks if not (chunk.get("text") or "").strip()),
            "stable_chunk_ids_unique": len(set(chunk.get("chunk_id") for chunk in chunks)) == len(chunks),
        },
        "worst_chunks": score_rows[:20],
    }


def build_document_metadata_quality(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    by_doc: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        filename = payload.get("filename") or payload.get("source_file") or ""
        if filename and filename not in by_doc:
            by_doc[filename] = payload
    rows = [document_metadata_row(filename, payload) for filename, payload in sorted(by_doc.items())]
    fields = ["doc_name", "doc_title_full", "doc_kind", "doc_number", "doc_date", "authority", "approving_act"]
    missing_counts = {
        field: sum(1 for row in rows if not row["fields"].get(field, {}).get("ok"))
        for field in fields
    }
    scores = [row["score"] for row in rows]
    return {
        "documents_evaluated": len(rows),
        "avg_score": mean(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "missing_counts": missing_counts,
        "documents": rows,
    }


def document_metadata_row(filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    doc_name = str(payload.get("doc_name") or "")
    doc_title_full = str(payload.get("doc_title_full") or "")
    doc_reg = str(payload.get("doc_reg") or "")
    approving_act = str(payload.get("approving_act") or "")
    doc_kind = str(payload.get("doc_kind") or "")
    doc_number = str(payload.get("doc_number") or "")
    doc_date = str(payload.get("doc_date") or "")
    authority = str(payload.get("authority") or "")

    fields = {
        "doc_name": {"value": doc_name, "ok": bool(doc_name.strip())},
        "doc_title_full": {
            "value": doc_title_full,
            "ok": bool(doc_title_full.strip()) and len(doc_title_full.strip()) >= 12,
        },
        "doc_kind": {
            "value": doc_kind,
            "ok": bool(doc_kind.strip()) and doc_kind not in {"unknown", "other"},
        },
        "doc_number": {"value": doc_number, "ok": bool(doc_number.strip())},
        "doc_date": {"value": doc_date, "ok": bool(doc_date.strip())},
        "authority": {"value": authority, "ok": bool(authority.strip())},
        "approving_act": {
            "value": approving_act,
            "ok": bool(approving_act.strip()) and approving_act.strip().lower() != "не указано",
        },
    }
    score = round(100 * sum(1 for item in fields.values() if item["ok"]) / len(fields), 2)
    return {
        "filename": filename,
        "score": score,
        "fields": fields,
    }


def duplicates(values: list[str]) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def compare_quality(new_chunks: list[dict[str, Any]], baseline_chunks: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_chunks = filter_baseline_to_new_filenames(new_chunks, baseline_chunks)
    new_report = build_quality_report(new_chunks)
    baseline_report = build_quality_report(normalize_baseline_chunks(baseline_chunks))
    comparisons = {
        "chunks_total": compare_number(
            new_report["chunks"]["chunks_total"],
            baseline_report["chunks"]["chunks_total"],
            higher_is_better=True,
            gate=False,
        ),
        "chunks_without_doc_name": compare_number(
            new_report["documents"]["chunks_without_doc_name"],
            baseline_report["documents"]["chunks_without_doc_name"],
            higher_is_better=False,
        ),
        "chunks_without_heading_path_text": compare_number(
            new_report["structure"]["chunks_without_heading_path_text"],
            baseline_report["structure"]["chunks_without_heading_path_text"],
            higher_is_better=False,
        ),
        "chunks_without_point_number": compare_number(
            new_report["points"]["chunks_without_point_number"],
            baseline_report["points"]["chunks_without_point_number"],
            higher_is_better=False,
        ),
        "duplicate_doc_point_text": compare_number(
            new_report["chunks"]["duplicate_doc_point_text"],
            baseline_report["chunks"]["duplicate_doc_point_text"],
            higher_is_better=False,
        ),
        "service_markers": compare_number(
            new_report["chunks"]["chunks_with_service_markers"],
            baseline_report["chunks"]["chunks_with_service_markers"],
            higher_is_better=False,
        ),
        "document_metadata_avg_score": compare_number(
            new_report["documents"]["metadata_quality"]["avg_score"],
            baseline_report["documents"]["metadata_quality"]["avg_score"],
            higher_is_better=True,
        ),
        "documents_missing_doc_kind": compare_number(
            new_report["documents"]["metadata_quality"]["missing_counts"]["doc_kind"],
            baseline_report["documents"]["metadata_quality"]["missing_counts"]["doc_kind"],
            higher_is_better=False,
        ),
        "documents_missing_doc_date": compare_number(
            new_report["documents"]["metadata_quality"]["missing_counts"]["doc_date"],
            baseline_report["documents"]["metadata_quality"]["missing_counts"]["doc_date"],
            higher_is_better=False,
        ),
        "documents_missing_authority": compare_number(
            new_report["documents"]["metadata_quality"]["missing_counts"]["authority"],
            baseline_report["documents"]["metadata_quality"]["missing_counts"]["authority"],
            higher_is_better=False,
        ),
        "documents_missing_approving_act": compare_number(
            new_report["documents"]["metadata_quality"]["missing_counts"]["approving_act"],
            baseline_report["documents"]["metadata_quality"]["missing_counts"]["approving_act"],
            higher_is_better=False,
        ),
    }
    return {
        "schema_version": "mr_baseline_comparison_v1",
        "new": new_report,
        "baseline": baseline_report,
        "comparisons": comparisons,
        "passes": all(row["passes"] for row in comparisons.values() if row.get("gate", True))
        and new_report["retrieval_readiness"]["chunks_missing_required_payload_keys"] == 0,
    }


def filter_baseline_to_new_filenames(
    new_chunks: list[dict[str, Any]],
    baseline_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    new_filenames = {
        (chunk.get("payload") or {}).get("filename")
        for chunk in new_chunks
        if (chunk.get("payload") or {}).get("filename")
    }
    if not new_filenames:
        return baseline_chunks
    filtered = [
        chunk for chunk in baseline_chunks if (chunk.get("payload") or {}).get("filename") in new_filenames
    ]
    return filtered or baseline_chunks


def normalize_baseline_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        payload = dict(chunk.get("payload") or {})
        text = chunk.get("text") or ""
        payload.setdefault("doc_id", stable_id("doc", payload.get("doc_name", ""), payload.get("doc_reg", "")))
        payload.setdefault("point_id", stable_id("point", payload["doc_id"], payload.get("point_identity_key", ""), i))
        payload.setdefault("token_estimate", max(1, len(text) // 4))
        chunk_id = chunk.get("chunk_id") or stable_id("chunk", payload["doc_id"], payload["point_id"], normalize_for_hash(text))
        normalized.append({"chunk_id": chunk_id, "text": text, "payload": payload})
    return normalized


def compare_number(
    new_value: int | float,
    baseline_value: int | float,
    higher_is_better: bool,
    gate: bool = True,
) -> dict[str, Any]:
    if higher_is_better:
        passes = new_value >= baseline_value
        delta = new_value - baseline_value
    else:
        passes = new_value <= baseline_value
        delta = baseline_value - new_value
    return {
        "new": new_value,
        "baseline": baseline_value,
        "delta_in_better_direction": delta,
        "passes": passes,
        "gate": gate,
    }


def save_baseline_comparison(paths: ProjectPaths, baseline_path: Path | None = None) -> dict[str, Any]:
    baseline = baseline_path or paths.baseline_chunks_json
    new_chunks = load_chunks(paths.chunks_json)
    baseline_chunks = load_chunks(baseline)
    result = compare_quality(new_chunks, baseline_chunks)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = paths.reports_dir / f"baseline_comparison_{timestamp}.json"
    atomic_write_json(report_path, result)
    result["report_path"] = str(report_path)
    return result
