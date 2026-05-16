from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import Citation, PipelineResult, RuntimeRequest
from mr_norm.runtime.final_answer import build_final_answer
from mr_norm.runtime.llm_providers import build_pipeline_llm_providers
from mr_norm.runtime.pipeline import run_pipeline
from mr_norm.runtime.planner import build_planner
from mr_norm.runtime.reranker import build_reranker
from mr_norm.runtime.tool_runner import ToolRunner


@dataclass(frozen=True)
class NormLookupRequest:
    query: str
    filters: dict[str, Any] = field(default_factory=dict)
    profile: str = "balanced"
    limit: int = 10
    trace_id: str = ""
    mode: str = "evidence"
    planner_backend: str = "deterministic"
    reranker_backend: str = "passthrough"
    final_answer_backend: str = "evidence"
    llm_provider: str = "none"
    planner_model: str | None = None
    reranker_model: str | None = None
    final_answer_model: str | None = None

    def to_runtime_request(self) -> RuntimeRequest:
        return RuntimeRequest(
            query=self.query,
            filters=dict(self.filters),
            limit=self.limit,
            profile=self.profile,
            trace_id=self.trace_id or "norm_lookup",
            mode=self.mode,
        )


@dataclass(frozen=True)
class NormLookupTrace:
    planner_backend: str
    reranker_backend: str
    final_answer_backend: str
    runtime_profile: str
    runtime_fusion: str
    trace_id: str
    selected_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_backend": self.planner_backend,
            "reranker_backend": self.reranker_backend,
            "final_answer_backend": self.final_answer_backend,
            "runtime_profile": self.runtime_profile,
            "runtime_fusion": self.runtime_fusion,
            "trace_id": self.trace_id,
            "selected_tools": list(self.selected_tools),
        }


@dataclass(frozen=True)
class NormLookupResult:
    answer: str
    citations: list[Citation]
    evidence: list[RetrievedItem]
    trace: NormLookupTrace
    warnings: list[str]
    pipeline: PipelineResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [citation.to_dict() for citation in self.citations],
            "evidence": [asdict(item) for item in self.evidence],
            "trace": self.trace.to_dict(),
            "warnings": list(self.warnings),
            "pipeline": self.pipeline.to_dict(),
        }


def run_norm_lookup(
    request: NormLookupRequest,
    config: IndexingConfig | None = None,
    *,
    keys_path: Path | None = None,
    tool_runners: dict[str, ToolRunner] | None = None,
) -> NormLookupResult:
    llm_providers = build_pipeline_llm_providers(
        request.llm_provider,
        planner_model=request.planner_model,
        reranker_model=request.reranker_model,
        final_answer_model=request.final_answer_model,
        planner_backend=request.planner_backend,
        reranker_backend=request.reranker_backend,
        final_answer_backend=request.final_answer_backend,
        keys_path=keys_path,
    )
    pipeline = run_pipeline(
        request.to_runtime_request(),
        config,
        tool_runners=tool_runners,
        planner=build_planner(request.planner_backend, provider=llm_providers.planner),
        reranker=build_reranker(request.reranker_backend, provider=llm_providers.reranker),
        final_answer=build_final_answer(
            request.final_answer_backend,
            provider=llm_providers.final_answer,
        ),
    )
    runtime_trace = pipeline.runtime.trace
    return NormLookupResult(
        answer=pipeline.final_answer.answer,
        citations=list(pipeline.final_answer.citations),
        evidence=list(pipeline.rerank.items),
        trace=NormLookupTrace(
            planner_backend=pipeline.trace.planner_backend,
            reranker_backend=pipeline.trace.reranker_backend,
            final_answer_backend=pipeline.trace.final_answer_backend,
            runtime_profile=runtime_trace.profile,
            runtime_fusion=runtime_trace.fusion,
            trace_id=runtime_trace.trace_id,
            selected_tools=tuple(runtime_trace.selected_tools),
        ),
        warnings=list(pipeline.warnings),
        pipeline=pipeline,
    )
