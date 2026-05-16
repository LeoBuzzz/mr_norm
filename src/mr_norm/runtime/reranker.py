from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RerankResult, RuntimeRequest, RuntimeResult
from mr_norm.runtime.prompts import load_prompt_pack_by_role

TOOL_PRIORITY = {"point": 0, "payload": 1, "vector": 2, "hybrid_rrf": 3}
RerankerProvider = Callable[[RuntimeRequest, RuntimeResult, dict[str, Any]], Mapping[str, Any]]


class Reranker(Protocol):
    backend_name: str

    def rerank(
        self,
        request: RuntimeRequest,
        runtime: RuntimeResult,
        *,
        limit: int | None = None,
    ) -> RerankResult: ...


def _tool_priority(item: RetrievedItem) -> int:
    return TOOL_PRIORITY.get(item.source_tool, 99)


def _item_score(item: RetrievedItem) -> float:
    if item.score is None:
        return 0.0
    return float(item.score)


class PassthroughReranker:
    backend_name = "passthrough"

    def rerank(
        self,
        request: RuntimeRequest,
        runtime: RuntimeResult,
        *,
        limit: int | None = None,
    ) -> RerankResult:
        effective_limit = limit if limit is not None else request.limit
        items = list(runtime.items[:effective_limit])
        scores = {item.chunk_id: _item_score(item) for item in items if item.chunk_id}
        return RerankResult(items=items, scores=scores)


class ScoreReranker:
    backend_name = "score"

    def rerank(
        self,
        request: RuntimeRequest,
        runtime: RuntimeResult,
        *,
        limit: int | None = None,
    ) -> RerankResult:
        effective_limit = limit if limit is not None else request.limit
        ranked = sorted(
            runtime.items,
            key=lambda item: (-_item_score(item), _tool_priority(item), item.chunk_id),
        )
        items = ranked[:effective_limit]
        scores = {item.chunk_id: _item_score(item) for item in items if item.chunk_id}
        return RerankResult(items=items, scores=scores)


def _parse_ranked_chunk_ids(payload: Mapping[str, Any], evidence: Sequence[RetrievedItem]) -> tuple[list[RetrievedItem], list[str]]:
    from mr_norm.runtime.llm_payloads import normalize_reranker_payload

    normalized, normalize_warnings = normalize_reranker_payload(payload)
    warnings = list(normalize_warnings)
    raw_ids = normalized["ranked_chunk_ids"]

    by_id = {item.chunk_id: item for item in evidence if item.chunk_id}
    items: list[RetrievedItem] = []
    for chunk_id in raw_ids:
        key = chunk_id
        if not key:
            warnings.append("reranker ignored empty chunk_id")
            continue
        item = by_id.get(key)
        if item is None:
            warnings.append(f"reranker ignored unknown chunk_id: {key!r}")
            continue
        if key not in {existing.chunk_id for existing in items}:
            items.append(item)
    return items, warnings


class PromptPackReranker:
    backend_name = "prompt"

    def __init__(self, *, provider: RerankerProvider | None = None) -> None:
        self._pack = load_prompt_pack_by_role("reranker")
        self._provider = provider

    def rerank(
        self,
        request: RuntimeRequest,
        runtime: RuntimeResult,
        *,
        limit: int | None = None,
    ) -> RerankResult:
        effective_limit = limit if limit is not None else request.limit
        if self._provider is None:
            fallback = PassthroughReranker().rerank(request, runtime, limit=effective_limit)
            return RerankResult(
                items=fallback.items,
                scores=fallback.scores,
                warnings=["prompt reranker provider not configured; used passthrough ordering"],
            )

        try:
            payload = self._provider(request, runtime, self._pack)
            items, warnings = _parse_ranked_chunk_ids(payload, runtime.items)
        except Exception as exc:
            fallback = PassthroughReranker().rerank(request, runtime, limit=effective_limit)
            return RerankResult(
                items=fallback.items,
                scores=fallback.scores,
                warnings=[f"prompt reranker failed: {type(exc).__name__}: {exc}"],
            )

        if not items:
            fallback = PassthroughReranker().rerank(request, runtime, limit=effective_limit)
            return RerankResult(
                items=fallback.items,
                scores=fallback.scores,
                warnings=warnings + ["prompt reranker returned no valid items; used passthrough ordering"],
            )

        items = items[:effective_limit]
        scores = {item.chunk_id: _item_score(item) for item in items if item.chunk_id}
        return RerankResult(items=items, scores=scores, warnings=warnings)


def build_reranker(backend: str, *, provider: RerankerProvider | None = None) -> Reranker:
    if backend == "passthrough":
        return PassthroughReranker()
    if backend == "score":
        return ScoreReranker()
    if backend == "prompt":
        return PromptPackReranker(provider=provider)
    raise ValueError(f"unsupported reranker backend: {backend}")
