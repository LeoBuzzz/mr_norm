from __future__ import annotations

import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.retrieval.filters import doc_name_variants
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import run_point_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text


ToolRunner = Callable[[ToolRequest, IndexingConfig], ToolResult]
DEFAULT_PIPELINES = ["point", "payload", "vector", "hybrid"]
BASE_PIPELINES = {"point", "payload", "vector"}


def default_tool_runners() -> dict[str, ToolRunner]:
    return {
        "point": run_point_tool,
        "payload": run_payload_tool,
        "vector": run_vector_tool,
    }


def default_batch_tool_runners() -> dict[str, ToolRunner]:
    vector_embedder = None

    def vector_runner(request: ToolRequest, config: IndexingConfig) -> ToolResult:
        nonlocal vector_embedder
        if vector_embedder is None:
            from mr_norm.indexing.qdrant_adapter import SentenceTransformerEmbedder

            vector_embedder = SentenceTransformerEmbedder(config)
        return run_vector_tool(request, config, embedder=vector_embedder)

    return {
        "point": run_point_tool,
        "payload": run_payload_tool,
        "vector": vector_runner,
    }


def parse_pipelines(value: str | list[str] | None) -> list[str]:
    if value is None:
        return list(DEFAULT_PIPELINES)
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = value
    pipelines: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in pipelines:
            pipelines.append(name)
    return pipelines or list(DEFAULT_PIPELINES)


def run_retrieval_compare_batch(
    questions: list[dict[str, Any]],
    config: IndexingConfig | None = None,
    *,
    pipelines: list[str] | str | None = None,
    limit: int = 5,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> dict:
    started_at = time.perf_counter()
    config = config or IndexingConfig.from_env()
    selected_pipelines = parse_pipelines(pipelines)
    runners = tool_runners or default_batch_tool_runners()
    reports = []

    for index, question in enumerate(questions, start=1):
        request = question_to_tool_request(question, default_limit=limit, ordinal=index)
        comparison = run_retrieval_compare(
            request,
            config,
            pipelines=selected_pipelines,
            tool_runners=runners,
        )
        entry = {
            "id": question.get("id") or question.get("name") or f"q{index}",
            "query": request.query,
            "filters": request.filters,
            "expected": question.get("expected") or {},
            "manual_judgement": question.get("manual_judgement") or {"relevant": None, "notes": ""},
            "comparison": comparison,
        }
        entry["eval"] = evaluate_question_comparison(entry)
        reports.append(entry)

    eval_summary = summarize_batch_eval(reports)
    return {
        "schema_version": "mr_retrieval_compare_batch_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection_name": config.collection_name,
        "vector_name": config.vector_name,
        "pipelines": selected_pipelines,
        "questions_total": len(reports),
        "questions": reports,
        "eval_summary": eval_summary,
        "metrics": {
            "elapsed_sec": round(time.perf_counter() - started_at, 6),
            "questions_total": len(reports),
            **eval_summary,
        },
        "warnings": [
            warning
            for report in reports
            for warning in (report.get("comparison", {}).get("warnings") or [])
        ],
    }


def filled_expected_fields(expected: dict[str, Any] | None) -> dict[str, str]:
    selected: dict[str, str] = {}
    for key in ("doc_name", "point_number", "chunk_id"):
        value = str((expected or {}).get(key) or "").strip()
        if value:
            selected[key] = value
    return selected


def expected_doc_name_matches(expected_doc_name: str, actual_doc_name: str) -> bool:
    actual = actual_doc_name.strip()
    if not actual:
        return False
    variants = doc_name_variants(expected_doc_name)
    if isinstance(variants, str):
        return actual.casefold() == variants.casefold()
    return any(actual.casefold() == variant.casefold() for variant in variants)


def item_matches_expected(item: dict[str, Any], expected: dict[str, str]) -> bool:
    if expected.get("chunk_id") and item.get("chunk_id") != expected["chunk_id"]:
        return False
    if expected.get("point_number") and item.get("point_number") != expected["point_number"]:
        return False
    if expected.get("doc_name") and not expected_doc_name_matches(expected["doc_name"], str(item.get("doc_name") or "")):
        return False
    return True


def compute_pipeline_match_metrics(items: list[dict[str, Any]], expected: dict[str, str]) -> dict[str, bool | None]:
    if not expected:
        return {
            "has_expected": False,
            "top1_doc_match": None,
            "top5_doc_match": None,
            "top1_point_match": None,
            "top5_chunk_match": None,
        }
    top_items = items[:5]
    doc_expected = expected.get("doc_name")
    point_expected = expected.get("point_number")
    chunk_expected = expected.get("chunk_id")
    return {
        "has_expected": True,
        "top1_doc_match": (
            bool(top_items)
            and expected_doc_name_matches(doc_expected, str(top_items[0].get("doc_name") or ""))
            if doc_expected
            else None
        ),
        "top5_doc_match": (
            any(expected_doc_name_matches(doc_expected, str(item.get("doc_name") or "")) for item in top_items)
            if doc_expected
            else None
        ),
        "top1_point_match": (
            bool(top_items) and top_items[0].get("point_number") == point_expected if point_expected else None
        ),
        "top5_chunk_match": (
            any(item.get("chunk_id") == chunk_expected for item in top_items) if chunk_expected else None
        ),
    }


def evaluate_question_comparison(question_entry: dict[str, Any]) -> dict[str, Any]:
    expected = filled_expected_fields(question_entry.get("expected"))
    pipelines: dict[str, dict[str, bool | None]] = {}
    for pipeline, result in ((question_entry.get("comparison") or {}).get("results") or {}).items():
        pipelines[pipeline] = compute_pipeline_match_metrics(result.get("items") or [], expected)
    return {"expected_fields": expected, "pipelines": pipelines}


def summarize_batch_eval(questions: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = ("top1_doc_match", "top5_doc_match", "top1_point_match", "top5_chunk_match")
    pipelines: dict[str, dict[str, dict[str, int]]] = {}
    for question in questions:
        for pipeline, metrics in (question.get("eval") or {}).get("pipelines", {}).items():
            pipeline_summary = pipelines.setdefault(pipeline, {name: {"passed": 0, "scored": 0} for name in metric_names})
            for metric_name in metric_names:
                value = metrics.get(metric_name)
                if value is None:
                    continue
                pipeline_summary[metric_name]["scored"] += 1
                if value:
                    pipeline_summary[metric_name]["passed"] += 1
    return {"pipelines": pipelines, "questions_with_expected": sum(1 for question in questions if question.get("eval", {}).get("expected_fields"))}


def question_to_tool_request(question: dict[str, Any], *, default_limit: int, ordinal: int) -> ToolRequest:
    filters = question.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"question {ordinal} filters must be an object")
    raw_limit = question.get("limit", default_limit)
    return ToolRequest(
        query=str(question.get("query") or ""),
        filters=dict(filters),
        limit=int(raw_limit) if raw_limit is not None else default_limit,
        profile=str(question.get("profile") or "fast"),
        trace_id=str(question.get("trace_id") or question.get("id") or question.get("name") or f"q{ordinal}"),
    )


def run_retrieval_compare(
    request: ToolRequest,
    config: IndexingConfig | None = None,
    *,
    pipelines: list[str] | str | None = None,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> dict:
    started_at = time.perf_counter()
    config = config or IndexingConfig.from_env()
    selected_pipelines = parse_pipelines(pipelines)
    runners = tool_runners or default_tool_runners()
    results: dict[str, dict] = {}
    successful_results: dict[str, ToolResult] = {}
    warnings: list[str] = []

    for pipeline in selected_pipelines:
        if pipeline == "hybrid":
            continue
        runner = runners.get(pipeline)
        if runner is None:
            results[pipeline] = error_pipeline_result(pipeline, request, config, f"unknown pipeline: {pipeline}")
            warnings.append(f"unknown pipeline: {pipeline}")
            continue
        try:
            result = runner(request, config)
        except Exception as exc:  # Tool comparison should preserve partial evidence from other tools.
            results[pipeline] = error_pipeline_result(pipeline, request, config, f"{type(exc).__name__}: {exc}")
            warnings.append(f"{pipeline} failed: {type(exc).__name__}: {exc}")
            continue
        successful_results[pipeline] = result
        results[pipeline] = result.to_dict()

    if "hybrid" in selected_pipelines:
        hybrid = build_hybrid_rrf_result(request, config, successful_results)
        results["hybrid"] = hybrid.to_dict()
        successful_results["hybrid"] = hybrid

    return {
        "schema_version": "mr_retrieval_compare_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "request": asdict(request),
        "collection_name": config.collection_name,
        "vector_name": config.vector_name,
        "pipelines": selected_pipelines,
        "results": results,
        "metrics": {
            "elapsed_sec": round(time.perf_counter() - started_at, 6),
            "pipelines_total": len(selected_pipelines),
            "pipelines_succeeded": len(successful_results),
        },
        "warnings": warnings,
    }


def build_hybrid_rrf_result(
    request: ToolRequest,
    config: IndexingConfig,
    results: dict[str, ToolResult],
    *,
    k: int = 60,
) -> ToolResult:
    started_at = time.perf_counter()
    fused_items = reciprocal_rank_fusion(results, limit=request.limit, k=k)
    return ToolResult(
        items=fused_items,
        trace=ToolTrace(
            tool_name="hybrid_rrf",
            trace_id=request.trace_id,
            collection_name=config.collection_name,
            vector_name=config.vector_name,
            query=request.query,
            normalized_filters=dict(request.filters),
            limit=request.limit,
            profile=request.profile,
        ),
        metrics=ToolMetrics(
            elapsed_sec=round(time.perf_counter() - started_at, 6),
            candidates_returned=len(fused_items),
            qdrant_calls=0,
        ),
        warnings=[] if results else ["hybrid requested without successful source pipelines"],
    )


def reciprocal_rank_fusion(results: dict[str, ToolResult], *, limit: int, k: int = 60) -> list[RetrievedItem]:
    by_chunk: dict[str, RetrievedItem] = {}
    scores: dict[str, float] = {}
    source_ranks: dict[str, dict[str, int]] = {}
    for pipeline_name, result in results.items():
        if pipeline_name == "hybrid":
            continue
        for rank, item in enumerate(result.items, start=1):
            if not item.chunk_id:
                continue
            by_chunk.setdefault(item.chunk_id, item)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (k + rank)
            source_ranks.setdefault(item.chunk_id, {})[pipeline_name] = rank
    ordered_chunk_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
    return [
        replace(
            by_chunk[chunk_id],
            score=round(scores[chunk_id], 8),
            source_tool="hybrid_rrf",
            matched={
                **by_chunk[chunk_id].matched,
                "rrf_score": round(scores[chunk_id], 8),
                "source_ranks": source_ranks[chunk_id],
            },
        )
        for chunk_id in ordered_chunk_ids
    ]


def error_pipeline_result(pipeline: str, request: ToolRequest, config: IndexingConfig, message: str) -> dict:
    return ToolResult(
        items=[],
        trace=ToolTrace(
            tool_name=pipeline,
            trace_id=request.trace_id,
            collection_name=config.collection_name,
            vector_name=config.vector_name,
            query=request.query,
            normalized_filters=dict(request.filters),
            limit=request.limit,
            profile=request.profile,
            empty_reason="pipeline_error",
        ),
        metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=0, qdrant_calls=0),
        warnings=[message],
    ).to_dict()


def save_retrieval_compare_report(report: dict, reports_dir: Path) -> dict:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"retrieval_compare_{timestamp}.json"
    markdown_path = reports_dir / f"retrieval_compare_{timestamp}.md"
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, render_retrieval_compare_markdown(report))
    saved = dict(report)
    saved["report_path"] = str(json_path)
    saved["markdown_report_path"] = str(markdown_path)
    return saved


def save_retrieval_compare_batch_report(report: dict, reports_dir: Path) -> dict:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"retrieval_compare_batch_{timestamp}.json"
    markdown_path = reports_dir / f"retrieval_compare_batch_{timestamp}.md"
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, render_retrieval_compare_batch_markdown(report))
    saved = dict(report)
    saved["report_path"] = str(json_path)
    saved["markdown_report_path"] = str(markdown_path)
    return saved


def render_retrieval_compare_markdown(report: dict) -> str:
    lines = [
        "# Retrieval Compare Report",
        "",
        f"- Collection: `{report.get('collection_name', '')}`",
        f"- Query: `{(report.get('request') or {}).get('query', '')}`",
        f"- Pipelines: {', '.join(report.get('pipelines') or [])}",
        "",
        "## Results",
        "",
    ]
    for pipeline, result in (report.get("results") or {}).items():
        items = result.get("items") or []
        lines.append(f"### {pipeline}")
        if not items:
            lines.append("- No results")
            continue
        for index, item in enumerate(items[:5], start=1):
            lines.append(
                f"- {index}. `{item.get('chunk_id', '')}` "
                f"{item.get('doc_name', '')} {item.get('point_number', '')}".rstrip()
            )
    return "\n".join(lines).rstrip() + "\n"


def render_retrieval_compare_batch_markdown(report: dict) -> str:
    lines = [
        "# Retrieval Compare Batch Report",
        "",
        f"- Collection: `{report.get('collection_name', '')}`",
        f"- Pipelines: {', '.join(report.get('pipelines') or [])}",
        f"- Questions: {report.get('questions_total', 0)}",
        f"- Questions with expected: `{(report.get('eval_summary') or {}).get('questions_with_expected', 0)}`",
        "",
    ]
    eval_summary = report.get("eval_summary") or {}
    if eval_summary.get("pipelines"):
        lines.extend(["## Eval Summary", ""])
        for pipeline, metrics in eval_summary["pipelines"].items():
            parts = []
            for metric_name, counts in metrics.items():
                if counts["scored"]:
                    parts.append(f"{metric_name}={counts['passed']}/{counts['scored']}")
            if parts:
                lines.append(f"- {pipeline}: {', '.join(parts)}")
        lines.append("")
    for index, question in enumerate(report.get("questions") or [], start=1):
        lines.extend(
            [
                f"## {index}. {question.get('id', '')}",
                "",
                f"- Query: `{question.get('query', '')}`",
                f"- Filters: `{question.get('filters') or {}}`",
                f"- Expected: `{question.get('expected') or {}}`",
                f"- Manual judgement: `{question.get('manual_judgement') or {}}`",
                "",
                "### Eval",
                "",
            ]
        )
        eval_pipelines = (question.get("eval") or {}).get("pipelines") or {}
        if eval_pipelines:
            for pipeline, metrics in eval_pipelines.items():
                if not metrics.get("has_expected"):
                    lines.append(f"- {pipeline}: no expected fields")
                    continue
                rendered = ", ".join(
                    f"{name}={metrics[name]}"
                    for name in ("top1_doc_match", "top5_doc_match", "top1_point_match", "top5_chunk_match")
                    if metrics.get(name) is not None
                )
                lines.append(f"- {pipeline}: {rendered or 'n/a'}")
        else:
            lines.append("- no eval")
        lines.extend(["", "### Top Results", ""])
        results = ((question.get("comparison") or {}).get("results") or {})
        for pipeline, result in results.items():
            items = result.get("items") or []
            if not items:
                lines.append(f"- {pipeline}: no results")
                continue
            top = items[0]
            lines.append(
                f"- {pipeline}: `{top.get('chunk_id', '')}` "
                f"{top.get('doc_name', '')} {top.get('point_number', '')}".rstrip()
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
