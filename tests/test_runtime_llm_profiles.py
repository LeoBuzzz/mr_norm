from __future__ import annotations

from mr_norm.runtime.llm_profiles import (
    OLLAMA_FINAL_ANSWER_MODEL,
    POLZA_FINAL_ANSWER_MODEL,
    resolve_role_model,
    resolve_role_profile,
)


def test_resolve_role_model_uses_provider_defaults() -> None:
    assert resolve_role_model("ollama", "planner") == "qwen3:30b"
    assert resolve_role_model("polza", "final_answer") == POLZA_FINAL_ANSWER_MODEL


def test_resolve_role_model_prefers_explicit_override() -> None:
    assert resolve_role_model("ollama", "final_answer", "custom-model") == "custom-model"


def test_resolve_role_profile_keeps_provider_temperature_and_max_tokens() -> None:
    profile = resolve_role_profile("ollama", "final_answer")

    assert profile.model == OLLAMA_FINAL_ANSWER_MODEL
    assert profile.max_tokens == 4096
