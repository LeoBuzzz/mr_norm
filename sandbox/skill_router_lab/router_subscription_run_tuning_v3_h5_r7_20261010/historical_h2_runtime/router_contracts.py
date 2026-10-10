"""Serializable contracts for the sandbox question parser (stage 1)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouterPlanConfig:
    light_model: str
    heavy_model: str
    provider: str
    max_tasks: int = 6
    max_query_chars: int = 8000
    max_context_chars: int = 16000
    timeout_sec: float = 60.0
    classifier_max_tokens: int = 256
    parser_max_tokens: int = 4096
    prompt_version: str = "skill-router-prompts.v1"


@dataclass
class SkillQueryPlan:
    schema_version: str
    original_query: str
    status: str
    complexity: str | None
    dialog_action: str = "new"
    tasks: list[dict[str, Any]] = field(default_factory=list)
    clarification: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

