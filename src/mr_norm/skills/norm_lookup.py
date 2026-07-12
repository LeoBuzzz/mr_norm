from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest
from mr_norm.retrieval.doc_point_topic_index import (
    load_default_doc_point_topic_index,
    merge_topic_retry_items,
    rerank_with_doc_point_topics,
    search_doc_point_topics,
    select_topic_rerank_policy,
    select_topic_retry_policy,
    should_apply_topic_rerank,
    should_attempt_topic_retry,
    topic_hit_to_retrieved_item,
)
from mr_norm.retrieval.gost_definitions import (
    GostSnippet,
    gost_snippet_in_top_evidence,
    merge_gost_into_evidence,
    prefetch_gost_snippets,
)
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import run_point_tool
from mr_norm.runtime.contracts import (
    Citation,
    PipelineResult,
    PreparedQueryPlan,
    PreparedToolQuery,
    QueryUnderstandingResult,
    RuntimeRequest,
)
from mr_norm.runtime.final_answer import build_final_answer
from mr_norm.runtime.llm_providers import build_pipeline_llm_providers
from mr_norm.runtime.pipeline import run_pipeline
from mr_norm.runtime.pipeline_timing import PipelineStageTimings
from mr_norm.runtime.pipeline_diagnostics import (
    merge_retry_items,
    resolve_retrieval_limits,
    select_items_for_final_answer,
    should_retry_doc_point_lookup,
    should_retry_doc_scoped_lookup,
    should_retry_soft_catalog_lookup,
)
from mr_norm.runtime.planner import build_planner
from mr_norm.skills.document_resolve import DocumentResolveResult, resolve_document
from mr_norm.runtime.query_planner import (
    apply_prepared_plan,
    plan_query,
    prepared_plan_to_understanding,
)
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
    understand_query_mode: str = "auto"
    enable_pue_aliases: bool | None = None

    def to_runtime_request(self) -> RuntimeRequest:
        retrieval_limit, final_answer_limit, _ = resolve_retrieval_limits(self.limit)
        return RuntimeRequest(
            query=self.query,
            filters=dict(self.filters),
            limit=self.limit,
            retrieval_limit=retrieval_limit,
            final_answer_limit=final_answer_limit,
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
    gost_prefetch_count: int = 0
    retrieval_limit: int = 0
    final_answer_limit: int = 0
    retry_triggered: bool = False
    retry_reason: str = ""
    pipeline_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_backend": self.planner_backend,
            "reranker_backend": self.reranker_backend,
            "final_answer_backend": self.final_answer_backend,
            "runtime_profile": self.runtime_profile,
            "runtime_fusion": self.runtime_fusion,
            "trace_id": self.trace_id,
            "selected_tools": list(self.selected_tools),
            "gost_prefetch_count": self.gost_prefetch_count,
            "retrieval_limit": self.retrieval_limit,
            "final_answer_limit": self.final_answer_limit,
            "retry_triggered": self.retry_triggered,
            "retry_reason": self.retry_reason,
            "pipeline_diagnostics": dict(self.pipeline_diagnostics),
        }


@dataclass(frozen=True)
class NormLookupResult:
    answer: str
    citations: list[Citation]
    evidence: list[RetrievedItem]
    trace: NormLookupTrace
    warnings: list[str]
    pipeline: PipelineResult
    understanding: QueryUnderstandingResult | None = None
    prepared_plan: PreparedQueryPlan | None = None
    gost_snippets: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "answer": self.answer,
            "citations": [citation.to_dict() for citation in self.citations],
            "evidence": [asdict(item) for item in self.evidence],
            "trace": self.trace.to_dict(),
            "warnings": list(self.warnings),
            "pipeline": self.pipeline.to_dict(),
        }
        if self.understanding is not None:
            payload["understanding"] = self.understanding.to_dict()
        if self.prepared_plan is not None:
            payload["prepared_plan"] = self.prepared_plan.to_dict()
        if self.gost_snippets:
            payload["gost_snippets"] = list(self.gost_snippets)
        return payload


def _run_doc_point_retry(
    *,
    request: NormLookupRequest,
    config: IndexingConfig,
    filters: dict[str, Any],
    prepared_plan: PreparedQueryPlan | None,
) -> tuple[list[RetrievedItem], str]:
    point_number = str(filters.get("point_number") or "").strip()
    if prepared_plan and prepared_plan.point_number_hints and not point_number:
        point_number = str(prepared_plan.point_number_hints[0])
    retry_filters = dict(filters)
    if point_number:
        retry_filters["point_number"] = point_number
    if not retry_filters.get("doc_id") and not retry_filters.get("doc_name"):
        return [], "retry_skip:missing_doc_filter"
    if not point_number:
        return [], "retry_skip:missing_point_hint"

    result = run_point_tool(
        ToolRequest(
            query=point_number,
            filters=retry_filters,
            limit=min(20, request.limit),
            profile=request.profile,
            trace_id=request.trace_id or "norm_lookup_retry",
        ),
        config,
    )
    if not result.items:
        return [], "retry:point_tool_still_empty"
    return list(result.items), "retry:point_tool_refetch"


def _payload_search_query(
    *,
    request: NormLookupRequest,
    prepared_plan: PreparedQueryPlan | None,
) -> str:
    if prepared_plan:
        for entry in prepared_plan.tool_queries:
            if entry.tool_name == "payload" and entry.queries:
                return str(entry.queries[0])
            if entry.tool_name == "vector" and entry.queries:
                return str(entry.queries[0])
    return request.query


def _run_doc_scoped_payload_retry(
    *,
    request: NormLookupRequest,
    config: IndexingConfig,
    filters: dict[str, Any],
    prepared_plan: PreparedQueryPlan | None,
    retry_filters: dict[str, Any] | None = None,
) -> tuple[list[RetrievedItem], str]:
    scoped_filters = dict(retry_filters or filters)
    if not scoped_filters.get("doc_id") and not scoped_filters.get("doc_name"):
        if prepared_plan and prepared_plan.resolved_doc_names:
            catalog_id = str(prepared_plan.document_resolution.catalog_id or "").strip()
            if catalog_id and not catalog_id.startswith("knowledge:"):
                scoped_filters["doc_id"] = catalog_id
            else:
                scoped_filters["doc_name"] = prepared_plan.resolved_doc_names[0]
        else:
            return [], "retry_skip:missing_doc_filter"

    result = run_payload_tool(
        ToolRequest(
            query=_payload_search_query(request=request, prepared_plan=prepared_plan),
            filters=scoped_filters,
            limit=min(25, request.limit),
            profile=request.profile,
            trace_id=request.trace_id or "norm_lookup_payload_retry",
        ),
        config,
    )
    if not result.items:
        return [], "retry:payload_tool_still_empty"
    return list(result.items), "retry:payload_tool_refetch"


def run_norm_lookup(
    request: NormLookupRequest,
    config: IndexingConfig | None = None,
    *,
    keys_path: Path | None = None,
    tool_runners: dict[str, ToolRunner] | None = None,
    project_paths: ProjectPaths | None = None,
) -> NormLookupResult:
    config = config or IndexingConfig.from_env()
    lookup_started = time.perf_counter()
    stage_timings = PipelineStageTimings()
    understanding: QueryUnderstandingResult | None = None
    prepared_plan: PreparedQueryPlan | None = None
    effective_query = request.query
    effective_filters = dict(request.filters)
    gost_snippets: list[GostSnippet] = []
    document_resolve_result: DocumentResolveResult | None = None
    skill_locked_doc_id = ""

    t0 = time.perf_counter()
    gost_snippets = prefetch_gost_snippets(request.query, config, request.filters)
    stage_timings.gost_prefetch_sec = time.perf_counter() - t0

    if not effective_filters.get("doc_id") and not str(effective_filters.get("doc_name") or "").strip():
        document_resolve_result = resolve_document(
            request.query,
            project_paths=project_paths,
            enable_pue_aliases=request.enable_pue_aliases,
        )
        if document_resolve_result.found and document_resolve_result.status in {"exact", "probable"}:
            skill_locked_doc_id = document_resolve_result.doc_id
            effective_filters["doc_id"] = skill_locked_doc_id

    if request.understand_query_mode != "off":
        t0 = time.perf_counter()
        prepared_plan = plan_query(
            request.query,
            filters=effective_filters,
            mode=request.understand_query_mode,
            llm_provider=request.llm_provider,
            keys_path=keys_path,
            project_paths=project_paths,
            enable_pue_aliases=request.enable_pue_aliases,
            gost_snippets=[snippet.to_dict() for snippet in gost_snippets],
        )
        stage_timings.query_planning_sec = time.perf_counter() - t0
        understanding = prepared_plan_to_understanding(prepared_plan)
        effective_query, effective_filters = apply_prepared_plan(
            request.query,
            effective_filters,
            prepared_plan,
            skill_locked_doc_id=skill_locked_doc_id,
        )

    retrieval_limit, final_answer_limit, _ = resolve_retrieval_limits(request.limit)
    runtime_request = RuntimeRequest(
        query=effective_query,
        filters=effective_filters,
        limit=request.limit,
        retrieval_limit=retrieval_limit,
        final_answer_limit=final_answer_limit,
        profile=request.profile,
        trace_id=request.trace_id or "norm_lookup",
        mode=request.mode,
        prepared_plan=prepared_plan,
    )

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
    final_answer_impl = build_final_answer(
        request.final_answer_backend,
        provider=llm_providers.final_answer,
    )
    pipeline = run_pipeline(
        runtime_request,
        config,
        tool_runners=tool_runners,
        planner=build_planner(request.planner_backend, provider=llm_providers.planner),
        reranker=build_reranker(request.reranker_backend, provider=llm_providers.reranker),
        final_answer=final_answer_impl,
    )

    retry_triggered = False
    retry_reason = ""
    retry_items_added = 0
    ranked_items = list(pipeline.rerank.items)
    retry_mode = "point"
    soft_retry_filters: dict[str, Any] | None = None
    do_retry, retry_reason = should_retry_doc_point_lookup(
        filters=effective_filters,
        prepared_plan=prepared_plan,
        ranked_items=ranked_items,
        tool_results=pipeline.runtime.tool_results,
        top_n=final_answer_limit,
    )
    if not do_retry:
        question_type = str(prepared_plan.question_type or "") if prepared_plan else ""
        do_retry, retry_reason = should_retry_doc_scoped_lookup(
            filters=effective_filters,
            prepared_plan=prepared_plan,
            ranked_items=ranked_items,
            tool_results=pipeline.runtime.tool_results,
            top_n=final_answer_limit,
            question_type=question_type,
        )
        if do_retry:
            retry_mode = "payload"
    if not do_retry:
        do_retry, retry_reason, soft_retry_filters = should_retry_soft_catalog_lookup(
            filters=effective_filters,
            prepared_plan=prepared_plan,
            ranked_items=ranked_items,
            top_n=final_answer_limit,
        )
        if do_retry:
            retry_mode = "payload"
    if do_retry:
        t0 = time.perf_counter()
        if retry_mode == "payload":
            retry_items, retry_status = _run_doc_scoped_payload_retry(
                request=request,
                config=config,
                filters=effective_filters,
                prepared_plan=prepared_plan,
                retry_filters=soft_retry_filters,
            )
        else:
            retry_items, retry_status = _run_doc_point_retry(
                request=request,
                config=config,
                filters=effective_filters,
                prepared_plan=prepared_plan,
            )
        retry_reason = retry_status
        if retry_items:
            retry_triggered = True
            ranked_items, retry_items_added = merge_retry_items(
                ranked_items,
                retry_items,
                limit=retrieval_limit,
            )
            pipeline = replace(
                pipeline,
                rerank=replace(pipeline.rerank, items=ranked_items),
                diagnostics={
                    **dict(pipeline.diagnostics),
                    "retry_triggered": True,
                    "retry_reason": retry_reason,
                    "retry_items_added": retry_items_added,
                },
            )
            if request.final_answer_backend == "prompt" and llm_providers.final_answer is not None:
                final_evidence = select_items_for_final_answer(
                    ranked_items,
                    request=runtime_request,
                    limit=final_answer_limit,
                )
                merged_final = final_answer_impl.answer(
                    runtime_request,
                    final_evidence,
                    limit=final_answer_limit,
                )
                pipeline = replace(
                    pipeline,
                    final_answer=merged_final,
                )
            stage_timings.retry_sec += time.perf_counter() - t0

    topic_rerank_applied = False
    topic_rerank_reason = ""
    topic_rerank_policy = ""
    topic_rerank_items_promoted = 0
    topic_rerank_top_hits: list[dict[str, Any]] = []
    do_topic_rerank, topic_rerank_reason = should_apply_topic_rerank(
        query=request.query,
        question_type=str(prepared_plan.question_type or "") if prepared_plan else "",
        ranked_items=ranked_items,
        resolved_doc_names=tuple(prepared_plan.resolved_doc_names) if prepared_plan else (),
        point_number_hints=tuple(prepared_plan.point_number_hints) if prepared_plan else (),
    )
    if do_topic_rerank:
        t0 = time.perf_counter()
        try:
            resolved_doc_ids: tuple[str, ...] = ()
            if prepared_plan and prepared_plan.document_resolution.catalog_id:
                resolved_doc_ids = (prepared_plan.document_resolution.catalog_id,)
            rerank_policy = select_topic_rerank_policy(
                request.query,
                question_type=str(prepared_plan.question_type or "") if prepared_plan else "",
                resolved_doc_ids=resolved_doc_ids,
                resolved_doc_names=tuple(prepared_plan.resolved_doc_names) if prepared_plan else (),
                ranked_items=ranked_items,
            )
            topic_rerank_policy = rerank_policy.search_profile + ":promote"
            topic_index = load_default_doc_point_topic_index(
                str((project_paths or ProjectPaths.from_root(None)).root)
            )
            rerank_result = rerank_with_doc_point_topics(
                request.query,
                ranked_items,
                topic_index,
                policy=rerank_policy,
            )
            topic_rerank_reason = rerank_result.reason
            topic_rerank_top_hits = [
                {
                    "doc_id": hit.entry.doc_id,
                    "doc_name": hit.entry.doc_name,
                    "point_number": hit.entry.point_number,
                    "score": hit.score,
                    "hits": list(hit.hits),
                }
                for hit in rerank_result.top_hits
            ]
            if rerank_result.applied:
                topic_rerank_applied = True
                topic_rerank_items_promoted = rerank_result.items_promoted
                ranked_items = list(rerank_result.ranked_items)
                pipeline = replace(
                    pipeline,
                    rerank=replace(pipeline.rerank, items=ranked_items),
                    diagnostics={
                        **dict(pipeline.diagnostics),
                        "topic_rerank_applied": True,
                        "topic_rerank_reason": topic_rerank_reason,
                        "topic_rerank_policy": topic_rerank_policy,
                        "topic_rerank_items_promoted": topic_rerank_items_promoted,
                        "topic_rerank_top_hits": topic_rerank_top_hits,
                    },
                )
                if request.final_answer_backend == "prompt" and llm_providers.final_answer is not None:
                    final_evidence = select_items_for_final_answer(
                        ranked_items,
                        request=runtime_request,
                        limit=final_answer_limit,
                    )
                    merged_final = final_answer_impl.answer(
                        runtime_request,
                        final_evidence,
                        limit=final_answer_limit,
                    )
                    pipeline = replace(
                        pipeline,
                        final_answer=merged_final,
                    )
            else:
                pipeline = replace(
                    pipeline,
                    diagnostics={
                        **dict(pipeline.diagnostics),
                        "topic_rerank_applied": False,
                        "topic_rerank_reason": topic_rerank_reason,
                        "topic_rerank_policy": topic_rerank_policy,
                        "topic_rerank_items_promoted": 0,
                        "topic_rerank_top_hits": topic_rerank_top_hits,
                    },
                )
        except Exception as exc:
            topic_rerank_reason = f"topic_rerank:error:{type(exc).__name__}: {exc}"
        stage_timings.topic_rerank_sec += time.perf_counter() - t0

    topic_retry_triggered = False
    topic_retry_reason = ""
    topic_retry_items_added = 0
    topic_retry_policy = ""
    if request.final_answer_backend == "prompt" and llm_providers.final_answer is not None:
        should_topic_retry, topic_retry_reason = should_attempt_topic_retry(
            query=request.query,
            final_warnings=list(pipeline.final_answer.warnings),
            has_citations=bool(pipeline.final_answer.citations),
            resolved_doc_names=prepared_plan.resolved_doc_names if prepared_plan else (),
            topic_rerank_applied=topic_rerank_applied,
        )
        if should_topic_retry:
            t0 = time.perf_counter()
            try:
                topic_policy = select_topic_retry_policy(request.query)
                topic_retry_policy = (
                    f"{topic_policy.search_profile}:{topic_policy.evidence_policy}"
                )
                topic_index = load_default_doc_point_topic_index(
                    str((project_paths or ProjectPaths.from_root(None)).root)
                )
                topic_hits = search_doc_point_topics(
                    request.query,
                    topic_index,
                    top_k=8,
                    profile=topic_policy.search_profile,
                )
                topic_items = [topic_hit_to_retrieved_item(hit) for hit in topic_hits]
                if topic_items:
                    ranked_items, topic_retry_items_added = merge_topic_retry_items(
                        topic_items=topic_items,
                        ranked_items=ranked_items,
                        policy=topic_policy.evidence_policy,
                        limit=retrieval_limit,
                    )
                    final_evidence = select_items_for_final_answer(
                        ranked_items,
                        request=runtime_request,
                        limit=final_answer_limit,
                    )
                    topic_final = final_answer_impl.answer(
                        runtime_request,
                        final_evidence,
                        limit=final_answer_limit,
                    )
                    pipeline = replace(
                        pipeline,
                        rerank=replace(pipeline.rerank, items=ranked_items),
                        final_answer=topic_final,
                        diagnostics={
                            **dict(pipeline.diagnostics),
                            "topic_retry_triggered": True,
                            "topic_retry_reason": topic_retry_reason,
                            "topic_retry_policy": topic_retry_policy,
                            "topic_retry_items_added": topic_retry_items_added,
                            "topic_retry_top_hits": [
                                {
                                    "doc_id": hit.entry.doc_id,
                                    "doc_name": hit.entry.doc_name,
                                    "point_number": hit.entry.point_number,
                                    "score": hit.score,
                                    "hits": list(hit.hits),
                                }
                                for hit in topic_hits[:5]
                            ],
                        },
                    )
                    topic_retry_triggered = True
                else:
                    topic_retry_reason = "topic_retry:no_hits"
            except Exception as exc:
                topic_retry_reason = f"topic_retry:error:{type(exc).__name__}: {exc}"
            stage_timings.retry_sec += time.perf_counter() - t0

    evidence = ranked_items[: request.limit]
    answer = pipeline.final_answer.answer
    citations = list(pipeline.final_answer.citations)
    final_warnings = list(pipeline.final_answer.warnings)

    if gost_snippets:
        pre_merge_evidence = list(evidence)
        evidence = merge_gost_into_evidence(evidence, gost_snippets)
        should_regenerate = gost_snippet_in_top_evidence(
            pre_merge_evidence,
            gost_snippets,
            top_n=5,
        )
        if (
            should_regenerate
            and request.final_answer_backend == "prompt"
            and llm_providers.final_answer is not None
        ):
            t0 = time.perf_counter()
            final_evidence = select_items_for_final_answer(
                evidence,
                request=runtime_request,
                limit=final_answer_limit,
            )
            merged_final = final_answer_impl.answer(runtime_request, final_evidence, limit=final_answer_limit)
            stage_timings.gost_merge_answer_sec = time.perf_counter() - t0
            answer = merged_final.answer
            citations = list(merged_final.citations)
            final_warnings = list(merged_final.warnings)

    runtime_trace = pipeline.runtime.trace
    plan_warnings = list(prepared_plan.warnings if prepared_plan else [])
    understanding_warnings = (
        list(understanding.warnings) if understanding is not None else []
    )
    warnings = list(pipeline.warnings) + plan_warnings + understanding_warnings + final_warnings
    if retry_triggered:
        warnings.append(f"doc_point_retry:{retry_reason}:added={retry_items_added}")
    warnings = list(dict.fromkeys(warnings))

    diagnostics = dict(pipeline.diagnostics)
    diagnostics["retry_triggered"] = retry_triggered
    diagnostics["retry_reason"] = retry_reason
    diagnostics["retry_items_added"] = retry_items_added
    diagnostics["topic_rerank_applied"] = topic_rerank_applied
    diagnostics["topic_rerank_reason"] = topic_rerank_reason
    diagnostics["topic_rerank_policy"] = topic_rerank_policy
    diagnostics["topic_rerank_items_promoted"] = topic_rerank_items_promoted
    diagnostics["topic_rerank_top_hits"] = topic_rerank_top_hits
    diagnostics["topic_retry_triggered"] = topic_retry_triggered
    diagnostics["topic_retry_reason"] = topic_retry_reason
    diagnostics["topic_retry_policy"] = topic_retry_policy
    diagnostics["topic_retry_items_added"] = topic_retry_items_added
    if document_resolve_result is not None:
        diagnostics["document_resolve_found"] = document_resolve_result.found
        diagnostics["document_resolve_status"] = document_resolve_result.status
        diagnostics["document_resolve_doc_id"] = document_resolve_result.doc_id
        diagnostics["document_resolve_doc_name"] = document_resolve_result.doc_name
        diagnostics["document_resolve_mention_kind"] = document_resolve_result.mention_kind
        diagnostics["document_resolve_confidence"] = document_resolve_result.confidence
        diagnostics["document_resolve_warnings"] = list(document_resolve_result.warnings)
    pipeline_stage_timings = PipelineStageTimings(**(diagnostics.get("stage_timings") or {}))
    stage_timings = stage_timings.merge(pipeline_stage_timings)
    stage_timings.norm_lookup_sec = time.perf_counter() - lookup_started
    diagnostics["stage_timings"] = stage_timings.with_total().to_dict()

    return NormLookupResult(
        answer=answer,
        citations=citations,
        evidence=evidence,
        trace=NormLookupTrace(
            planner_backend=pipeline.trace.planner_backend,
            reranker_backend=pipeline.trace.reranker_backend,
            final_answer_backend=pipeline.trace.final_answer_backend,
            runtime_profile=runtime_trace.profile,
            runtime_fusion=runtime_trace.fusion,
            trace_id=runtime_trace.trace_id,
            selected_tools=tuple(runtime_trace.selected_tools),
            gost_prefetch_count=len(gost_snippets),
            retrieval_limit=retrieval_limit,
            final_answer_limit=final_answer_limit,
            retry_triggered=retry_triggered,
            retry_reason=retry_reason,
            pipeline_diagnostics=diagnostics,
        ),
        warnings=warnings,
        pipeline=pipeline,
        understanding=understanding,
        prepared_plan=prepared_plan,
        gost_snippets=tuple(snippet.to_dict() for snippet in gost_snippets),
    )
