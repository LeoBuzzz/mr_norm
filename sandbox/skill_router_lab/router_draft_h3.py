"""H3 versioned validator policy layered over the immutable v2 assembler."""
from __future__ import annotations

import re
from typing import Any

from router_draft import *  # noqa: F401,F403 - preserve the frozen v2 contract and assembler
from router_draft import DraftValidationError, validate_semantic_draft as _validate_v2

TOPIC_LOCATOR_RE = re.compile(r"(?<![\w@])[^\s@]+@\d+:\d+(?!\d)")
VALIDATION_POLICY = "topic_literal_guard.v1"


def validate_semantic_draft(draft: Any, manifest: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Reject SourceRef-shaped topic text before normal validation/assembly."""
    if isinstance(draft, dict) and isinstance(draft.get("tasks"), list):
        for index, task in enumerate(draft["tasks"], start=1):
            if isinstance(task, dict):
                topic = task.get("topic")
                if isinstance(topic, str) and TOPIC_LOCATOR_RE.search(topic):
                    raise DraftValidationError(
                        f"TOPIC_MUST_BE_LITERAL_TEXT task {index}: topic must be ordinary literal text"
                    )
    return _validate_v2(draft, manifest, **kwargs)
