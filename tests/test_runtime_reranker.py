from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace
from mr_norm.runtime.pipeline_features import default_reranker_backend
from mr_norm.runtime.reranker import PassthroughReranker, PromptPackReranker, ScoreReranker, build_reranker


def make_runtime_result(items: list[RetrievedItem]) -> RuntimeResult:
    return RuntimeResult(
        items=items,
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced"),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=0, tools_succeeded=0, items_returned=len(items)),
    )


def test_passthrough_reranker_preserves_runtime_order() -> None:
    items = [
        RetrievedItem(chunk_id="chunk_a", score=0.2, source_tool="vector"),
        RetrievedItem(chunk_id="chunk_b", score=0.9, source_tool="payload"),
    ]
    request = RuntimeRequest(query="заземление", limit=2)
    result = PassthroughReranker().rerank(request, make_runtime_result(items))

    assert [item.chunk_id for item in result.items] == ["chunk_a", "chunk_b"]


def test_score_reranker_sorts_by_score_desc() -> None:
    items = [
        RetrievedItem(chunk_id="chunk_a", score=0.2, source_tool="vector"),
        RetrievedItem(chunk_id="chunk_b", score=0.9, source_tool="payload"),
        RetrievedItem(chunk_id="chunk_c", score=0.9, source_tool="point"),
    ]
    request = RuntimeRequest(query="заземление", limit=3)
    result = ScoreReranker().rerank(request, make_runtime_result(items))

    assert [item.chunk_id for item in result.items] == ["chunk_c", "chunk_b", "chunk_a"]


def test_score_reranker_preserves_structural_header_and_body_before_high_score_noise() -> None:
    header = RetrievedItem(
        chunk_id="table_header",
        score=0.2,
        source_tool="payload",
        chunk_index=61,
        matched={"structural_target": "таблица 2"},
    )
    body = RetrievedItem(
        chunk_id="table_body",
        score=0.1,
        source_tool="payload",
        chunk_index=62,
        matched={
            "structural_target": "таблица 2",
            "structural_parent_chunk_id": "table_header",
        },
    )
    noise = [
        RetrievedItem(chunk_id=f"point_{number}", score=0.99, source_tool="payload")
        for number in (21, 11, 4, 10)
    ]
    request = RuntimeRequest(query="Дай содержание таблицы 2 приложения", limit=4)

    result = ScoreReranker().rerank(request, make_runtime_result([*noise, body, header]))

    assert [item.chunk_id for item in result.items[:2]] == ["table_header", "table_body"]
    assert len(result.items) == 4


def test_prompt_reranker_promotes_structural_group_after_provider_order() -> None:
    header = RetrievedItem(
        chunk_id="table_header",
        score=0.2,
        source_tool="payload",
        chunk_index=61,
        matched={"structural_target": "таблица 2"},
    )
    body = RetrievedItem(
        chunk_id="table_body",
        score=0.1,
        source_tool="payload",
        chunk_index=62,
        matched={
            "structural_target": "таблица 2",
            "structural_parent_chunk_id": "table_header",
        },
    )
    noise = RetrievedItem(chunk_id="point_21", score=0.99, source_tool="payload")
    request = RuntimeRequest(query="Дай содержание таблицы 2 приложения", limit=2)
    runtime = make_runtime_result([noise, body, header])

    def provider(_request, _runtime, _pack):
        return {"ranked_chunk_ids": ["point_21", "table_body", "table_header"]}

    result = PromptPackReranker(provider=provider).rerank(request, runtime)

    assert [item.chunk_id for item in result.items] == ["table_header", "table_body"]


def test_prompt_pack_reranker_uses_provider_order() -> None:
    items = [
        RetrievedItem(chunk_id="chunk_a", score=0.2, source_tool="vector"),
        RetrievedItem(chunk_id="chunk_b", score=0.9, source_tool="payload"),
    ]
    request = RuntimeRequest(query="заземление", limit=2)
    runtime = make_runtime_result(items)

    def provider(_request, _runtime, _pack):
        return {"ranked_chunk_ids": ["chunk_b", "chunk_a", "missing"]}

    result = PromptPackReranker(provider=provider).rerank(request, runtime)

    assert [item.chunk_id for item in result.items] == ["chunk_b", "chunk_a"]
    assert any("unknown chunk_id" in warning for warning in result.warnings)


def test_prompt_pack_reranker_falls_back_without_provider() -> None:
    items = [RetrievedItem(chunk_id="chunk_a", source_tool="payload")]
    request = RuntimeRequest(query="заземление", limit=1)
    result = PromptPackReranker().rerank(request, make_runtime_result(items))

    assert result.items[0].chunk_id == "chunk_a"
    assert any("provider not configured" in warning for warning in result.warnings)


def test_build_reranker_rejects_unknown_backend() -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported reranker backend"):
        build_reranker("unknown")


def test_default_reranker_backend_by_profile() -> None:
    assert default_reranker_backend("deep") == "prompt"
    assert default_reranker_backend("balanced") == "score"
    assert default_reranker_backend("fast") == "passthrough"
