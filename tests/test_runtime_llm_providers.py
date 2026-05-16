from __future__ import annotations

import json

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace
from mr_norm.runtime.llm_providers import (
    build_pipeline_llm_providers,
    build_planner_llm_provider,
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
