"""Read-only canonical corpus loader for the isolated router lab."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def chunk_order(chunk: dict[str, Any]) -> tuple:
    payload = chunk.get("payload", {})

    def number(field: str) -> int:
        try:
            return int(payload.get(field, -1))
        except (TypeError, ValueError):
            return -1

    return (number("chunk_index"), number("part_index"), number("chunk_start"), number("char_start"), chunk.get("chunk_id", ""))


@dataclass
class CanonicalCorpus:
    root: Path
    registry_by_key: dict[str, dict[str, Any]]
    catalog_by_key: dict[str, dict[str, Any]]
    docs: dict[str, list[dict[str, Any]]]
    qdrant_verified_doc_ids: frozenset[str] = frozenset()

    @classmethod
    def load(cls, root: Path) -> "CanonicalCorpus":
        root = root.resolve()
        registry_data = _json(root / "src/mr_norm/data/normative_documents_registry.json")
        registry = registry_data if isinstance(registry_data, list) else registry_data.get("documents", registry_data.get("entries"))
        if not isinstance(registry, list):
            raise ValueError("registry must be a list or contain documents/entries list")
        catalog_data = _json(root / "output/document_catalog.json")
        catalog = catalog_data.get("entries")
        if not isinstance(catalog, list):
            raise ValueError("document catalog entries missing")
        registry_by_key = {str(row["registry_key"]): row for row in registry}
        catalog_by_key = {str(row["registry_key"]): row for row in catalog}
        if len(registry_by_key) != len(registry) or len(catalog_by_key) != len(catalog) or set(registry_by_key) != set(catalog_by_key):
            raise ValueError("registry/catalog registry_key join is not bijective")
        doc_to_key: dict[str, str] = {}
        for key, entry in catalog_by_key.items():
            doc_id = str(entry.get("doc_id") or "")
            if not doc_id or doc_id in doc_to_key:
                raise ValueError(f"catalog doc_id missing or duplicate: {key}")
            doc_to_key[doc_id] = key
        raw_chunks = _json(root / "output/qdrant_chunks.json")
        if not isinstance(raw_chunks, list):
            raise ValueError("canonical chunks must be a list")
        chunks_by_doc: dict[str, list[dict[str, Any]]] = {doc_id: [] for doc_id in doc_to_key}
        chunk_ids: set[str] = set()
        for chunk in raw_chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            doc_id = str(chunk.get("payload", {}).get("doc_id") or "")
            if not chunk_id or chunk_id in chunk_ids or not str(chunk.get("text") or "").strip():
                raise ValueError("canonical chunk has missing/duplicate id or empty text")
            if doc_id not in chunks_by_doc:
                raise ValueError(f"chunk references unknown doc_id {doc_id}")
            chunk_ids.add(chunk_id)
            chunks_by_doc[doc_id].append(chunk)
        for doc_id, chunks in chunks_by_doc.items():
            chunks_by_doc[doc_id] = sorted(chunks, key=chunk_order)
        return cls(root, registry_by_key, catalog_by_key, chunks_by_doc)

    def entry_for_doc(self, doc_id: str) -> dict[str, Any]:
        key = next((key for key, entry in self.catalog_by_key.items() if entry["doc_id"] == doc_id), None)
        if key is None:
            raise KeyError(doc_id)
        return self.catalog_by_key[key]

    def resolve_document(self, hint: str) -> dict[str, Any]:
        """Resolve exact ID/key or a unique registry/catalog alias; ambiguity fails closed."""
        query = _norm(hint)
        if not query:
            raise LookupError("document hint is empty")
        exact = []
        for key, entry in self.catalog_by_key.items():
            aliases = [key, entry.get("doc_id", ""), entry.get("doc_name", ""), *entry.get("aliases", [])]
            if any(_norm(alias) == query for alias in aliases if alias):
                exact.append(entry)
        candidates = exact
        if not candidates:
            candidates = []
            for key, entry in self.catalog_by_key.items():
                aliases = [key, entry.get("doc_name", ""), *entry.get("aliases", [])]
                if any(_norm(alias) and _norm(alias) in query for alias in aliases if alias):
                    candidates.append(entry)
        if not candidates:
            # Permit an exact order number only with a word boundary. Never let 86 match 861.
            import re
            numbers = set(re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", query))
            candidates = [entry for entry in self.catalog_by_key.values()
                          if numbers.intersection(str(n) for n in entry.get("order_numbers", []))]
        distinct = {entry["doc_id"]: entry for entry in candidates}
        if len(distinct) != 1:
            if distinct:
                keys = sorted(self.entry_for_doc(doc_id)["registry_key"] for doc_id in distinct)
                raise LookupError("ambiguous document: " + ", ".join(keys))
            raise LookupError("document not found")
        return next(iter(distinct.values()))


def load_verified_qdrant_subset(
    root: Path,
    doc_ids: list[str] | tuple[str, ...],
    *,
    config: Any = None,
) -> CanonicalCorpus:
    """Read selected complete documents from Qdrant, rejecting any drift from the frozen source."""
    from mr_norm.config.indexing import IndexingConfig
    from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient, filter_spec_to_qdrant_filter

    selected = tuple(dict.fromkeys(doc_ids))
    if not selected:
        raise ValueError("select at least one document for the Qdrant subset")
    reference = CanonicalCorpus.load(root)
    unknown = set(selected) - set(reference.docs)
    if unknown:
        raise ValueError(f"unknown canonical doc_id: {sorted(unknown)}")

    indexing = config or IndexingConfig.from_env()
    client = QdrantRetrievalClient(indexing).client
    live_docs: dict[str, list[dict[str, Any]]] = {}
    for doc_id in selected:
        expected = {chunk["chunk_id"]: chunk for chunk in reference.docs[doc_id]}
        query_filter = filter_spec_to_qdrant_filter({"must": [{"field": "doc_id", "value": doc_id}]})
        actual: dict[str, dict[str, Any]] = {}
        offset = None
        seen_offsets: set[str] = set()
        while True:
            points, offset = client.scroll(
                collection_name=indexing.collection_name,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = dict(point.payload or {})
                chunk_id = str(payload.pop("chunk_id", ""))
                if not chunk_id or chunk_id in actual:
                    raise ValueError(f"Qdrant has missing or duplicate chunk_id in {doc_id}: {chunk_id}")
                chunk = {
                    "schema_version": payload.pop("schema_version", None),
                    "chunk_id": chunk_id,
                    "text": payload.pop("text", None),
                    "payload": payload,
                }
                if chunk != expected.get(chunk_id):
                    raise ValueError(f"Qdrant chunk differs from canonical source: {doc_id}/{chunk_id}")
                actual[chunk_id] = chunk
            if offset is None:
                break
            offset_key = str(offset)
            if offset_key in seen_offsets:
                raise ValueError(f"Qdrant scroll offset repeated for {doc_id}")
            seen_offsets.add(offset_key)
        missing = set(expected) - set(actual)
        if missing:
            raise ValueError(f"Qdrant document is incomplete: {doc_id}, missing {len(missing)} chunks")
        live_docs[doc_id] = sorted(actual.values(), key=chunk_order)

    selected_keys = {reference.entry_for_doc(doc_id)["registry_key"] for doc_id in selected}
    return CanonicalCorpus(
        root=reference.root,
        registry_by_key={key: value for key, value in reference.registry_by_key.items() if key in selected_keys},
        catalog_by_key={key: value for key, value in reference.catalog_by_key.items() if key in selected_keys},
        docs=live_docs,
        qdrant_verified_doc_ids=frozenset(selected),
    )


def _norm(value: Any) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKC", str(value or "")).lower().replace("ё", "е")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())
