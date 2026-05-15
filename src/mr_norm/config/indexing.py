from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_COLLECTION_NAME = "mr_norm_docs_bge_m3"


@dataclass(frozen=True)
class IndexingConfig:
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_timeout_sec: float = 120.0
    collection_name: str = DEFAULT_COLLECTION_NAME
    vector_name: str = "bge-m3"
    embedding_model_name: str = "deepvk/USER-bge-m3"
    batch_size: int = 32
    upsert_batch_size: int = 512
    normalize: bool = True
    use_query_passage_prefix: bool = False
    embedding_device: str = ""
    vector_size: int | None = None

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @classmethod
    def from_env(cls) -> "IndexingConfig":
        vector_size_raw = os.environ.get("MR_NORM_VECTOR_SIZE", "").strip()
        return cls(
            qdrant_host=os.environ.get("QDRANT_HOST", "localhost").strip() or "localhost",
            qdrant_port=int(os.environ.get("QDRANT_PORT", "6333")),
            qdrant_timeout_sec=float(os.environ.get("MR_NORM_QDRANT_TIMEOUT_SEC", "120")),
            collection_name=os.environ.get("MR_NORM_QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME).strip()
            or DEFAULT_COLLECTION_NAME,
            vector_name=os.environ.get("QDRANT_VECTOR_NAME", "bge-m3").strip() or "bge-m3",
            embedding_model_name=os.environ.get("EMBEDDING_MODEL_NAME", "deepvk/USER-bge-m3").strip()
            or "deepvk/USER-bge-m3",
            batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE", "32")),
            upsert_batch_size=int(os.environ.get("MR_NORM_UPSERT_BATCH_SIZE", "512")),
            normalize=os.environ.get("EMBEDDING_NORMALIZE", "1") != "0",
            use_query_passage_prefix=os.environ.get("EMBEDDING_USE_QUERY_PASSAGE_PREFIX", "0") == "1",
            embedding_device=os.environ.get("RAG_EMBEDDING_DEVICE", "").strip(),
            vector_size=int(vector_size_raw) if vector_size_raw else None,
        )
