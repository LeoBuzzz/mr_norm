from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.compare import reciprocal_rank_fusion
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import is_point_lookup_filters, run_point_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.retrieval.query_intent import structural_reference_queries
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace, ToolCallPlan
from mr_norm.runtime.pipeline_features import rrf_weights_enabled
from mr_norm.runtime.profiles import get_profile_config
from mr_norm.runtime.router import route_runtime
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text

ToolRunner = Callable[[ToolRequest, IndexingConfig], ToolResult]

STRUCTURAL_NEIGHBOR_CHUNK_CAP = 4
_STRUCTURAL_MARKER = re.compile(
    r"(?i:\b(таблиц\w*|приложен\w*)\s*(?:№\s*|n\s*)?)([0-9]+(?:[._][0-9]+)*|[А-ЯЁA-Z])?"
)


def _structural_target(reference: str) -> tuple[str, str]:
    kind, _, label = reference.partition(" ")
    return ("table" if kind == "таблица" else "appendix", label.strip().casefold())


def _matches_structural_target(item: RetrievedItem, reference: str) -> bool:
    kind, label = _structural_target(reference)
    text = f"{item.text}\n{item.heading_path_text}"
    for match in _STRUCTURAL_MARKER.finditer(text):
        marker_kind = "table" if match.group(1).casefold().startswith("таблиц") else "appendix"
        marker_label = (match.group(2) or "").replace("_", ".").casefold()
        if marker_kind == kind and (marker_label == label or (kind == "appendix" and not label)):
            return True
    return False


def _truncate_at_next_structural_marker(item: RetrievedItem, reference: str) -> tuple[RetrievedItem | None, bool]:
    target_kind, target_label = _structural_target(reference)
    for match in _STRUCTURAL_MARKER.finditer(item.text):
        marker_kind = "table" if match.group(1).casefold().startswith("таблиц") else "appendix"
        marker_label = (match.group(2) or "").replace("_", ".").casefold()
        is_boundary = (
            marker_kind == "appendix" and target_kind == "table"
        ) or (
            marker_kind == target_kind and marker_label != target_label
        )
        if not is_boundary:
            continue
        text = item.text[: match.start()].rstrip()
        return (replace(item, text=text) if text else None), True
    return item, False


def _resolved_structural_doc_scope(request: RuntimeRequest) -> dict[str, str]:
    filters = request.filters or {}
    doc_id = str(filters.get("doc_id") or "").strip()
    if doc_id:
        return {"doc_id": doc_id}
    doc_name = str(filters.get("doc_name") or "").strip()
    if doc_name:
        return {"doc_name": doc_name}
    plan = request.prepared_plan
    if plan is None or plan.ambiguous or len(plan.resolved_doc_names) != 1:
        return {}
    catalog_id = str(plan.document_resolution.catalog_id or "").strip()
    if catalog_id and not catalog_id.startswith("knowledge:"):
        return {"doc_id": catalog_id}
    return {"doc_name": plan.resolved_doc_names[0]}


def _expand_structural_neighbors(
    request: RuntimeRequest,
    config: IndexingConfig,
    runners: dict[str, ToolRunner],
    step_results: dict[str, ToolResult],
) -> None:
    references = structural_reference_queries(request.query)
    has_table_target = any(reference.startswith("таблица ") for reference in references)
    resolved_scope = _resolved_structural_doc_scope(request)
    payload_result = step_results.get("payload")
    payload_runner = runners.get("payload")
    if (
        not references
        or not resolved_scope
        or payload_result is None
        or payload_runner is None
    ):
        return

    candidates: list[RetrievedItem] = []
    for tool_name in ("payload", "vector"):
        result = step_results.get(tool_name)
        if result:
            candidates.extend(result.items)
    seen_targets: set[tuple[str, str]] = set()
    additions: dict[str, list[RetrievedItem]] = {}
    headers: dict[str, RetrievedItem] = {}
    added_items: list[RetrievedItem] = []
    extra_calls = 0
    extra_warnings: list[str] = []

    for reference in references:
        if reference == "приложение" and has_table_target:
            continue
        for header in candidates:
            if ":table:" in header.chunk_id or not _matches_structural_target(header, reference):
                continue
            if resolved_scope.get("doc_id") and header.doc_id != resolved_scope["doc_id"]:
                continue
            if resolved_scope.get("doc_name") and header.doc_name != resolved_scope["doc_name"]:
                continue
            doc_key = header.doc_id or header.doc_name
            if not doc_key:
                continue
            target_key = (reference, doc_key)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            parent_id = header.chunk_id
            headers[parent_id] = replace(
                header,
                source_tool="payload",
                matched={**header.matched, "structural_target": reference},
            )
            if header.chunk_index is None:
                break
            doc_filter = {"doc_id": header.doc_id} if header.doc_id else {"doc_name": header.doc_name}
            stop = False
            for offset in range(1, STRUCTURAL_NEIGHBOR_CHUNK_CAP + 1):
                try:
                    neighbor_result = payload_runner(
                        ToolRequest(
                            filters={**doc_filter, "chunk_index": header.chunk_index + offset},
                            limit=1,
                            profile="fast",
                            trace_id=f"{request.trace_id}:structural_neighbor",
                        ),
                        config,
                    )
                except Exception as exc:
                    extra_warnings.append(
                        f"structural_neighbor failed: {type(exc).__name__}: {exc}"
                    )
                    break
                extra_calls += neighbor_result.metrics.qdrant_calls
                extra_warnings.extend(neighbor_result.warnings)
                neighbor = next(
                    (
                        item
                        for item in neighbor_result.items
                        if item.chunk_index == header.chunk_index + offset
                        and (item.doc_id == header.doc_id if header.doc_id else item.doc_name == header.doc_name)
                    ),
                    None,
                )
                if neighbor is None:
                    break
                bounded, stop = _truncate_at_next_structural_marker(neighbor, reference)
                if bounded is not None and bounded.text.strip() and bounded.chunk_id != parent_id:
                    bounded = replace(
                        bounded,
                        matched={
                            **bounded.matched,
                            "structural_target": reference,
                            "structural_parent_chunk_id": parent_id,
                        },
                    )
                    additions.setdefault(parent_id, []).append(bounded)
                    added_items.append(bounded)
                if stop:
                    break
            break

    if not added_items and not headers:
        return
    merged: list[RetrievedItem] = []
    payload_ids = {item.chunk_id for item in payload_result.items if item.chunk_id}
    replacements = {
        item.chunk_id: item
        for group in additions.values()
        for item in group
        if item.chunk_id
    }
    emitted_ids: set[str] = set()

    def append_group(header_id: str, header: RetrievedItem) -> None:
        if header_id not in emitted_ids:
            merged.append(header)
            if header_id:
                emitted_ids.add(header_id)
        for addition in additions.get(header_id, []):
            if addition.chunk_id and addition.chunk_id in emitted_ids:
                continue
            merged.append(addition)
            if addition.chunk_id:
                emitted_ids.add(addition.chunk_id)

    for header_id, header in headers.items():
        if header_id not in payload_ids:
            append_group(header_id, header)
    for item in payload_result.items:
        if item.chunk_id in replacements:
            continue
        item = headers.get(item.chunk_id, item)
        if item.chunk_id and item.chunk_id in emitted_ids:
            continue
        merged.append(item)
        if item.chunk_id:
            emitted_ids.add(item.chunk_id)
        if item.chunk_id in additions:
            append_group(item.chunk_id, item)
    step_results["payload"] = replace(
        payload_result,
        items=merged,
        metrics=replace(
            payload_result.metrics,
            candidates_returned=len(merged),
            qdrant_calls=payload_result.metrics.qdrant_calls + extra_calls,
        ),
        warnings=list(dict.fromkeys([*payload_result.warnings, *extra_warnings])),
    )


def _prioritize_structural_groups(
    items: list[RetrievedItem],
    payload_result: ToolResult | None,
    query: str,
) -> list[RetrievedItem]:
    if not payload_result or not structural_reference_queries(query):
        return items
    grouped = [
        item
        for item in payload_result.items
        if item.matched.get("structural_target")
    ]
    if not grouped:
        return items
    promoted: list[RetrievedItem] = []
    for target in dict.fromkeys(str(item.matched.get("structural_target")) for item in grouped):
        headers = [
            item
            for item in grouped
            if item.matched.get("structural_target") == target
            and not item.matched.get("structural_parent_chunk_id")
        ]
        for header in headers:
            promoted.append(header)
            promoted.extend(
                sorted(
                    (
                        item
                        for item in grouped
                        if item.matched.get("structural_parent_chunk_id") == header.chunk_id
                    ),
                    key=lambda item: item.chunk_index if item.chunk_index is not None else 0,
                )
            )
    promoted_ids = {item.chunk_id for item in promoted if item.chunk_id}
    return [*promoted, *(item for item in items if not item.chunk_id or item.chunk_id not in promoted_ids)]


class CachedQueryEmbedder:
    """Small in-process LRU cache for repeated query embeddings."""

    def __init__(self, base_embedder: Any, *, max_size: int = 512) -> None:
        self._base = base_embedder
        self._max_size = max_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(text: str) -> str:
        return " ".join((text or "").strip().lower().split())

    def encode(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(text) for text in texts]
        results: list[list[float] | None] = []
        missing: list[tuple[int, str, str]] = []
        with self._lock:
            for index, (text, key) in enumerate(zip(texts, keys, strict=True)):
                cached = self._cache.get(key)
                if cached is not None:
                    self.hits += 1
                    self._cache.move_to_end(key)
                    results.append(cached)
                else:
                    self.misses += 1
                    results.append(None)
                    missing.append((index, text, key))

        if missing:
            encoded = self._base.encode([text for _index, text, _key in missing])
            with self._lock:
                for vector, (index, _text, key) in zip(encoded, missing, strict=True):
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._max_size:
                        self._cache.popitem(last=False)
                    results[index] = vector

        return [vector for vector in results if vector is not None]


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

            vector_embedder = CachedQueryEmbedder(SentenceTransformerEmbedder(config))
        return run_vector_tool(request, config, embedder=vector_embedder)

    return {
        "point": run_point_tool,
        "payload": run_payload_tool,
        "vector": vector_runner,
    }


def _parallel_tools_enabled() -> bool:
    flag = os.environ.get("MR_NORM_DISABLE_PARALLEL_TOOLS", "").strip().lower()
    return flag not in {"1", "true", "yes", "on"}


def _queries_for_step(step: ToolCallPlan) -> tuple[tuple[str, ...], list[str]]:
    warnings: list[str] = []
    queries = tuple(query.strip() for query in step.queries if query.strip())
    if not queries:
        queries = (step.request.query.strip(),) if step.request.query.strip() else ()
    if not queries and step.tool_name == "point" and is_point_lookup_filters(dict(step.request.filters)):
        queries = ("",)
    if not queries and step.tool_name != "point":
        warnings.append(f"{step.tool_name} skipped: empty query list")
    return queries, warnings


def _run_tool_step(
    step: ToolCallPlan,
    runner: ToolRunner,
    config: IndexingConfig,
    queries: tuple[str, ...],
) -> ToolResult:
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
    return _merge_tool_results(
        per_query_results,
        tool_name=step.tool_name,
        trace_id=step.request.trace_id,
    )


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

    runnable_steps: list[tuple[ToolCallPlan, ToolRunner, tuple[str, ...]]] = []
    for step in sorted(plan, key=lambda item: item.priority):
        runner = runners.get(step.tool_name)
        if runner is None:
            warnings.append(f"unknown runtime tool: {step.tool_name}")
            continue
        queries, query_warnings = _queries_for_step(step)
        warnings.extend(query_warnings)
        if not queries:
            continue
        runnable_steps.append((step, runner, queries))

    step_results: dict[str, ToolResult] = {}
    step_errors: dict[str, str] = {}
    if _parallel_tools_enabled() and len(runnable_steps) > 1:
        with ThreadPoolExecutor(max_workers=len(runnable_steps)) as executor:
            future_to_step = {
                executor.submit(_run_tool_step, step, runner, config, queries): step
                for step, runner, queries in runnable_steps
            }
            for future in as_completed(future_to_step):
                step = future_to_step[future]
                try:
                    step_results[step.tool_name] = future.result()
                except Exception as exc:
                    step_errors[step.tool_name] = f"{step.tool_name} failed: {type(exc).__name__}: {exc}"
    else:
        for step, runner, queries in runnable_steps:
            try:
                step_results[step.tool_name] = _run_tool_step(step, runner, config, queries)
            except Exception as exc:
                step_errors[step.tool_name] = f"{step.tool_name} failed: {type(exc).__name__}: {exc}"

    _expand_structural_neighbors(request, config, runners, step_results)

    for step, _runner, _queries in runnable_steps:
        error = step_errors.get(step.tool_name)
        if error:
            warnings.append(error)
            continue
        result = step_results.get(step.tool_name)
        if result is None:
            continue
        tool_results[step.tool_name] = result
        qdrant_calls += result.metrics.qdrant_calls
        if result.items:
            tools_succeeded += 1
        warnings.extend(result.warnings)

    items = []
    fusion = ""
    retrieval_limit = request.retrieval_limit if request.retrieval_limit is not None else request.limit
    if profile.use_hybrid and len(tool_results) > 1:
        fusion_weights = None if rrf_weights_enabled() else {}
        fused = reciprocal_rank_fusion(tool_results, limit=retrieval_limit, weights=fusion_weights)
        fused = _prioritize_structural_groups(fused, tool_results.get("payload"), request.query)
        fused = fused[:retrieval_limit]
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
                limit=retrieval_limit,
                profile=profile.name,
            ),
            metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=len(fused), qdrant_calls=0),
            warnings=[],
        )
    elif tool_results:
        for tool_name in ("point", "payload", "vector"):
            result = tool_results.get(tool_name)
            if result and result.items:
                items = _prioritize_structural_groups(result.items, tool_results.get("payload"), request.query)
                items = items[:retrieval_limit]
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
            retrieval_limit=retrieval_limit,
            final_answer_limit=request.final_answer_limit or request.limit,
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
