from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_POLZA_BASE_URL = "https://polza.ai/api/v1"
DEFAULT_KEYS_PATH = Path("keys")

HttpPost = Callable[[str, dict[str, str], bytes, float], dict[str, Any]]


class LLMConfigError(ValueError):
    """Raised when LLM provider configuration or credentials are missing."""


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.1
    max_tokens: int = 2048
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


def default_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    return json.loads(payload)


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ValueError("LLM response content is empty")

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload


def load_polza_api_key(*, keys_path: Path | None = None) -> str:
    env_key = os.environ.get("POLZA_AI_API_KEY", "").strip()
    if env_key:
        return env_key

    path = keys_path or DEFAULT_KEYS_PATH
    if not path.is_file():
        raise LLMConfigError(
            "Polza API key not found: set POLZA_AI_API_KEY or create a local keys file"
        )

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        label, _, value = stripped.partition(":")
        if "polza" in label.lower():
            key = value.strip()
            if key:
                return key

    raise LLMConfigError("Polza API key not found in keys file")


class OpenAICompatibleChatClient:
    provider_name: str

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_sec: float = 120.0,
        http_post: HttpPost | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_sec = timeout_sec
        self._http_post = http_post or default_http_post

    @property
    def model(self) -> str:
        return self._model

    def chat(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_format is not None:
            payload["response_format"] = request.response_format

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        raw = self._http_post(
            f"{self._base_url}/chat/completions",
            headers,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            self._timeout_sec,
        )
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response missing choices")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError("LLM response missing message content")
        return LLMResponse(content=content, model=str(raw.get("model") or payload["model"]), raw=raw)


class OllamaChatClient(OpenAICompatibleChatClient):
    provider_name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout_sec: float = 120.0,
        http_post: HttpPost | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=None,
            timeout_sec=timeout_sec,
            http_post=http_post,
        )


class PolzaChatClient(OpenAICompatibleChatClient):
    provider_name = "polza"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = DEFAULT_POLZA_BASE_URL,
        keys_path: Path | None = None,
        timeout_sec: float = 120.0,
        http_post: HttpPost | None = None,
    ) -> None:
        resolved_key = api_key or load_polza_api_key(keys_path=keys_path)
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=resolved_key,
            timeout_sec=timeout_sec,
            http_post=http_post,
        )


def build_chat_client(
    provider: str,
    model: str,
    *,
    keys_path: Path | None = None,
    base_url: str | None = None,
    timeout_sec: float = 120.0,
    http_post: HttpPost | None = None,
) -> OpenAICompatibleChatClient:
    if provider == "ollama":
        return OllamaChatClient(
            model=model,
            base_url=base_url or DEFAULT_OLLAMA_BASE_URL,
            timeout_sec=timeout_sec,
            http_post=http_post,
        )
    if provider == "polza":
        return PolzaChatClient(
            model=model,
            base_url=base_url or DEFAULT_POLZA_BASE_URL,
            keys_path=keys_path,
            timeout_sec=timeout_sec,
            http_post=http_post,
        )
    raise LLMConfigError(f"unsupported LLM provider: {provider}")
