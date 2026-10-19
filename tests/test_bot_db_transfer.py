from __future__ import annotations

import sys
from pathlib import Path

from qdrant_client import QdrantClient, models


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bot_db_transfer as transfer  # noqa: E402


def test_transfer_roundtrip(tmp_path: Path, monkeypatch) -> None:
    original = QdrantClient(":memory:")
    original.create_collection(
        collection_name="bot_test",
        vectors_config={"bge-m3": models.VectorParams(size=2, distance=models.Distance.COSINE)},
    )
    original.upsert(
        collection_name="bot_test",
        points=[models.PointStruct(id=1, vector={"bge-m3": [0.1, 0.2]}, payload={"doc_id": "sample"})],
        wait=True,
    )
    monkeypatch.setattr(transfer, "client", lambda: original)
    monkeypatch.setattr(transfer, "transfer_artifacts", lambda *args, **kwargs: None)
    transfer.export_collection(tmp_path / "export", "bot_test")

    restored = QdrantClient(":memory:")
    monkeypatch.setattr(transfer, "client", lambda: restored)
    transfer.import_collection(tmp_path / "export", rebuild=False)

    assert restored.count(collection_name="bot_test", exact=True).count == 1
    point = restored.retrieve(collection_name="bot_test", ids=[1], with_vectors=True)[0]
    assert point.payload == {"doc_id": "sample"}
    assert "bge-m3" in point.vector
