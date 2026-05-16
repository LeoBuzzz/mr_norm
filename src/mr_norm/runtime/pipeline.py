from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.runtime.contracts import PipelineResult, PipelineTrace, RuntimeRequest
from mr_norm.runtime.final_answer import EvidenceOnlyFinalAnswer, FinalAnswer, build_final_answer
from mr_norm.runtime.planner import DeterministicPlanner, Planner, build_planner
from mr_norm.runtime.reranker import PassthroughReranker, Reranker, build_reranker
from mr_norm.runtime.tool_runner import ToolRunner, run_runtime
from mr_norm.tools.rtf_processor import atomic_write_json, atomic_write_text


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
