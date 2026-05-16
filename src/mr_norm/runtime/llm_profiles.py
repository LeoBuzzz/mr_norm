from __future__ import annotations

from dataclasses import dataclass

OLLAMA_PLANNER_MODEL = "qwen3:30b"
OLLAMA_RERANKER_MODEL = "qwen3:30b"
OLLAMA_FINAL_ANSWER_MODEL = "llama-3.3-70b-Instruct:latest"
OLLAMA_FAST_MODEL = "qwen3:8b"

POLZA_PLANNER_MODEL = "qwen/qwen3.5-flash-02-23"
POLZA_RERANKER_MODEL = "qwen/qwen3.5-flash-02-23"
POLZA_FINAL_ANSWER_MODEL = "deepseek/deepseek-v4-flash"
POLZA_PREMIUM_FINAL_ANSWER_MODEL = "anthropic/claude-sonnet-4.6"


@dataclass(frozen=True)
class LLMRoleProfile:
    model: str
    temperature: float = 0.1
    max_tokens: int = 2048


@dataclass(frozen=True)
class LLMProviderProfiles:
    planner: LLMRoleProfile
    reranker: LLMRoleProfile
    final_answer: LLMRoleProfile


OLLAMA_PROFILES = LLMProviderProfiles(
    planner=LLMRoleProfile(model=OLLAMA_PLANNER_MODEL, max_tokens=1024),
    reranker=LLMRoleProfile(model=OLLAMA_RERANKER_MODEL, max_tokens=1024),
    final_answer=LLMRoleProfile(model=OLLAMA_FINAL_ANSWER_MODEL, max_tokens=4096),
)

POLZA_PROFILES = LLMProviderProfiles(
    planner=LLMRoleProfile(model=POLZA_PLANNER_MODEL, max_tokens=1024),
    reranker=LLMRoleProfile(model=POLZA_RERANKER_MODEL, max_tokens=1024),
    final_answer=LLMRoleProfile(model=POLZA_FINAL_ANSWER_MODEL, max_tokens=4096),
)


def get_llm_profiles(provider: str) -> LLMProviderProfiles:
    if provider == "ollama":
        return OLLAMA_PROFILES
    if provider == "polza":
        return POLZA_PROFILES
    raise ValueError(f"unsupported LLM provider: {provider}")


def resolve_role_model(provider: str, role: str, explicit_model: str | None = None) -> str:
    if explicit_model and explicit_model.strip():
        return explicit_model.strip()
    profiles = get_llm_profiles(provider)
    if role == "planner":
        return profiles.planner.model
    if role == "reranker":
        return profiles.reranker.model
    if role == "final_answer":
        return profiles.final_answer.model
    raise ValueError(f"unsupported LLM role: {role}")


def resolve_role_profile(provider: str, role: str, explicit_model: str | None = None) -> LLMRoleProfile:
    profiles = get_llm_profiles(provider)
    base = {
        "planner": profiles.planner,
        "reranker": profiles.reranker,
        "final_answer": profiles.final_answer,
    }[role]
    model = resolve_role_model(provider, role, explicit_model)
    return LLMRoleProfile(model=model, temperature=base.temperature, max_tokens=base.max_tokens)
