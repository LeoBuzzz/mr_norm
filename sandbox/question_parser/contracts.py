"""Public plan contract. Status separates a user clarification from a technical failure."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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


@dataclass
class TechnicalFailure:
    """A model or contract error. This is not a question to the user."""

    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"status": "technical_error", "code": self.code, "message": self.message}
