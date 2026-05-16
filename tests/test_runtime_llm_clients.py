from __future__ import annotations

import json
from pathlib import Path

import pytest

from mr_norm.runtime.llm_clients import (
    LLMConfigError,
    LLMRequest,
    OllamaChatClient,
    PolzaChatClient,
    build_chat_client,
    load_polza_api_key,
    parse_json_object,
)


def test_parse_json_object_strips_markdown_fence() -> None:
    payload = parse_json_object(
        """```json
        {"selected_tools": ["payload"], "routing_reasons": ["payload: text lookup"]}
        ```"""
    )

    assert payload["selected_tools"] == ["payload"]


def test_load_polza_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "test-polza-key")
    assert load_polza_api_key() == "test-polza-key"


def test_load_polza_api_key_from_keys_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("POLZA_AI_API_KEY", raising=False)
    keys_path = tmp_path / "keys"
    keys_path.write_text("Polza key: file-key-value\n", encoding="utf-8")
    assert load_polza_api_key(keys_path=keys_path) == "file-key-value"


def test_ollama_chat_client_builds_openai_payload() -> None:
    seen: dict[str, object] = {}

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(body.decode("utf-8"))
        seen["timeout_sec"] = timeout_sec
        return {
            "model": "qwen3:30b",
            "choices": [{"message": {"content": '{"answer":"ok","citations":[]}'}}],
        }

    client = OllamaChatClient(model="qwen3:30b", http_post=fake_http_post)
    response = client.chat(
        LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="qwen3:30b",
            response_format={"type": "json_object"},
        )
    )

    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in seen["headers"]
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert response.content.startswith('{"answer"')


def test_polza_chat_client_sends_bearer_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "secret-key")
    seen: dict[str, object] = {}

    def fake_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
        seen["url"] = url
        seen["headers"] = headers
        return {"choices": [{"message": {"content": '{"ranked_chunk_ids":["chunk_1"]}'}}]}

    client = PolzaChatClient(model="deepseek/deepseek-v4-flash", http_post=fake_http_post)
    client.chat(LLMRequest(messages=[{"role": "user", "content": "test"}], model="deepseek/deepseek-v4-flash"))

    assert seen["url"] == "https://polza.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret-key"


def test_build_chat_client_rejects_unknown_provider() -> None:
    with pytest.raises(LLMConfigError, match="unsupported LLM provider"):
        build_chat_client("unknown", "model")
