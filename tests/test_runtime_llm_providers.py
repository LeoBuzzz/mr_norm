from __future__ import annotations

import json

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import (
    PreparedQueryPlan,
    RuntimeMetrics,
    RuntimeRequest,
    RuntimeResult,
    RuntimeTrace,
)
from mr_norm.runtime.llm_providers import (
    build_pipeline_llm_providers,
    build_planner_llm_provider,
    build_reranker_llm_provider,
    chat_json_with_model_fallback,
)
from mr_norm.runtime.prompts import load_prompt_pack_by_role


def make_runtime_result() -> RuntimeResult:
    return RuntimeResult(
        items=[
            RetrievedItem(
                chunk_id="chunk_1",
                doc_name="ПУЭ",
                point_number="1.7.1",
                text="Требования к заземлению.",
                source_tool="payload",
            )
        ],
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=1, tools_succeeded=1, items_returned=1),
    )


def test_build_planner_llm_provider_returns_structured_payload() -> None:
    calls: list[str] = []

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        payload = json.loads(body.decode("utf-8"))
        calls.append(payload["model"])
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "selected_tools": ["payload", "vector"],
                                "routing_reasons": ["payload: text lookup"],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    provider = build_planner_llm_provider(
        "ollama",
        ["qwen3:30b"],
        temperature=0.1,
        max_tokens=512,
        http_post=fake_http_post,
    )
    pack = load_prompt_pack_by_role("planner")
    payload = provider(RuntimeRequest(query="заземление", profile="balanced"), make_runtime_result(), pack)

    assert payload["selected_tools"] == ["payload", "vector"]
    assert calls == ["qwen3:30b"]


def test_chat_json_with_model_fallback_uses_second_model() -> None:
    calls: list[str] = []

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        model = json.loads(body.decode("utf-8"))["model"]
        calls.append(model)
        if model == "primary-model":
            raise RuntimeError("primary unavailable")
        return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

    payload = chat_json_with_model_fallback(
        "ollama",
        ["primary-model", "fallback-model"],
        http_post=fake_http_post,
        system_prompt="test",
        user_payload={"query": "заземление"},
        temperature=0.1,
        max_tokens=512,
    )

    assert payload["status"] == "ok"
    assert calls == ["primary-model", "fallback-model"]


def test_polza_failure_falls_back_to_local_ollama(monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "test-polza-key")
    calls: list[tuple[str, str]] = []

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        payload = json.loads(body.decode("utf-8"))
        model = payload["model"]
        provider = "polza" if "polza.ai" in url else "ollama"
        calls.append((provider, model))
        if provider == "polza":
            raise RuntimeError("connection reset")
        return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

    payload = chat_json_with_model_fallback(
        "polza",
        ["deepseek/deepseek-v3.2"],
        http_post=fake_http_post,
        system_prompt="test",
        user_payload={"query": "test"},
        temperature=0.1,
        max_tokens=64,
    )

    assert payload["status"] == "ok"
    assert calls == [("polza", "deepseek/deepseek-v3.2"), ("ollama", "qwen3:30b")]


def test_build_reranker_llm_provider_includes_plan_context() -> None:
    captured: list[dict] = []

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        payload = json.loads(body.decode("utf-8"))
        user_content = json.loads(payload["messages"][1]["content"])
        captured.append(user_content)
        return {
            "choices": [
                {"message": {"content": json.dumps({"ranked_chunk_ids": ["chunk_1"]})}}
            ]
        }

    provider = build_reranker_llm_provider(
        "ollama",
        ["qwen3:30b"],
        temperature=0.0,
        max_tokens=256,
        http_post=fake_http_post,
    )
    request = RuntimeRequest(
        query="заземление",
        filters={"doc_id": "doc_abc"},
        profile="deep",
        prepared_plan=PreparedQueryPlan(
            question_type="requirement",
            exact_phrase_terms=("заземление",),
        ),
    )
    pack = load_prompt_pack_by_role("reranker")
    payload = provider(request, make_runtime_result(), pack)

    assert payload["ranked_chunk_ids"] == ["chunk_1"]
    assert captured[0]["question_type"] == "requirement"
    assert captured[0]["resolved_doc_id"] == "doc_abc"
    assert captured[0]["exact_phrase_terms"] == ["заземление"]


def test_build_pipeline_llm_providers_only_for_prompt_backends() -> None:
    providers = build_pipeline_llm_providers(
        "ollama",
        planner_backend="deterministic",
        reranker_backend="prompt",
        final_answer_backend="evidence",
        http_post=lambda *args, **kwargs: {"choices": [{"message": {"content": "{}"}}]},
    )

    assert providers.planner is None
    assert providers.reranker is not None
    assert providers.final_answer is None


def test_build_pipeline_llm_providers_none_returns_empty() -> None:
    providers = build_pipeline_llm_providers("none", planner_backend="prompt")

    assert providers.planner is None
    assert providers.reranker is None
    assert providers.final_answer is None
