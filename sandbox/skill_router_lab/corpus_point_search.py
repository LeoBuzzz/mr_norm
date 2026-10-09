"""Global or document-scoped hybrid search returning verified numbered points.

Sandbox-only pilot. The canonical corpus and live Qdrant collection are read-only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from corpus import CanonicalCorpus, chunk_order
from hybrid_retrieval import rerank_local, _tokens

TOP_K = 30
OUTPUT_POINTS = 10
PAGE_SIZE = 256


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class BM25Index:
    """Precomputed exact-token Okapi BM25 statistics for one scope."""
    chunks: list[dict[str, Any]]
    lengths: dict[str, int]
    frequencies: dict[str, Counter[str]]
    document_frequency: Counter[str]
    average_length: float

    @classmethod
    def build(cls, chunks: list[dict[str, Any]]) -> "BM25Index":
        lengths: dict[str, int] = {}
        frequencies: dict[str, Counter[str]] = {}
        df: Counter[str] = Counter()
        for chunk in chunks:
            cid = str(chunk["chunk_id"])
            terms = Counter(_tokens(str(chunk.get("text") or "")))
            frequencies[cid] = terms
            lengths[cid] = sum(terms.values())
            df.update(terms.keys())
        avg = sum(lengths.values()) / len(lengths) if lengths else 0.0
        return cls(chunks, lengths, frequencies, df, avg)

    def search(self, query: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
        terms = _tokens(query)
        if not terms or not self.average_length:
            return []
        n = len(self.chunks)
        scored = []
        for chunk in self.chunks:
            cid = str(chunk["chunk_id"])
            tfmap = self.frequencies[cid]
            length = self.lengths[cid]
            score = 0.0
            for term in terms:
                tf = tfmap.get(term, 0)
                if tf:
                    df = self.document_frequency[term]
                    idf = math.log1p((n - df + 0.5) / (df + 0.5))
                    score += idf * (tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / self.average_length)))
            if score > 0:
                scored.append((score, cid, chunk))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{"chunk": c, "score": s, "rank": rank}
                for rank, (s, _, c) in enumerate(scored[:top_k], 1)]


@dataclass
class CorpusPointSearchContext:
    corpus: CanonicalCorpus
    config: Any = None
    qdrant_client: Any = None
    embedder: Any = None
    vector_runner: Callable[..., Any] | None = None
    reranker: Callable[..., Any] | None = None
    scope_verified: bool = False
    verification: dict[str, Any] = field(default_factory=dict)
    _indexes: dict[str, BM25Index] = field(default_factory=dict)
    chunk_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.chunk_by_id:
            self.chunk_by_id = {c["chunk_id"]: c for rows in self.corpus.docs.values() for c in rows}
        if not self.groups:
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for doc_id, rows in self.corpus.docs.items():
                for chunk in rows:
                    payload = chunk.get("payload", {})
                    identity = str(payload.get("point_identity_key") or "")
                    if identity:
                        grouped[(doc_id, identity)].append(chunk)
            self.groups = {key: sorted(rows, key=chunk_order) for key, rows in grouped.items()}

    def bm25(self, doc_id: str | None) -> BM25Index:
        key = doc_id or "*"
        if key not in self._indexes:
            rows = self.corpus.docs[doc_id] if doc_id else [c for doc in sorted(self.corpus.docs) for c in self.corpus.docs[doc]]
            self._indexes[key] = BM25Index.build(rows)
        return self._indexes[key]


def _canonical_point(point: Any) -> dict[str, Any]:
    payload = dict(getattr(point, "payload", None) or {})
    cid = str(payload.pop("chunk_id", ""))
    return {"schema_version": payload.pop("schema_version", None), "chunk_id": cid,
            "text": payload.pop("text", None), "payload": payload}


def verify_live_corpus(context: CorpusPointSearchContext, *, page_size: int = PAGE_SIZE) -> dict[str, Any]:
    """Single full collection scroll; fail if live data differs or contains extra sources."""
    if page_size < 1:
        raise ValueError("page_size must be positive")
    if context.qdrant_client is None:
        from mr_norm.config.indexing import IndexingConfig
        from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient
        context.config = context.config or IndexingConfig.from_env()
        context.qdrant_client = QdrantRetrievalClient(context.config)
    client = context.qdrant_client
    raw = getattr(client, "client", client)
    cfg = context.config or getattr(client, "config", None)
    if cfg is None:
        from mr_norm.config.indexing import IndexingConfig
        cfg = IndexingConfig.from_env()
    expected = context.chunk_by_id
    actual: set[str] = set()
    offset = None
    offsets: set[str] = set()
    calls = 0
    started = time.perf_counter()
    from mr_norm.retrieval.qdrant_adapter import filter_spec_to_qdrant_filter
    allowlist = sorted(context.corpus.docs)
    scoped_filter = filter_spec_to_qdrant_filter({"must": [{"field": "doc_id", "any": allowlist}]})
    while True:
        points, offset = raw.scroll(collection_name=cfg.collection_name, scroll_filter=scoped_filter,
                                    limit=page_size, offset=offset, with_payload=True, with_vectors=False)
        calls += 1
        for point in points:
            row = _canonical_point(point)
            cid = row["chunk_id"]
            if not cid or cid in actual:
                raise ValueError(f"live Qdrant has missing or duplicate chunk_id: {cid}")
            if cid not in expected:
                raise ValueError(f"live Qdrant contains non-canonical chunk: {cid}")
            if row != expected[cid]:
                raise ValueError(f"live Qdrant differs from canonical chunk {cid}")
            actual.add(cid)
        if offset is None:
            break
        marker = str(offset)
        if marker in offsets:
            raise ValueError("Qdrant scroll returned a repeated offset")
        offsets.add(marker)
    missing = set(expected) - actual
    if missing:
        raise ValueError(f"live Qdrant is missing {len(missing)} canonical chunks")
    context.scope_verified = True
    context.corpus.qdrant_verified_doc_ids = frozenset(context.corpus.docs)
    manifest = {"collection_name": cfg.collection_name, "vector_name": cfg.vector_name,
                "chunk_count": len(actual), "document_count": len(context.corpus.docs),
                "rpc_count": calls, "page_size": page_size,
                "elapsed_sec": round(time.perf_counter() - started, 4),
                "corpus_sha256": _corpus_fingerprint(context.corpus)}
    context.verification = manifest
    return manifest


def _corpus_fingerprint(corpus: CanonicalCorpus) -> str:
    h = hashlib.sha256()
    for doc_id in sorted(corpus.docs):
        for chunk in corpus.docs[doc_id]:
            h.update(chunk["chunk_id"].encode())
            h.update(str(chunk.get("text") or "").encode())
            h.update(json.dumps(chunk.get("payload", {}), sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def _vector_hits(context: CorpusPointSearchContext, query: str, doc_id: str | None, top_k: int) -> list[Any]:
    if context.vector_runner is not None:
        import inspect
        parameters = inspect.signature(context.vector_runner).parameters
        if "doc_ids" in parameters:
            return list(context.vector_runner(query=query, doc_id=doc_id,
                                              doc_ids=tuple(context.corpus.docs) if doc_id is None else (doc_id,),
                                              top_k=top_k, config=context.config))
        return list(context.vector_runner(query=query, doc_id=doc_id, top_k=top_k, config=context.config))
    if context.qdrant_client is None:
        from mr_norm.config.indexing import IndexingConfig
        from mr_norm.retrieval.qdrant_adapter import QdrantRetrievalClient
        context.config = context.config or IndexingConfig.from_env()
        context.qdrant_client = QdrantRetrievalClient(context.config)
    if context.embedder is None:
        from mr_norm.indexing.qdrant_adapter import SentenceTransformerEmbedder
        context.embedder = SentenceTransformerEmbedder(context.config)
    vector = context.embedder.encode([query])[0]
    client = context.qdrant_client
    spec = ({"must": [{"field": "doc_id", "value": doc_id}]} if doc_id else
            {"must": [{"field": "doc_id", "any": sorted(context.corpus.docs)}]})
    if hasattr(client, "vector_search"):
        return list(client.vector_search(vector, spec, limit=top_k, source_tool="vector"))
    from mr_norm.retrieval.qdrant_adapter import filter_spec_to_qdrant_filter
    raw = getattr(client, "client", client)
    qfilter = filter_spec_to_qdrant_filter(spec)
    if hasattr(raw, "search"):
        points = raw.search(collection_name=context.config.collection_name,
                            query_vector=(context.config.vector_name, vector), query_filter=qfilter,
                            limit=top_k, with_payload=True, with_vectors=False)
    else:
        points = raw.query_points(collection_name=context.config.collection_name, query=vector,
                                  using=context.config.vector_name, query_filter=qfilter,
                                  limit=top_k, with_payload=True, with_vectors=False).points
    from mr_norm.retrieval.qdrant_adapter import point_to_item
    return [point_to_item(p, source_tool="vector") for p in points]


def _row(item: Any) -> dict[str, Any]:
    return dict(item) if isinstance(item, dict) else vars(item)


def _point_identity(chunk: dict[str, Any], context: CorpusPointSearchContext) -> tuple[str | None, str]:
    payload = chunk.get("payload", {})
    identity = payload.get("point_identity_key")
    if identity:
        return str(identity), "point_identity_key"
    point_id = str(payload.get("point_id") or "")
    doc_id = str(payload.get("doc_id") or "")
    if point_id:
        matches = [c for c in context.corpus.docs[doc_id] if str(c.get("payload", {}).get("point_id") or "") == point_id]
        if matches and len({str(c.get("payload", {}).get("point_identity_key") or "") for c in matches}) <= 1:
            return point_id, "point_id"
    return None, "unresolved_identity"


def assemble_point_by_identity(context: CorpusPointSearchContext, doc_id: str, identity: str,
                               identity_basis: str) -> dict[str, Any]:
    if identity_basis == "point_identity_key":
        parts = list(context.groups.get((doc_id, identity), []))
    else:
        parts = sorted([c for c in context.corpus.docs[doc_id]
                        if str(c.get("payload", {}).get("point_id") or "") == identity], key=chunk_order)
    if not parts:
        return {"complete": False, "source_check": "identity_not_found", "text": "", "all_point_chunk_ids": []}
    payloads = [p.get("payload", {}) for p in parts]
    totals = {p.get("total_parts") for p in payloads}
    indexes = [p.get("part_index") for p in payloads]
    total = next(iter(totals)) if len(totals) == 1 else None
    valid = isinstance(total, int) and total > 0 and indexes == list(range(total)) and len(parts) == total
    same_identity = all(str(p.get("point_identity_key") or p.get("point_id") or "") == identity for p in payloads)
    same_doc = all(str(p.get("doc_id") or "") == doc_id for p in payloads)
    same_point_no = len({str(p.get("point_number") or "") for p in payloads}) == 1
    ids = [p["chunk_id"] for p in parts]
    if not (valid and same_identity and same_doc and same_point_no):
        return {"complete": False, "source_check": "split_parts_incomplete_or_inconsistent",
                "text": "".join(str(p.get("text") or "") for p in parts), "verbatim_parts": [p["text"] for p in parts],
                "all_point_chunk_ids": ids, "part_count": len(parts), "total_parts": total}
    point_number = str(payloads[0].get("point_number") or "").strip()
    escaped = re.escape(point_number).replace(r"\_", r"[_ .]").replace(r"\.", r"[._]")
    # A period is a valid label delimiter only when it is not followed by another digit;
    # otherwise labels such as 9.1 or 9.1.2 would be mistaken for their parent point.
    if not point_number or not re.match(rf"^{escaped}(?:\s|\)|\.(?!\d))", str(parts[0].get("text") or "").lstrip()):
        return {"complete": False, "source_check": "first_chunk_text_does_not_confirm_point_label",
                "text": "".join(str(p.get("text") or "") for p in parts),
                "verbatim_parts": [p["text"] for p in parts], "all_point_chunk_ids": ids,
                "part_count": len(parts), "total_parts": total}
    filename = payloads[0].get("filename")
    spans = [(p.get("char_start"), p.get("char_end")) for p in payloads]
    source_text = None
    source_check = ""
    if filename and all(isinstance(a, int) and isinstance(b, int) and b > a for a, b in spans):
        structured = (context.corpus.root / "output/marked_docs" /
                      Path(str(filename)).with_suffix(".structured.json").name).resolve()
        marked = (context.corpus.root / "output/marked_docs").resolve()
        if marked in structured.parents and structured.is_file():
            data = json.loads(structured.read_text(encoding="utf-8-sig"))
            lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
            paragraphs = [p for p in data.get("paragraphs", []) if isinstance(p.get("char_start"), int)
                          and isinstance(p.get("char_end"), int) and lo <= p["char_start"] and p["char_end"] <= hi
                          and str(p.get("text") or "").strip()]
            paragraphs.sort(key=lambda p: (p.get("paragraph_index", -1), p.get("char_start", -1)))
            candidate = "\n".join(str(p["text"]) for p in paragraphs).strip()
            joined = "".join(str(p.get("text") or "") for p in parts)
            if paragraphs and "".join(candidate.split()) == "".join(joined.split()):
                source_text, source_check = candidate, "verified_structured_source_whitespace_normalized"
            else:
                source_check = "structured_source_mismatch"
        else:
            source_check = "structured_source_missing_or_unsafe_path"
    else:
        source_check = "missing_or_invalid_source_span"
    if source_text is None and len(parts) == 1 and valid and source_check != "structured_source_mismatch":
        source_text, source_check = str(parts[0]["text"]), "single_chunk_verbatim"
    return {"complete": source_text is not None, "source_check": source_check,
            "text": source_text if source_text is not None else "".join(str(p.get("text") or "") for p in parts),
            "verbatim_parts": None if source_text is not None else [p["text"] for p in parts],
            "all_point_chunk_ids": ids, "part_count": len(parts), "total_parts": total}


def corpus_point_search(query: str, doc_id: str | None = None, *,
                        context: CorpusPointSearchContext, top_k_per_branch: int = TOP_K) -> dict[str, Any]:
    """Search all canonical documents or exactly one doc_id; return up to ten complete points."""
    if not isinstance(query, str) or not query.strip():
        return {"status": "needs_clarification", "reason": "empty_query"}
    if doc_id is not None and (not isinstance(doc_id, str) or not doc_id.strip()):
        return {"status": "invalid_scope", "reason": "doc_id_must_be_nonempty_or_none"}
    if doc_id is not None and doc_id not in context.corpus.docs:
        return {"status": "invalid_scope", "reason": "unknown_canonical_doc_id", "doc_id": doc_id}
    scope = "global" if doc_id is None else "document"
    if doc_id is None and not context.scope_verified:
        return {"status": "needs_review", "reason": "global_live_scope_not_verified"}
    if doc_id is not None and doc_id not in context.corpus.qdrant_verified_doc_ids:
        return {"status": "needs_review", "reason": "scoped_live_document_not_verified", "doc_id": doc_id}
    if top_k_per_branch < 1 or top_k_per_branch > 100:
        raise ValueError("top_k_per_branch must be between 1 and 100")
    bm25_started = time.perf_counter()
    bm_hits = context.bm25(doc_id).search(query, top_k_per_branch)
    bm25_sec = time.perf_counter() - bm25_started
    try:
        vector_started = time.perf_counter()
        dense_hits = _vector_hits(context, query, doc_id, top_k_per_branch)
        vector_sec = time.perf_counter() - vector_started
    except Exception as exc:
        return {"status": "needs_review", "reason": "dense_retrieval_failed", "error": f"{type(exc).__name__}: {exc}"}
    provenance: dict[str, dict[str, Any]] = {"bm25": {}, "vector": {}}
    for hit in bm_hits:
        c = hit["chunk"]
        provenance["bm25"][c["chunk_id"]] = {"rank": hit["rank"], "score": hit["score"]}
    for rank, hit in enumerate(dense_hits, 1):
        row = _row(hit)
        cid, hit_doc = str(row.get("chunk_id") or ""), str(row.get("doc_id") or "")
        if cid in provenance["vector"]:
            return {"status": "needs_review", "reason": "dense_hit_duplicate_chunk_id", "chunk_id": cid}
        if cid not in context.chunk_by_id or not cid:
            return {"status": "needs_review", "reason": "dense_hit_unknown_chunk", "chunk_id": cid}
        canonical = context.chunk_by_id[cid]
        canonical_doc = str(canonical.get("payload", {}).get("doc_id") or "")
        if hit_doc != canonical_doc or (doc_id and canonical_doc != doc_id):
            return {"status": "needs_review", "reason": "dense_hit_source_identity_mismatch", "chunk_id": cid,
                    "hit_doc_id": hit_doc, "canonical_doc_id": canonical_doc}
        if row.get("text") is not None and row.get("text") != canonical.get("text"):
            return {"status": "needs_review", "reason": "dense_hit_text_mismatch", "chunk_id": cid}
        canonical_payload = canonical.get("payload", {})
        for field_name in ("point_id", "point_number", "point_identity_key", "filename"):
            if row.get(field_name) is not None and str(row.get(field_name)) != str(canonical_payload.get(field_name) or ""):
                return {"status": "needs_review", "reason": "dense_hit_metadata_mismatch",
                        "chunk_id": cid, "field": field_name}
        provenance["vector"][cid] = {"rank": rank, "score": row.get("score")}
    ids = list(dict.fromkeys([*provenance["bm25"], *provenance["vector"]]))
    candidates = []
    for cid in ids:
        chunk = context.chunk_by_id[cid]
        payload = chunk["payload"]
        did = str(payload["doc_id"])
        entry = context.corpus.entry_for_doc(did)
        candidates.append({"chunk_id": cid, "doc_id": did, "doc_name": entry.get("doc_name", ""),
                           "registry_key": entry.get("registry_key"), "point_id": payload.get("point_id"),
                           "point_number": payload.get("point_number"),
                           "point_identity_key": payload.get("point_identity_key"), "text": chunk["text"],
                           "provenance": {b: provenance[b][cid] for b in provenance if cid in provenance[b]}})
    reranker = context.reranker or rerank_local
    try:
        rerank_started = time.perf_counter()
        ranked, device, rerank_elapsed = reranker(query, candidates)
        rerank_sec = time.perf_counter() - rerank_started
    except Exception as exc:
        return {"status": "needs_review", "reason": "reranker_failed", "error": f"{type(exc).__name__}: {exc}"}
    ranked_ids = [str(r.get("chunk_id") or "") for r in ranked]
    if len(ranked_ids) != len(set(ranked_ids)) or set(ranked_ids) != set(ids):
        return {"status": "needs_review", "reason": "reranker_changed_candidate_identity"}
    candidate_by_id = {str(c["chunk_id"]): c for c in candidates}
    canonical_ranked = []
    for returned in ranked:
        cid = str(returned["chunk_id"])
        if cid not in context.chunk_by_id or returned.get("doc_id") != context.chunk_by_id[cid]["payload"].get("doc_id"):
            return {"status": "needs_review", "reason": "reranker_changed_source_identity", "chunk_id": cid}
        original = candidate_by_id[cid]
        for field_name in ("text", "point_number", "point_identity_key", "point_id", "doc_name", "registry_key"):
            if returned.get(field_name) != original.get(field_name):
                return {"status": "needs_review", "reason": "reranker_changed_source_metadata",
                        "chunk_id": cid, "field": field_name}
        # Treat only ranking score/order as reranker output; all source data is canonical.
        canonical_ranked.append({**candidate_by_id[cid], "reranker_score": returned.get("reranker_score")})
    ranked = canonical_ranked
    raw = []
    for rank, row in enumerate(ranked[:OUTPUT_POINTS], 1):
        raw.append({"rank": rank, **row})
    result_rows = []
    ranked_identity_chunk_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    for candidate in ranked:
        source = context.chunk_by_id[candidate["chunk_id"]]
        candidate_identity, _ = _point_identity(source, context)
        if candidate_identity is not None:
            ranked_identity_chunk_ids[(candidate["doc_id"], candidate_identity)].append(candidate["chunk_id"])
    seen: set[tuple[str, str]] = set()
    unresolved = []
    duplicate_raw = 0
    backfill_ranks = []
    for rank, row in enumerate(ranked, 1):
        did = str(row["doc_id"])
        canonical = context.chunk_by_id[row["chunk_id"]]
        payload = canonical["payload"]
        point_number = str(payload.get("point_number") or "")
        if not point_number:
            continue
        identity, basis = _point_identity(canonical, context)
        if identity is None:
            unresolved.append({"rank": rank, "chunk_id": row["chunk_id"], "doc_id": did,
                               "point_number": point_number, "reason": "unresolved_identity"})
            continue
        key = (did, identity)
        if key in seen:
            if rank <= OUTPUT_POINTS:
                duplicate_raw += 1
            continue
        seen.add(key)
        assembly = assemble_point_by_identity(context, did, identity, basis)
        if rank > OUTPUT_POINTS:
            backfill_ranks.append(rank)
        entry = context.corpus.entry_for_doc(did)
        item = {"rank": len(result_rows) + 1, "doc_id": did, "doc_name": entry.get("doc_name", ""),
                "registry_key": entry.get("registry_key"), "point_number": point_number,
                "point_identity_key": payload.get("point_identity_key"), "point_id": payload.get("point_id"),
                "identity_basis": basis, "filename": payload.get("filename"),
                "heading_path_text": payload.get("heading_path_text"), "section_title": payload.get("section_title"),
                "text": assembly["text"], "complete": assembly["complete"], "source_check": assembly["source_check"],
                "best_chunk_id": row["chunk_id"], "best_chunk_rank": rank,
                "reranker_score": row.get("reranker_score"), "matched_chunk_ids": ranked_identity_chunk_ids.get(key, [row["chunk_id"]]),
                "all_point_chunk_ids": assembly["all_point_chunk_ids"], "part_count": assembly.get("part_count"),
                "total_parts": assembly.get("total_parts"), "provenance": row.get("provenance", {})}
        result_rows.append(item)
        if len(result_rows) >= OUTPUT_POINTS:
            break
    raw_unique = set()
    for row in ranked[:OUTPUT_POINTS]:
        chunk = context.chunk_by_id[row["chunk_id"]]
        n = str(chunk.get("payload", {}).get("point_number") or "")
        ident, _basis = _point_identity(chunk, context)
        if n and ident:
            raw_unique.add((row["doc_id"], ident))
    complete_count = sum(bool(r["complete"]) for r in result_rows)
    status = "found" if result_rows and complete_count == len(result_rows) else "partial" if result_rows else "no_evidence"
    if unresolved and status == "found":
        status = "partial"
    return {"schema_version": "corpus_point_search_v1", "query": query,
            "scope": {"mode": scope, "doc_id": doc_id, "doc_count": 1 if doc_id else len(context.corpus.docs),
                      "live_verified": context.scope_verified if doc_id is None else (doc_id in context.corpus.qdrant_verified_doc_ids)},
            "requested_point_count": OUTPUT_POINTS, "returned_point_count": len(result_rows),
            "complete_point_count": complete_count, "status": status,
            "exhaustive": False, "raw_top_chunks": raw, "raw_chunk_count": len(raw), "results": result_rows,
            "diagnostics": {"bm25_top_chunk_ids": [h["chunk"]["chunk_id"] for h in bm_hits],
                "vector_top_chunk_ids": [str(_row(h).get("chunk_id") or "") for h in dense_hits],
                "candidate_count": len(candidates), "candidate_chunk_ids": ids, "reranked_chunk_ids": ranked_ids,
                "unique_points_in_raw_top10": len(raw_unique), "deduplicated_chunk_count": duplicate_raw,
                "backfill_count": len(backfill_ranks), "backfill_best_chunk_ranks": backfill_ranks,
                "unresolved_candidates": unresolved, "insufficient_unique_points": len(result_rows) < OUTPUT_POINTS,
                "reranker_device": device, "reranker_elapsed_sec": rerank_elapsed,
                "timing_sec": {"bm25": bm25_sec, "dense": vector_sec, "reranker": rerank_sec}},
            "coverage_note": "Ranked BM25+dense candidate pool only; not exhaustive. Duplicate chunks from one point are backfilled from lower ranks. Full point text is restored from all canonical parts and source checked."}
