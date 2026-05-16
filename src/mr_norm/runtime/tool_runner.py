from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.compare import reciprocal_rank_fusion
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import is_point_lookup_filters, run_point_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace, ToolCallPlan
from mr_norm.runtime.profiles import get_profile_config
from mr_norm.runtime.router import route_runtime
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text

ToolRunner = Callable[[ToolRequest, IndexingConfig], ToolResult]


def _merge_tool_results(results: list[ToolResult], *, tool_name: str, trace_id: str) -> ToolResult:
    if not results:
        return ToolResult(
            items=[],
            trace=ToolTrace(tool_name=tool_name, trace_id=trace_id),
            metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=0, qdrant_calls=0),
            warnings=[],
        )
    if len(results) == 1:
        return results[0]

    seen: set[str] = set()
    merged_items: list[RetrievedItem] = []
    warnings: list[str] = []
    qdrant_calls = 0
    elapsed = 0.0
    for result in results:
        qdrant_calls += result.metrics.qdrant_calls
        elapsed += result.metrics.elapsed_sec
        warnings.extend(result.warnings)
        for item in result.items:
            key = item.chunk_id or f"{item.doc_name}:{item.point_number}:{item.text[:80]}"
            if key in seen:
                continue
            seen.add(key)
            merged_items.append(item)

    base = results[0]
    return ToolResult(
        items=merged_items,
        trace=base.trace,
        metrics=ToolMetrics(
            elapsed_sec=round(elapsed, 6),
            candidates_returned=len(merged_items),
            qdrant_calls=qdrant_calls,
        ),
        warnings=list(dict.fromkeys(warnings)),
    )


def default_runtime_tool_runners() -> dict[str, ToolRunner]:
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


def run_runtime(
    request: RuntimeRequest,
    config: IndexingConfig | None = None,
    *,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> RuntimeResult:
    started_at = time.perf_counter()
    config = config or IndexingConfig.from_env()
    profile = get_profile_config(request.profile)
    runners = tool_runners or default_runtime_tool_runners()
    plan, warnings = route_runtime(request)
    tool_results: dict[str, ToolResult] = {}
    routing_reasons = [f"{step.tool_name}: {step.reason}" for step in plan]
    qdrant_calls = 0
    tools_succeeded = 0

    for step in sorted(plan, key=lambda item: item.priority):
        runner = runners.get(step.tool_name)
        if runner is None:
            warnings.append(f"unknown runtime tool: {step.tool_name}")
            continue
        queries = tuple(query.strip() for query in step.queries if query.strip())
        if not queries:
            queries = (step.request.query.strip(),) if step.request.query.strip() else ()
        if not queries and step.tool_name == "point" and is_point_lookup_filters(dict(step.request.filters)):
            queries = ("",)
        if not queries and step.tool_name != "point":
            warnings.append(f"{step.tool_name} skipped: empty query list")
            continue

        try:
            per_query_results: list[ToolResult] = []
            for query_text in queries:
                tool_request = ToolRequest(
                    query=query_text,
                    filters=dict(step.request.filters),
                    limit=step.request.limit,
                    profile=step.request.profile,
                    trace_id=step.request.trace_id,
                    required_tokens=step.request.required_tokens,
                )
                per_query_results.append(runner(tool_request, config))
            result = _merge_tool_results(
                per_query_results,
                tool_name=step.tool_name,
                trace_id=step.request.trace_id,
            )
        except Exception as exc:
            warnings.append(f"{step.tool_name} failed: {type(exc).__name__}: {exc}")
            continue
        tool_results[step.tool_name] = result
        qdrant_calls += result.metrics.qdrant_calls
        if result.items:
            tools_succeeded += 1
        warnings.extend(result.warnings)

    items = []
    fusion = ""
    if profile.use_hybrid and len(tool_results) > 1:
        fused = reciprocal_rank_fusion(tool_results, limit=request.limit)
        items = fused
        fusion = "hybrid_rrf"
        tool_results["hybrid"] = ToolResult(
            items=fused,
            trace=ToolTrace(
                tool_name="hybrid_rrf",
                trace_id=request.trace_id,
                collection_name=config.collection_name,
                vector_name=config.vector_name,
                query=request.query,
                normalized_filters=dict(request.filters),
                limit=request.limit,
                profile=profile.name,
            ),
            metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=len(fused), qdrant_calls=0),
            warnings=[],
        )
    elif tool_results:
        for tool_name in ("point", "payload", "vector"):
            result = tool_results.get(tool_name)
            if result and result.items:
                items = result.items[: request.limit]
                break

    empty_reason = ""
    if not items:
        empty_reason = "no_runtime_matches"
        if not plan:
            empty_reason = "empty_tool_plan"

    elapsed = round(time.perf_counter() - started_at, 6)
    return RuntimeResult(
        items=items,
        tool_results=tool_results,
        plan=plan,
        trace=RuntimeTrace(
            trace_id=request.trace_id,
            profile=profile.name,
            mode=request.mode,
            selected_tools=[step.tool_name for step in plan],
            routing_reasons=routing_reasons,
            fusion=fusion,
            empty_reason=empty_reason,
        ),
        metrics=RuntimeMetrics(
            elapsed_sec=elapsed,
            tools_planned=len(plan),
            tools_succeeded=tools_succeeded,
            items_returned=len(items),
            qdrant_calls=qdrant_calls,
        ),
        warnings=warnings,
    )


def runtime_request_from_question(question: dict[str, Any], *, default_limit: int, ordinal: int) -> RuntimeRequest:
    filters = question.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"question {ordinal} filters must be an object")
    raw_limit = question.get("limit", default_limit)
    return RuntimeRequest(
        query=str(question.get("query") or ""),
        filters=dict(filters),
        limit=int(raw_limit) if raw_limit is not None else default_limit,
        profile=str(question.get("profile") or "balanced"),
        trace_id=str(question.get("trace_id") or question.get("id") or question.get("name") or f"q{ordinal}"),
        mode=str(question.get("mode") or "evidence"),
    )


def run_runtime_batch(
    questions: list[dict[str, Any]],
    config: IndexingConfig | None = None,
    *,
    profile: str = "balanced",
    limit: int = 10,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    config = config or IndexingConfig.from_env()
    runners = tool_runners or default_runtime_tool_runners()
    entries: list[dict[str, Any]] = []

    for index, question in enumerate(questions, start=1):
        request = runtime_request_from_question(question, default_limit=limit, ordinal=index)
        if question.get("profile") is None:
            request = RuntimeRequest(
                query=request.query,
                filters=request.filters,
                limit=request.limit,
                profile=profile,
                trace_id=request.trace_id,
                mode=request.mode,
            )
        result = run_runtime(request, config, tool_runners=runners)
        entries.append(
            {
                "id": question.get("id") or question.get("name") or f"q{index}",
                "request": asdict(request),
                "result": result.to_dict(),
            }
        )

    return {
        "schema_version": "mr_runtime_batch_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection_name": config.collection_name,
        "vector_name": config.vector_name,
        "profile": profile,
        "questions_total": len(entries),
        "questions": entries,
        "metrics": {
            "elapsed_sec": round(time.perf_counter() - started_at, 6),
            "questions_total": len(entries),
        },
        "warnings": [warning for entry in entries for warning in entry["result"].get("warnings") or []],
    }


def save_runtime_report(report: dict[str, Any], reports_dir: Path, *, prefix: str = "rag_runtime") -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"{prefix}_{timestamp}.json"
    markdown_path = reports_dir / f"{prefix}_{timestamp}.md"
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, render_runtime_markdown(report))
    saved = dict(report)
    saved["report_path"] = str(json_path)
    saved["markdown_report_path"] = str(markdown_path)
    return saved


def render_runtime_markdown(report: dict[str, Any]) -> str:
    if report.get("schema_version") == "mr_runtime_batch_v1":
        return _render_runtime_batch_markdown(report)
    return _render_runtime_single_markdown(report)


def _render_runtime_single_markdown(report: dict[str, Any]) -> str:
    trace = report.get("trace") or {}
    lines = [
        "# RAG Runtime Report",
        "",
        f"- Profile: `{trace.get('profile', '')}`",
        f"- Mode: `{trace.get('mode', '')}`",
        f"- Fusion: `{trace.get('fusion', '') or 'none'}`",
        f"- Tools: {', '.join(trace.get('selected_tools') or [])}",
        "",
        "## Evidence",
        "",
    ]
    for index, item in enumerate(report.get("items") or [], start=1):
        lines.append(
            f"- {index}. `{item.get('chunk_id', '')}` "
            f"{item.get('doc_name', '')} {item.get('point_number', '')}".rstrip()
        )
    if not report.get("items"):
        lines.append("- No evidence items")
    return "\n".join(lines).rstrip() + "\n"


def _render_runtime_batch_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RAG Runtime Batch Report",
        "",
        f"- Profile: `{report.get('profile', '')}`",
        f"- Questions: {report.get('questions_total', 0)}",
        "",
    ]
    for index, question in enumerate(report.get("questions") or [], start=1):
        result = question.get("result") or {}
        trace = result.get("trace") or {}
        lines.extend(
            [
                f"## {index}. {question.get('id', '')}",
                "",
                f"- Query: `{(question.get('request') or {}).get('query', '')}`",
                f"- Tools: {', '.join(trace.get('selected_tools') or [])}",
                f"- Fusion: `{trace.get('fusion', '') or 'none'}`",
                "",
            ]
        )
        items = result.get("items") or []
        if not items:
            lines.append("- No evidence items")
        else:
            top = items[0]
            lines.append(
                f"- Top: `{top.get('chunk_id', '')}` {top.get('doc_name', '')} {top.get('point_number', '')}".rstrip()
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
