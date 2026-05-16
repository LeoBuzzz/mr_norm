from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeRequest, RuntimeResult
from mr_norm.runtime.final_answer import FinalAnswerProvider
from mr_norm.runtime.llm_clients import (
    HttpPost,
    LLMRequest,
    OpenAICompatibleChatClient,
    build_chat_client,
    parse_json_object,
)
from mr_norm.runtime.llm_profiles import resolve_role_profile
from mr_norm.runtime.planner import PlannerProvider
from mr_norm.runtime.reranker import RerankerProvider

JSON_RESPONSE_FORMAT = {"type": "json_object"}


def _serialize_evidence(items: Sequence[RetrievedItem], *, limit: int = 20) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items[:limit]:
        payload.append(
            {
                "chunk_id": item.chunk_id,
                "doc_name": item.doc_name,
                "point_number": item.point_number,
                "heading_path_text": item.heading_path_text,
                "text": item.text,
                "score": item.score,
                "source_tool": item.source_tool,
            }
        )
    return payload


def _chat_json(
    client: OpenAICompatibleChatClient,
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    response = client.chat(
        LLMRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            model=client.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=JSON_RESPONSE_FORMAT,
        )
    )
    return parse_json_object(response.content)


def build_planner_llm_provider(
    client: OpenAICompatibleChatClient,
    *,
    temperature: float,
    max_tokens: int,
) -> PlannerProvider:
    def provider(request: RuntimeRequest, runtime: RuntimeResult | None, pack: Mapping[str, Any]) -> dict[str, Any]:
        user_payload = {
            "query": request.query,
            "filters": dict(request.filters),
            "profile": request.profile,
            "runtime_selected_tools": list(runtime.trace.selected_tools) if runtime else [],
            "output_contract": pack.get("output_contract"),
        }
        return _chat_json(
            client,
            system_prompt=str(pack.get("prompt") or ""),
            user_payload=user_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return provider


def build_reranker_llm_provider(
    client: OpenAICompatibleChatClient,
    *,
    temperature: float,
    max_tokens: int,
    evidence_limit: int = 20,
) -> RerankerProvider:
    def provider(request: RuntimeRequest, runtime: RuntimeResult, pack: Mapping[str, Any]) -> dict[str, Any]:
        user_payload = {
            "query": request.query,
            "profile": request.profile,
            "evidence": _serialize_evidence(runtime.items, limit=evidence_limit),
            "output_contract": pack.get("output_contract"),
        }
        return _chat_json(
            client,
            system_prompt=str(pack.get("prompt") or ""),
            user_payload=user_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return provider


def build_final_answer_llm_provider(
    client: OpenAICompatibleChatClient,
    *,
    temperature: float,
    max_tokens: int,
) -> FinalAnswerProvider:
    def provider(
        request: RuntimeRequest,
        evidence: Sequence[RetrievedItem],
        pack: Mapping[str, Any],
    ) -> dict[str, Any]:
        user_payload = {
            "query": request.query,
            "evidence": _serialize_evidence(evidence, limit=request.limit),
            "output_contract": pack.get("output_contract"),
        }
        return _chat_json(
            client,
            system_prompt=str(pack.get("prompt") or ""),
            user_payload=user_payload,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return provider


class PipelineLLMProviders:
    __slots__ = ("planner", "reranker", "final_answer")

    def __init__(
        self,
        *,
        planner: PlannerProvider | None = None,
        reranker: RerankerProvider | None = None,
        final_answer: FinalAnswerProvider | None = None,
    ) -> None:
        self.planner = planner
        self.reranker = reranker
        self.final_answer = final_answer


def build_pipeline_llm_providers(
    llm_provider: str,
    *,
    planner_model: str | None = None,
    reranker_model: str | None = None,
    final_answer_model: str | None = None,
    planner_backend: str = "deterministic",
    reranker_backend: str = "passthrough",
    final_answer_backend: str = "evidence",
    keys_path: Path | None = None,
    http_post: HttpPost | None = None,
) -> PipelineLLMProviders:
    if llm_provider == "none":
        return PipelineLLMProviders()

    planner_provider = None
    reranker_provider = None
    final_answer_provider = None

    if planner_backend == "prompt":
        profile = resolve_role_profile(llm_provider, "planner", planner_model)
        client = build_chat_client(
            llm_provider,
            profile.model,
            keys_path=keys_path,
            http_post=http_post,
        )
        planner_provider = build_planner_llm_provider(
            client,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
        )

    if reranker_backend == "prompt":
        profile = resolve_role_profile(llm_provider, "reranker", reranker_model)
        client = build_chat_client(
            llm_provider,
            profile.model,
            keys_path=keys_path,
            http_post=http_post,
        )
        reranker_provider = build_reranker_llm_provider(
            client,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
        )

    if final_answer_backend == "prompt":
        profile = resolve_role_profile(llm_provider, "final_answer", final_answer_model)
        client = build_chat_client(
            llm_provider,
            profile.model,
            keys_path=keys_path,
            http_post=http_post,
        )
        final_answer_provider = build_final_answer_llm_provider(
            client,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
        )

    return PipelineLLMProviders(
        planner=planner_provider,
        reranker=reranker_provider,
        final_answer=final_answer_provider,
    )
