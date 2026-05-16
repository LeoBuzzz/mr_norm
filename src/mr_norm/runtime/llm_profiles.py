from __future__ import annotations

from dataclasses import dataclass

# Ollama: local-first defaults
OLLAMA_PLANNER_MODEL = "qwen3:30b"
OLLAMA_PLANNER_FALLBACK_MODEL = "qwen3:8b"
OLLAMA_RERANKER_MODEL = "qwen3:30b"
OLLAMA_RERANKER_FALLBACK_MODEL = "qwen3:8b"
OLLAMA_FINAL_ANSWER_MODEL = "llama-3.3-70b-Instruct:latest"
OLLAMA_FINAL_ANSWER_FALLBACK_MODEL = "qwen3:30b"

# Polza: cloud defaults
POLZA_PLANNER_MODEL = "qwen/qwen3.5-flash-02-23"
POLZA_PLANNER_FALLBACK_MODEL = "qwen/qwen3.6-flash"
POLZA_RERANKER_MODEL = "qwen/qwen3.5-flash-02-23"
POLZA_RERANKER_FALLBACK_MODEL = "google/gemini-3.1-flash-lite"
POLZA_FINAL_ANSWER_MODEL = "deepseek/deepseek-v4-flash"
POLZA_FINAL_ANSWER_FALLBACK_MODEL = "qwen/qwen3.5-flash-02-23"
POLZA_PREMIUM_FINAL_ANSWER_MODEL = "anthropic/claude-sonnet-4.6"


@dataclass(frozen=True)
class LLMRoleProfile:
    model: str
    fallback_model: str = ""
    temperature: float = 0.1
    max_tokens: int = 2048


@dataclass(frozen=True)
class LLMProviderProfiles:
    planner: LLMRoleProfile
    reranker: LLMRoleProfile
    final_answer: LLMRoleProfile


OLLAMA_PROFILES = LLMProviderProfiles(
    planner=LLMRoleProfile(
        model=OLLAMA_PLANNER_MODEL,
        fallback_model=OLLAMA_PLANNER_FALLBACK_MODEL,
        max_tokens=1024,
    ),
    reranker=LLMRoleProfile(
        model=OLLAMA_RERANKER_MODEL,
        fallback_model=OLLAMA_RERANKER_FALLBACK_MODEL,
        max_tokens=1024,
    ),
    final_answer=LLMRoleProfile(
        model=OLLAMA_FINAL_ANSWER_MODEL,
        fallback_model=OLLAMA_FINAL_ANSWER_FALLBACK_MODEL,
        max_tokens=4096,
    ),
)

POLZA_PROFILES = LLMProviderProfiles(
    planner=LLMRoleProfile(
        model=POLZA_PLANNER_MODEL,
        fallback_model=POLZA_PLANNER_FALLBACK_MODEL,
        max_tokens=1024,
    ),
    reranker=LLMRoleProfile(
        model=POLZA_RERANKER_MODEL,
        fallback_model=POLZA_RERANKER_FALLBACK_MODEL,
        max_tokens=1024,
    ),
    final_answer=LLMRoleProfile(
        model=POLZA_FINAL_ANSWER_MODEL,
        fallback_model=POLZA_FINAL_ANSWER_FALLBACK_MODEL,
        max_tokens=4096,
    ),
)


def get_llm_profiles(provider: str) -> LLMProviderProfiles:
    if provider == "ollama":
        return OLLAMA_PROFILES
    if provider == "polza":
        return POLZA_PROFILES
    raise ValueError(f"unsupported LLM provider: {provider}")


def get_role_profile(provider: str, role: str) -> LLMRoleProfile:
    profiles = get_llm_profiles(provider)
    if role == "planner":
        return profiles.planner
    if role == "reranker":
        return profiles.reranker
    if role == "final_answer":
        return profiles.final_answer
    raise ValueError(f"unsupported LLM role: {role}")


def resolve_role_models(provider: str, role: str, explicit_model: str | None = None) -> list[str]:
    if explicit_model and explicit_model.strip():
        return [explicit_model.strip()]

    profile = get_role_profile(provider, role)
    models = [profile.model]
    fallback = profile.fallback_model.strip()
    if fallback and fallback not in models:
        models.append(fallback)
    return models


def resolve_role_model(provider: str, role: str, explicit_model: str | None = None) -> str:
    return resolve_role_models(provider, role, explicit_model)[0]


def resolve_role_profile(provider: str, role: str, explicit_model: str | None = None) -> LLMRoleProfile:
    base = get_role_profile(provider, role)
    model = resolve_role_model(provider, role, explicit_model)
    return LLMRoleProfile(
        model=model,
        fallback_model=base.fallback_model,
        temperature=base.temperature,
        max_tokens=base.max_tokens,
    )


def format_role_model_chain(provider: str, role: str) -> str:
    models = resolve_role_models(provider, role)
    if len(models) == 1:
        return models[0]
    return " -> ".join(models)
