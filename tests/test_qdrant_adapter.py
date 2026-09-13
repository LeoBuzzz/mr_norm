from __future__ import annotations

from mr_norm.retrieval.qdrant_adapter import _is_transient_qdrant_error


def test_qdrant_adapter_recognizes_winerror_10054() -> None:
    assert _is_transient_qdrant_error(RuntimeError("[WinError 10054] connection aborted"))


def test_qdrant_adapter_recognizes_gateway_errors() -> None:
    assert _is_transient_qdrant_error(RuntimeError("Unexpected Response: 502 (Bad Gateway)"))
