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
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text


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


def chunk_quality_penalties(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    payload = chunk.get("payload") or {}
    text = chunk.get("text") or ""
    penalties: list[dict[str, Any]] = []
    if not payload.get("doc_name"):
        penalties.append({"code": "missing_doc_name", "points": 25, "stage": "metadata_extraction"})
    if not payload.get("heading_path_text"):
        penalties.append({"code": "missing_heading_path_text", "points": 20, "stage": "heading_parsing"})
    if len(text.strip()) < 20:
        penalties.append({"code": "empty_or_tiny_text", "points": 20, "stage": "chunk_splitting"})
    if has_service_markers(text):
        penalties.append({"code": "service_markers_in_text", "points": 15, "stage": "rtf_processing"})
    if looks_truncated(text):
        penalties.append({"code": "looks_truncated", "points": 15, "stage": "chunk_splitting"})
    if not payload.get("point_number") and has_point_structure(chunk):
        penalties.append({"code": "missing_point_number", "points": 10, "stage": "point_parsing"})
    if len(text) > 1800:
        penalties.append({"code": "text_over_target", "points": 10, "stage": "chunk_splitting"})
    missing_keys = sorted(missing_payload_keys(chunk))
    if missing_keys:
        penalties.append(
            {
                "code": "missing_required_payload_keys",
                "points": 20,
                "stage": "payload_compatibility",
                "keys": missing_keys,
            }
        )
    return penalties


def chunk_quality_score(chunk: dict[str, Any]) -> int:
    score = 100
    for penalty in chunk_quality_penalties(chunk):
        score -= int(penalty["points"])
    return max(0, score)


def has_point_structure(chunk: dict[str, Any]) -> bool:
    text = chunk.get("text") or ""
    return bool(re.search(r"^\s*(\{?\d+(?:[\.\-]\d+)*\}?|пункт)", text, re.IGNORECASE))


@dataclass
class ChunkQualityReporter:
    paths: ProjectPaths

    def report(
        self,
        chunks: list[dict[str, Any]] | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if chunks is None:
            chunks = load_chunks(self.paths.chunks_json)
        report = build_quality_report(chunks, run_context=run_context)
        self.paths.ensure_output_dirs()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.paths.reports_dir / f"chunk_quality_{timestamp}.json"
        atomic_write_json(path, report)
        report["report_path"] = str(path)
        markdown_path = self.paths.reports_dir / f"chunk_quality_{timestamp}.md"
        atomic_write_text(markdown_path, render_quality_markdown(report))
        report["markdown_report_path"] = str(markdown_path)
        return report


def build_quality_report(
    chunks: list[dict[str, Any]],
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [chunk.get("payload") or {} for chunk in chunks]
    filenames = [payload.get("filename", "") for payload in payloads]
    docs = sorted(set(name for name in filenames if name))
    lengths = [len(chunk.get("text") or "") for chunk in chunks]
    token_estimates = [int(payload.get("token_estimate") or max(1, len(chunks[i].get("text", "")) // 4)) for i, payload in enumerate(payloads)]
    missing_key_chunks = sum(1 for chunk in chunks if missing_payload_keys(chunk))
    score_rows = [
        {
            "score": chunk_quality_score(chunk),
            "penalties": chunk_quality_penalties(chunk),
            "chunk_id": chunk.get("chunk_id"),
            "filename": (chunk.get("payload") or {}).get("filename"),
            "doc_name": (chunk.get("payload") or {}).get("doc_name"),
            "heading_path_text": (chunk.get("payload") or {}).get("heading_path_text"),
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
    point_ids = [
        payload.get("point_id")
        for payload in payloads
        if payload.get("point_id") and not payload.get("is_split")
    ]
    doc_metadata = build_document_metadata_quality(payloads)
    report = {
        "schema_version": "mr_chunk_quality_v1",
        "run_context": run_context or {},
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
            "duplicate_chunk_ids": duplicates([str(chunk.get("chunk_id") or "") for chunk in chunks]),
        },
        "worst_chunks": score_rows[:20],
    }
    report["blocking_defects"] = build_blocking_defects(report)
    report["passes_quality_gate"] = not report["blocking_defects"]
    return report


def build_run_context(
    *,
    command: str,
    paths: ProjectPaths,
    scope: str,
    input_paths: list[Path] | None = None,
    baseline_path: Path | None = None,
    elapsed_sec: float | None = None,
) -> dict[str, Any]:
    return {
        "command": command,
        "scope": scope,
        "project_root": str(paths.root),
        "input_dir": str(paths.input_dir),
        "chunks_json": str(paths.chunks_json),
        "baseline_chunks_json": str(baseline_path or paths.baseline_chunks_json),
        "input_paths": [str(path) for path in input_paths] if input_paths is not None else None,
        "elapsed_sec": round(elapsed_sec, 3) if elapsed_sec is not None else None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_blocking_defects(report: dict[str, Any]) -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    documents = report["documents"]
    structure = report["structure"]
    chunks = report["chunks"]
    retrieval = report["retrieval_readiness"]
    points = report["points"]
    if documents["documents_total"] == 0:
        defects.append({"code": "no_documents_processed", "stage": "rtf_processing", "count": 1})
    if retrieval["chunks_missing_required_payload_keys"]:
        defects.append(
            {
                "code": "missing_required_payload_keys",
                "stage": "payload_compatibility",
                "count": retrieval["chunks_missing_required_payload_keys"],
            }
        )
    if retrieval["empty_text_chunks"]:
        defects.append({"code": "empty_text_chunks", "stage": "chunk_splitting", "count": retrieval["empty_text_chunks"]})
    if not retrieval["stable_chunk_ids_unique"]:
        defects.append({"code": "duplicate_chunk_ids", "stage": "chunk_splitting", "count": retrieval["duplicate_chunk_ids"]})
    if chunks["chunks_with_service_markers"]:
        defects.append({"code": "service_markers_in_text", "stage": "rtf_processing", "count": chunks["chunks_with_service_markers"]})
    if documents["chunks_without_doc_name"]:
        defects.append({"code": "missing_doc_name", "stage": "metadata_extraction", "count": documents["chunks_without_doc_name"]})
    if structure["chunks_without_heading_path_text"]:
        defects.append(
            {
                "code": "missing_heading_path_text",
                "stage": "heading_parsing",
                "count": structure["chunks_without_heading_path_text"],
            }
        )
    if points["duplicate_point_ids"]:
        defects.append({"code": "duplicate_point_ids", "stage": "point_parsing", "count": points["duplicate_point_ids"]})
    return defects


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
    baseline_scope = baseline_scope_info(new_chunks, baseline_chunks)
    baseline_chunks = filter_baseline_to_new_filenames(new_chunks, baseline_chunks)
    new_report = build_quality_report(new_chunks)
    baseline_report = build_quality_report(normalize_baseline_chunks(baseline_chunks))
    comparisons = {
        "documents_total": compare_number(
            new_report["documents"]["documents_total"],
            baseline_report["documents"]["documents_total"],
            higher_is_better=True,
        ),
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
    blocking_defects = build_comparison_blocking_defects(new_report, comparisons, baseline_scope)
    return {
        "schema_version": "mr_baseline_comparison_v1",
        "baseline_scope": baseline_scope,
        "new": new_report,
        "baseline": baseline_report,
        "comparisons": comparisons,
        "blocking_defects": blocking_defects,
        "passes": not blocking_defects,
    }


def baseline_scope_info(
    new_chunks: list[dict[str, Any]],
    baseline_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    new_filenames = sorted(
        {
            (chunk.get("payload") or {}).get("filename")
            for chunk in new_chunks
            if (chunk.get("payload") or {}).get("filename")
        }
    )
    baseline_filenames = sorted(
        {
            (chunk.get("payload") or {}).get("filename")
            for chunk in baseline_chunks
            if (chunk.get("payload") or {}).get("filename")
        }
    )
    matched_filenames = sorted(set(new_filenames) & set(baseline_filenames))
    extra_new_filenames = sorted(set(new_filenames) - set(baseline_filenames))
    missing_in_new = sorted(set(baseline_filenames) - set(new_filenames))
    return {
        "new_filenames": new_filenames,
        "baseline_filenames_total": len(baseline_filenames),
        "matched_filenames": matched_filenames,
        "extra_new_filenames": extra_new_filenames,
        "missing_in_new": missing_in_new,
        "same_document_scope": bool(new_filenames) and bool(matched_filenames) and not missing_in_new,
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
    return filtered


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


def build_comparison_blocking_defects(
    new_report: dict[str, Any],
    comparisons: dict[str, dict[str, Any]],
    baseline_scope: dict[str, Any],
) -> list[dict[str, Any]]:
    defects = list(new_report.get("blocking_defects") or [])
    for metric, row in comparisons.items():
        if row.get("gate", True) and not row["passes"]:
            defects.append(
                {
                    "code": f"baseline_metric_failed:{metric}",
                    "stage": "baseline_comparison",
                    "new": row["new"],
                    "baseline": row["baseline"],
                }
            )
    if baseline_scope["new_filenames"] and not baseline_scope["matched_filenames"]:
        defects.append(
            {
                "code": "baseline_scope_mismatch",
                "stage": "baseline_comparison",
                "extra_new_filenames": baseline_scope["extra_new_filenames"],
                "missing_in_new": baseline_scope["missing_in_new"],
            }
        )
    elif baseline_scope["missing_in_new"]:
        defects.append(
            {
                "code": "baseline_documents_missing_in_new",
                "stage": "baseline_comparison",
                "missing_in_new": baseline_scope["missing_in_new"],
            }
        )
    return defects


def save_baseline_comparison(paths: ProjectPaths, baseline_path: Path | None = None) -> dict[str, Any]:
    baseline = baseline_path or paths.baseline_chunks_json
    new_chunks = load_chunks(paths.chunks_json)
    if baseline.exists():
        baseline_chunks = load_chunks(baseline)
        result = compare_quality(new_chunks, baseline_chunks)
    else:
        new_report = build_quality_report(new_chunks)
        result = {
            "schema_version": "mr_baseline_comparison_v1",
            "baseline_scope": {
                "new_filenames": sorted(
                    {
                        (chunk.get("payload") or {}).get("filename")
                        for chunk in new_chunks
                        if (chunk.get("payload") or {}).get("filename")
                    }
                ),
                "baseline_filenames_total": 0,
                "matched_filenames": [],
                "extra_new_filenames": [],
                "missing_in_new": [],
                "same_document_scope": False,
            },
            "new": new_report,
            "baseline": {},
            "comparisons": {},
            "blocking_defects": [
                {
                    "code": "baseline_chunks_json_missing",
                    "stage": "baseline_comparison",
                    "path": str(baseline),
                }
            ],
            "passes": False,
        }
    result["run_context"] = build_run_context(
        command="compare-baseline",
        paths=paths,
        scope="existing-output",
        baseline_path=baseline,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = paths.reports_dir / f"baseline_comparison_{timestamp}.json"
    atomic_write_json(report_path, result)
    result["report_path"] = str(report_path)
    markdown_path = paths.reports_dir / f"baseline_comparison_{timestamp}.md"
    atomic_write_text(markdown_path, render_comparison_markdown(result))
    result["markdown_report_path"] = str(markdown_path)
    return result


def render_quality_markdown(report: dict[str, Any]) -> str:
    ctx = report.get("run_context") or {}
    lines = [
        "# Chunk Quality Report",
        "",
        f"- Status: {'PASS' if report.get('passes_quality_gate') else 'FAIL'}",
        f"- Scope: {ctx.get('scope', '')}",
        f"- Command: {ctx.get('command', '')}",
        f"- Chunks: {report['chunks']['chunks_total']}",
        f"- Documents: {report['documents']['documents_total']}",
        f"- Required payload coverage: {report['retrieval_readiness']['required_payload_keys_coverage']:.4f}",
        "",
        "## Blocking Defects",
        "",
    ]
    defects = report.get("blocking_defects") or []
    if defects:
        lines.extend(f"- `{item['code']}` ({item['stage']}): {item.get('count', '')}" for item in defects)
    else:
        lines.append("- None")
    lines.extend(["", "## Worst Chunks", ""])
    for item in report.get("worst_chunks", [])[:20]:
        penalties = ", ".join(p["code"] for p in item.get("penalties", [])) or "none"
        lines.append(
            f"- score={item['score']} chunk_id=`{item.get('chunk_id')}` file=`{item.get('filename')}` "
            f"point=`{item.get('point_number')}` penalties={penalties}"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Baseline Comparison Report",
        "",
        f"- Status: {'PASS' if result.get('passes') else 'FAIL'}",
        f"- Same document scope: {result.get('baseline_scope', {}).get('same_document_scope')}",
        "",
        "## Metrics",
        "",
        "| Metric | New | Baseline | Delta Better | Pass |",
        "|---|---:|---:|---:|---|",
    ]
    for metric, row in result.get("comparisons", {}).items():
        lines.append(
            f"| `{metric}` | {row['new']} | {row['baseline']} | {row['delta_in_better_direction']} | {row['passes']} |"
        )
    lines.extend(["", "## Blocking Defects", ""])
    defects = result.get("blocking_defects") or []
    if defects:
        lines.extend(f"- `{item['code']}` ({item['stage']})" for item in defects)
    else:
        lines.append("- None")
    lines.extend(["", "## Worst New Chunks", ""])
    for item in result.get("new", {}).get("worst_chunks", [])[:20]:
        penalties = ", ".join(p["code"] for p in item.get("penalties", [])) or "none"
        lines.append(f"- score={item['score']} chunk_id=`{item.get('chunk_id')}` penalties={penalties}")
    return "\n".join(lines).rstrip() + "\n"
