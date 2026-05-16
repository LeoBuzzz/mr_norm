from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.runtime.contracts import PipelineResult, PipelineTrace, RuntimeRequest
from mr_norm.runtime.final_answer import EvidenceOnlyFinalAnswer, FinalAnswer, build_final_answer
from mr_norm.runtime.llm_providers import build_pipeline_llm_providers
from mr_norm.runtime.pipeline_eval import evaluate_pipeline_result, summarize_pipeline_batch
from mr_norm.runtime.planner import DeterministicPlanner, Planner, build_planner
from mr_norm.runtime.reranker import PassthroughReranker, Reranker, build_reranker
from mr_norm.runtime.tool_runner import ToolRunner, run_runtime, runtime_request_from_question
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text


@dataclass(frozen=True)
class PipelineBatchDefaults:
    profile: str = "balanced"
    limit: int = 10
    planner_backend: str = "deterministic"
    reranker_backend: str = "passthrough"
    final_answer_backend: str = "evidence"
    llm_provider: str = "none"
    planner_model: str | None = None
    reranker_model: str | None = None
    final_answer_model: str | None = None
    keys_path: Path | None = None


def run_pipeline(
    request: RuntimeRequest,
    config: IndexingConfig | None = None,
    *,
    tool_runners: dict[str, ToolRunner] | None = None,
    planner: Planner | None = None,
    reranker: Reranker | None = None,
    final_answer: FinalAnswer | None = None,
) -> PipelineResult:
    planner_impl = planner or DeterministicPlanner()
    reranker_impl = reranker or PassthroughReranker()
    final_answer_impl = final_answer or EvidenceOnlyFinalAnswer()

    runtime = run_runtime(request, config, tool_runners=tool_runners)
    planner_result = planner_impl.plan(request, runtime)
    rerank_result = reranker_impl.rerank(request, runtime, limit=request.limit)
    final_result = final_answer_impl.answer(request, rerank_result.items, limit=request.limit)

    warnings = list(runtime.warnings)
    warnings.extend(planner_result.warnings)
    warnings.extend(rerank_result.warnings)
    warnings.extend(final_result.warnings)

    return PipelineResult(
        runtime=runtime,
        planner=planner_result,
        rerank=rerank_result,
        final_answer=final_result,
        trace=PipelineTrace(
            planner_backend=planner_impl.backend_name,
            reranker_backend=reranker_impl.backend_name,
            final_answer_backend=final_answer_impl.backend_name,
        ),
        warnings=warnings,
    )


def pipeline_overrides_from_question(
    question: dict[str, Any],
    defaults: PipelineBatchDefaults,
) -> PipelineBatchDefaults:
    return PipelineBatchDefaults(
        profile=str(question.get("profile") or defaults.profile),
        limit=int(question.get("limit", defaults.limit)),
        planner_backend=str(question.get("planner") or defaults.planner_backend),
        reranker_backend=str(question.get("reranker") or defaults.reranker_backend),
        final_answer_backend=str(
            question.get("final_answer") or question.get("final_answer_backend") or defaults.final_answer_backend
        ),
        llm_provider=str(question.get("llm_provider") or defaults.llm_provider),
        planner_model=question.get("planner_model") or defaults.planner_model,
        reranker_model=question.get("reranker_model") or defaults.reranker_model,
        final_answer_model=question.get("final_answer_model") or defaults.final_answer_model,
        keys_path=defaults.keys_path,
    )


def run_pipeline_batch(
    questions: list[dict[str, Any]],
    config: IndexingConfig | None = None,
    *,
    defaults: PipelineBatchDefaults | None = None,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    config = config or IndexingConfig.from_env()
    batch_defaults = defaults or PipelineBatchDefaults()
    entries: list[dict[str, Any]] = []

    for index, question in enumerate(questions, start=1):
        request = runtime_request_from_question(question, default_limit=batch_defaults.limit, ordinal=index)
        overrides = pipeline_overrides_from_question(question, batch_defaults)
        if question.get("profile") is None:
            request = RuntimeRequest(
                query=request.query,
                filters=request.filters,
                limit=request.limit,
                profile=batch_defaults.profile,
                trace_id=request.trace_id,
                mode=request.mode,
            )

        llm_providers = build_pipeline_llm_providers(
            overrides.llm_provider,
            planner_model=overrides.planner_model,
            reranker_model=overrides.reranker_model,
            final_answer_model=overrides.final_answer_model,
            planner_backend=overrides.planner_backend,
            reranker_backend=overrides.reranker_backend,
            final_answer_backend=overrides.final_answer_backend,
            keys_path=overrides.keys_path,
        )
        pipeline = run_pipeline(
            request,
            config,
            tool_runners=tool_runners,
            planner=build_planner(overrides.planner_backend, provider=llm_providers.planner),
            reranker=build_reranker(overrides.reranker_backend, provider=llm_providers.reranker),
            final_answer=build_final_answer(
                overrides.final_answer_backend,
                provider=llm_providers.final_answer,
            ),
        )
        result = pipeline.to_dict()
        entries.append(
            {
                "id": question.get("id") or question.get("name") or f"q{index}",
                "request": {
                    "query": request.query,
                    "filters": dict(request.filters),
                    "limit": request.limit,
                    "profile": request.profile,
                    "trace_id": request.trace_id,
                    "mode": request.mode,
                },
                "pipeline_config": {
                    "planner": overrides.planner_backend,
                    "reranker": overrides.reranker_backend,
                    "final_answer": overrides.final_answer_backend,
                    "llm_provider": overrides.llm_provider,
                },
                "result": result,
                "evaluation": evaluate_pipeline_result(result),
            }
        )

    return {
        "schema_version": "mr_pipeline_batch_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection_name": config.collection_name,
        "vector_name": config.vector_name,
        "defaults": {
            "profile": batch_defaults.profile,
            "limit": batch_defaults.limit,
            "planner": batch_defaults.planner_backend,
            "reranker": batch_defaults.reranker_backend,
            "final_answer": batch_defaults.final_answer_backend,
            "llm_provider": batch_defaults.llm_provider,
        },
        "questions_total": len(entries),
        "questions": entries,
        "metrics": {
            "elapsed_sec": round(time.perf_counter() - started_at, 6),
            "questions_total": len(entries),
            **summarize_pipeline_batch(entries),
        },
        "warnings": [warning for entry in entries for warning in entry["result"].get("warnings") or []],
    }


def build_default_pipeline(
    *,
    planner_backend: str = "deterministic",
    reranker_backend: str = "passthrough",
    final_answer_backend: str = "evidence",
) -> tuple[Planner, Reranker, FinalAnswer]:
    return (
        build_planner(planner_backend),
        build_reranker(reranker_backend),
        build_final_answer(final_answer_backend),
    )


def save_pipeline_report(report: dict[str, Any], reports_dir: Path, *, prefix: str = "rag_pipeline") -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports_dir / f"{prefix}_{timestamp}.json"
    markdown_path = reports_dir / f"{prefix}_{timestamp}.md"
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, render_pipeline_markdown(report))
    saved = dict(report)
    saved["report_path"] = str(json_path)
    saved["markdown_report_path"] = str(markdown_path)
    return saved


def render_pipeline_markdown(report: dict[str, Any]) -> str:
    if report.get("schema_version") == "mr_pipeline_batch_v1":
        return _render_pipeline_batch_markdown(report)

    trace = report.get("trace") or {}
    runtime_trace = (report.get("runtime") or {}).get("trace") or {}
    final_answer = report.get("final_answer") or {}
    lines = [
        "# RAG Pipeline Report",
        "",
        f"- Planner backend: `{trace.get('planner_backend', '')}`",
        f"- Reranker backend: `{trace.get('reranker_backend', '')}`",
        f"- Final answer backend: `{trace.get('final_answer_backend', '')}`",
        f"- Runtime profile: `{runtime_trace.get('profile', '')}`",
        f"- Runtime fusion: `{runtime_trace.get('fusion', '') or 'none'}`",
        "",
        "## Answer",
        "",
        str(final_answer.get("answer") or "").strip() or "_No answer_",
        "",
        "## Citations",
        "",
    ]
    citations = final_answer.get("citations") or []
    if not citations:
        lines.append("- No citations")
    else:
        for citation in citations:
            lines.append(
                f"- `{citation.get('chunk_id', '')}` "
                f"{citation.get('doc_name', '')} {citation.get('point_number', '')}".rstrip()
            )
    lines.extend(["", "## Evidence", ""])
    for index, item in enumerate((report.get("runtime") or {}).get("items") or [], start=1):
        lines.append(
            f"- {index}. `{item.get('chunk_id', '')}` "
            f"{item.get('doc_name', '')} {item.get('point_number', '')}".rstrip()
        )
    if not (report.get("runtime") or {}).get("items"):
        lines.append("- No evidence items")
    return "\n".join(lines).rstrip() + "\n"


def _render_pipeline_batch_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    lines = [
        "# RAG Pipeline Batch Report",
        "",
        f"- Questions: `{metrics.get('questions_total', 0)}`",
        f"- Warnings total: `{metrics.get('warnings_total', 0)}`",
        f"- Fallback total: `{metrics.get('fallback_total', 0)}`",
        f"- Empty answer rate: `{metrics.get('empty_answer_rate', 0.0)}`",
        f"- Elapsed sec: `{metrics.get('elapsed_sec', 0.0)}`",
        "",
        "## Questions",
        "",
    ]
    for entry in report.get("questions") or []:
        evaluation = entry.get("evaluation") or {}
        trace = evaluation.get("backend_trace") or {}
        final_answer = (entry.get("result") or {}).get("final_answer") or {}
        answer_preview = str(final_answer.get("answer") or "").strip().replace("\n", " ")
        if len(answer_preview) > 120:
            answer_preview = answer_preview[:117] + "..."
        lines.append(f"### {entry.get('id', '')}")
        lines.append(
            f"- Backends: planner=`{trace.get('planner_backend', '')}` "
            f"reranker=`{trace.get('reranker_backend', '')}` "
            f"final_answer=`{trace.get('final_answer_backend', '')}`"
        )
        lines.append(
            f"- Items: `{evaluation.get('items_returned', 0)}` "
            f"citations: `{evaluation.get('citations_count', 0)}` "
            f"warnings: `{evaluation.get('warnings_count', 0)}` "
            f"fallbacks: `{evaluation.get('fallback_count', 0)}`"
        )
        lines.append(f"- Answer: {answer_preview or '_empty_'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
