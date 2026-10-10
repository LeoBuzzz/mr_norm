"""Local request-envelope capture and hashing; deliberately no transport."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_envelope_bytes(envelope: dict[str, Any]) -> bytes:
    if not isinstance(envelope, dict) or set(envelope) != {"role", "attempt", "model", "effort", "messages"}:
        raise ValueError("request envelope must contain role, attempt, model, effort, messages")
    if envelope["role"] not in {"classifier", "simple_parser", "complex_parser", "repair"}:
        raise ValueError("unknown request role")
    if not isinstance(envelope["attempt"], int) or envelope["attempt"] < 1:
        raise ValueError("attempt must be positive")
    if not isinstance(envelope["messages"], list) or not envelope["messages"]:
        raise ValueError("messages must be a nonempty list")
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def capture_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return immutable capture bytes/hash metadata to persist before dispatch."""
    payload = canonical_envelope_bytes(envelope)
    return {"envelope": envelope, "envelope_sha256": hashlib.sha256(payload).hexdigest(),
            "envelope_utf8": payload.decode("utf-8"), "capture_stage": "pre_dispatch"}
