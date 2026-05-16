from __future__ import annotations

from mr_norm.runtime.llm_profiles import (
    OLLAMA_FINAL_ANSWER_FALLBACK_MODEL,
    OLLAMA_FINAL_ANSWER_MODEL,
    OLLAMA_PLANNER_FALLBACK_MODEL,
    OLLAMA_PLANNER_MODEL,
    POLZA_FINAL_ANSWER_MODEL,
    POLZA_PLANNER_FALLBACK_MODEL,
    POLZA_PLANNER_MODEL,
    format_role_model_chain,
    resolve_role_model,
    resolve_role_models,
    resolve_role_profile,
)


def test_resolve_role_models_uses_primary_and_fallback() -> None:
    assert resolve_role_models("ollama", "planner") == [OLLAMA_PLANNER_MODEL, OLLAMA_PLANNER_FALLBACK_MODEL]
    assert resolve_role_models("polza", "final_answer") == [
        POLZA_FINAL_ANSWER_MODEL,
        "qwen/qwen3.5-flash-02-23",
    ]


def test_resolve_role_models_explicit_override_disables_fallback() -> None:
    assert resolve_role_models("ollama", "planner", "custom-model") == ["custom-model"]


def test_resolve_role_model_returns_primary_only() -> None:
    assert resolve_role_model("ollama", "planner") == OLLAMA_PLANNER_MODEL
    assert resolve_role_model("polza", "final_answer") == POLZA_FINAL_ANSWER_MODEL


def test_resolve_role_profile_keeps_provider_temperature_and_max_tokens() -> None:
    profile = resolve_role_profile("ollama", "final_answer")

    assert profile.model == OLLAMA_FINAL_ANSWER_MODEL
    assert profile.fallback_model == OLLAMA_FINAL_ANSWER_FALLBACK_MODEL
    assert profile.max_tokens == 4096


def test_format_role_model_chain() -> None:
    assert format_role_model_chain("ollama", "planner") == f"{OLLAMA_PLANNER_MODEL} -> {OLLAMA_PLANNER_FALLBACK_MODEL}"
    assert resolve_role_models("polza", "planner", "only-one") == ["only-one"]


def test_polza_planner_fallback_differs_from_primary() -> None:
    models = resolve_role_models("polza", "planner")
    assert models[0] == POLZA_PLANNER_MODEL
    assert models[1] == POLZA_PLANNER_FALLBACK_MODEL
