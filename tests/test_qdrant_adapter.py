from __future__ import annotations

from types import SimpleNamespace

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient, _is_transient_qdrant_error


def test_qdrant_adapter_recognizes_winerror_10054() -> None:
    assert _is_transient_qdrant_error(RuntimeError("[WinError 10054] connection aborted"))


def test_qdrant_adapter_recognizes_gateway_errors() -> None:
    assert _is_transient_qdrant_error(RuntimeError("Unexpected Response: 502 (Bad Gateway)"))


def test_payload_search_recreates_client_after_reset(monkeypatch) -> None:
    class FlakyClient:
        instances = 0

        def __init__(self, **_kwargs):
            FlakyClient.instances += 1
            self.instance = FlakyClient.instances

        def close(self):
            return None

        def scroll(self, **_kwargs):
            if self.instance == 1:
                raise RuntimeError("[WinError 10054] connection reset")
            return ([SimpleNamespace(payload={"doc_id": "doc-1", "text": "ok"})], None)

    client = QdrantRetrievalClient.__new__(QdrantRetrievalClient)
    client.config = IndexingConfig()
    client._qdrant_client_type = FlakyClient
    client.client = FlakyClient()
    client.calls = 0
    monkeypatch.setattr("mr_norm.retrieval.qdrant_adapter.time.sleep", lambda _seconds: None)

    items = client.payload_search({"must": []}, limit=1, source_tool="point")

    assert [item.doc_id for item in items] == ["doc-1"]
    assert FlakyClient.instances == 2
