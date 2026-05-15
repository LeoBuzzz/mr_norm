from __future__ import annotations

import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
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
