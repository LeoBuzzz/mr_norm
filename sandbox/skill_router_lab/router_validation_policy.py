"""Explicit validator selection for versioned offline/future subscription runs."""
from __future__ import annotations

from typing import Any, Callable

from router_draft import validate_semantic_draft as validate_h2
from router_draft_h3 import VALIDATION_POLICY, validate_semantic_draft as validate_h3


def validator_for_policy(policy: str) -> Callable[..., dict[str, Any]]:
    if policy == "legacy.v2":
        return validate_h2
    if policy == VALIDATION_POLICY:
        return validate_h3
    raise ValueError(f"unsupported validation policy: {policy!r}")
