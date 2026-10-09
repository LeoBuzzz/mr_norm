"""Fail-closed exact-point and lexical all-mentions skills for the sandbox."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from dataclasses import asdict, is_dataclass

from corpus import CanonicalCorpus, chunk_order, _norm


def _point_candidates(chunks: list[dict[str, Any]], requested: str) -> list[list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        payload = chunk.get("payload", {})
        if str(payload.get("point_number", "")).strip() == requested:
            identity = str(payload.get("point_identity_key") or payload.get("point_id") or chunk["chunk_id"])
            groups[identity].append(chunk)
    return [sorted(group, key=chunk_order) for group in groups.values()]


def _verified_source_text(corpus: CanonicalCorpus, parts: list[dict[str, Any]]) -> tuple[str | None, str]:
    first = parts[0]["payload"]
    start, end = first.get("char_start"), first.get("char_end")
    filename = first.get("filename")
    if not filename or not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None, "missing_source_span"
    structured = (corpus.root / "output/marked_docs" / Path(str(filename)).with_suffix(".structured.json").name).resolve()
    marked = (corpus.root / "output/marked_docs").resolve()
    if marked not in structured.parents or not structured.is_file():
        return None, "structured_source_missing"
    data = json.loads(structured.read_text(encoding="utf-8-sig"))
    paragraphs = [p for p in data.get("paragraphs", [])
                  if isinstance(p.get("char_start"), int) and isinstance(p.get("char_end"), int)
                  and start <= p["char_start"] and p["char_end"] <= end and str(p.get("text") or "").strip()]
    paragraphs.sort(key=lambda p: (p.get("paragraph_index", -1), p.get("char_start", -1)))
    if not paragraphs:
        return None, "source_span_has_no_paragraphs"
    source_text = "\n".join(str(p["text"]) for p in paragraphs).strip()
    chunk_text = "".join(str(p["text"]) for p in parts)
    if _without_whitespace(source_text) != _without_whitespace(chunk_text):
        return None, "source_text_mismatch"
    return source_text, "verified_source_whitespace_normalized"


def _without_whitespace(text: str) -> str:
    return "".join(text.split())


def exact_point(
    corpus: CanonicalCorpus,
    document_hint: str,
    point_number: str,
    *,
    config: Any = None,
    point_lookup_fn: Any = None,
) -> dict[str, Any]:
    try:
        entry = corpus.resolve_document(document_hint)
    except LookupError as exc:
        return {"status": "needs_clarification" if "ambiguous" in str(exc) else "not_found", "reason": str(exc), "candidates": []}
    doc_id = entry["doc_id"]
    requested = str(point_number).strip()
    groups = _point_candidates(corpus.docs[doc_id], requested)
    if not groups:
        return {"status": "not_found_needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                "requested_point": requested, "reason": "No exact point_number match; no nearby-number substitution was attempted."}
    if len(groups) != 1:
        return {"status": "needs_clarification", "registry_key": entry["registry_key"], "doc_id": doc_id,
                "requested_point": requested, "candidate_point_identities": [g[0]["payload"].get("point_identity_key") for g in groups]}
    parts = groups[0]
    totals = {p["payload"].get("total_parts") for p in parts}
    indexes = [p["payload"].get("part_index") for p in parts]
    if len(totals) != 1 or not isinstance(next(iter(totals)), int) or indexes != list(range(next(iter(totals)))):
        return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                "requested_point": requested, "reason": "split_parts_incomplete_or_out_of_order", "chunk_ids": [p["chunk_id"] for p in parts]}
    total = next(iter(totals))
    if any(p["payload"].get("point_number") != requested for p in parts):
        return {"status": "needs_review", "reason": "point_number_metadata_mismatch"}
    first_text = parts[0]["text"].lstrip()
    escaped = re.escape(requested).replace(r"\_", r"[_ .]").replace(r"\.", r"[._]")
    if not re.match(rf"^{escaped}(?:\s|[.)])", first_text):
        return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                "requested_point": requested, "reason": "first_chunk_text_does_not_confirm_point_label", "chunk_ids": [p["chunk_id"] for p in parts]}
    if config is not None or point_lookup_fn is not None:
        import sys
        sys.path.insert(0, str(corpus.root / "src"))
        from mr_norm.skills.point_lookup import POINT_FETCH_LIMIT, PointLookupRequest, lookup_point
        if len(parts) > POINT_FETCH_LIMIT and point_lookup_fn is None:
            # point_lookup caps requests at 20; its lower-level point tool can
            # fetch all parts of the already verified complete point group.
            from mr_norm.retrieval.contracts import ToolRequest
            from mr_norm.retrieval.point_assemble import filter_items_by_exact_point
            from mr_norm.retrieval.tools.point import run_point_tool

            tool_result = run_point_tool(
                ToolRequest(query=requested, filters={"doc_id": doc_id, "point_number": requested},
                            limit=len(parts), profile="fast", trace_id="sandbox_exact_point_long"),
                config,
            )
            retrieved_parts = sorted(filter_items_by_exact_point(list(tool_result.items), requested),
                                     key=lambda item: (item.part_index, item.chunk_id))
            retrieved_ids = [part.chunk_id for part in retrieved_parts]
            retrieved_text = "".join(part.text for part in retrieved_parts)
        else:
            lookup = point_lookup_fn or lookup_point
            retrieved = lookup(PointLookupRequest(doc_id=doc_id, point_number=requested, limit=POINT_FETCH_LIMIT,
                                                  trace_id="sandbox_exact_point"), config=config)
            if not retrieved.found:
                return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                        "requested_point": requested, "reason": "existing_point_lookup_did_not_find_exact_point",
                        "warnings": list(retrieved.warnings)}
            retrieved_ids = [part.chunk_id for part in retrieved.parts]
            retrieved_text = "".join(part.text for part in retrieved.parts)
        expected_ids = [part["chunk_id"] for part in parts]
        if retrieved_ids != expected_ids:
            return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                    "requested_point": requested, "reason": "existing_point_tool_refs_disagree_with_canonical_group",
                    "retrieved_chunk_ids": retrieved_ids, "canonical_chunk_ids": expected_ids}
    source_text, source_check = _verified_source_text(corpus, parts)
    if source_text is None:
        if len(parts) == 1:
            source_text, source_check = parts[0]["text"], "single_chunk_verbatim"
        else:
            return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                    "requested_point": requested, "reason": source_check, "verbatim_parts": [p["text"] for p in parts],
                    "chunk_ids": [p["chunk_id"] for p in parts]}
    if config is not None or point_lookup_fn is not None:
        if _without_whitespace(retrieved_text) != _without_whitespace(source_text):
            return {"status": "needs_review", "registry_key": entry["registry_key"], "doc_id": doc_id,
                    "requested_point": requested, "reason": "existing_point_tool_text_disagrees_with_verified_source",
                    "chunk_ids": [part["chunk_id"] for part in parts]}
    return {"status": "found", "registry_key": entry["registry_key"], "doc_id": doc_id,
            "point_number": requested, "point_identity_key": parts[0]["payload"].get("point_identity_key"),
            "point_id": parts[0]["payload"].get("point_id"), "text": source_text,
            "text_whitespace_normalized": source_check == "verified_source_whitespace_normalized",
            "complete": len(parts) == total, "part_count": len(parts), "total_parts": total,
            "chunk_ids": [p["chunk_id"] for p in parts], "source_check": source_check}


def _lexical_roots(query: str) -> list[str]:
    """Use conservative 5-character roots of content terms as a transparent heuristic."""
    tokens = re.findall(r"[^\W_]+", query.lower(), flags=re.UNICODE)
    stop = {"дай", "мне", "все", "всё", "пункты", "пункт", "про", "из", "по", "для", "при", "что", "как", "есть",
            "это", "вытащи", "вытяни", "покажи", "найди", "касаемые", "касающихся", "указаны", "указанные",
            "правил", "правила", "правилах", "переключений", "переключение", "постановления", "приказа", "документа"}
    return list(dict.fromkeys(token[:5] for token in tokens if len(token) >= 6 and token not in stop))


def all_mentions(corpus: CanonicalCorpus, document_hint: str, query: str, *, max_results: int = 100) -> dict[str, Any]:
    try:
        entry = corpus.resolve_document(document_hint)
    except LookupError as exc:
        return {"status": "needs_clarification" if "ambiguous" in str(exc) else "not_found", "reason": str(exc), "candidates": []}
    if max_results < 1:
        raise ValueError("max_results must be positive")
    roots = _lexical_roots(query)
    if not roots:
        return {"status": "needs_clarification", "reason": "query_has_no_content_terms", "registry_key": entry["registry_key"]}
    chunks = corpus.docs[entry["doc_id"]]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        p = chunk.get("payload", {})
        identity = str(p.get("point_identity_key") or p.get("point_id") or chunk["chunk_id"])
        grouped[identity].append(chunk)
    results = []
    matched_ids: set[str] = set()
    for identity, hits in sorted(grouped.items()):
        ordered_hits = sorted(hits, key=chunk_order)
        # Match across the complete point group: requested terms can fall on different split parts.
        whole_text = " ".join(str(x.get("text", "")) for x in ordered_hits).lower()
        matched_terms = [root for root in roots if root in whole_text]
        required_terms = len(roots) if len(roots) <= 2 else max(2, (len(roots) + 1) // 2)
        if len(matched_terms) < required_terms:
            continue
        direct_hits = [x for x in ordered_hits if any(root in str(x.get("text", "")).lower() for root in matched_terms)]
        matching_parts = direct_hits or [
            x for x in ordered_hits if any(root in str(x.get("text", "")).lower() for root in matched_terms)
        ]
        matched_ids.update(x["chunk_id"] for x in matching_parts)
        exemplar = ordered_hits[0]["payload"]
        results.append({"point_number": exemplar.get("point_number"), "point_identity_key": identity,
                        "matched_terms": matched_terms, "required_term_count": required_terms,
                        "matched_chunk_ids": [x["chunk_id"] for x in matching_parts],
                        "source_refs": [{"chunk_id": x["chunk_id"], "point_id": x["payload"].get("point_id"),
                                         "part_index": x["payload"].get("part_index"), "total_parts": x["payload"].get("total_parts")} for x in ordered_hits]})
    required_terms = len(roots) if len(roots) <= 2 else max(2, (len(roots) + 1) // 2)
    return {"status": "found" if results else "no_lexical_matches", "registry_key": entry["registry_key"],
            "doc_id": entry["doc_id"], "lexical_roots": roots, "scanned_chunk_count": len(chunks),
            "minimum_terms_per_point_group": required_terms,
            "matched_chunk_count": len(matched_ids), "found_point_count": len(results), "shown_point_count": min(len(results), max_results),
            "truncated": len(results) > max_results, "coverage": "full_document_lexical_scan",
            "semantic_complete": False, "coverage_note": "Эвристические буквенные корни ищутся по сгруппированным частям: для длинного запроса требуется половина корней (округление вверх), минимум два. Это кандидатный лексический список, не исчерпывающий поиск смысловых упоминаний и не готовый полный ответ.",
            "results": results[:max_results]}


# Kept for explicit backwards-compatible experiments; the router now dispatches
# the public all_mentions label to the wide find_all skill below.
all_mentions_legacy = all_mentions


def _topic_from_query(corpus: CanonicalCorpus, query: str, document_hint: str) -> str:
    """Remove a recognized document alias before deriving lexical topic terms."""
    if not document_hint:
        return query
    try:
        entry = corpus.resolve_document(document_hint)
    except LookupError:
        return query
    normalized = _norm(query)
    aliases = [entry.get("doc_name", ""), *entry.get("aliases", [])]
    for alias in sorted((x for x in aliases if x), key=len, reverse=True):
        token = _norm(alias)
        if token and f" {token} " in f" {normalized} ":
            normalized = f" {normalized} ".replace(f" {token} ", " ")
    return normalized


def applicability(
    corpus: CanonicalCorpus,
    query: str,
    *,
    document_hint: str = "",
    doc_id: str = "",
    config: Any = None,
    limit: int = 8,
    lookup_runner: Any = None,
) -> dict[str, Any]:
    """Retrieve scoped evidence only; preserve source excerpts and never decide legal applicability."""
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    if doc_id:
        if doc_id not in corpus.docs:
            return {"status": "needs_clarification", "reason": "selected_document_not_in_verified_scope", "doc_id": doc_id}
        entry = corpus.entry_for_doc(doc_id)
    elif document_hint:
        try:
            entry = corpus.resolve_document(document_hint)
        except LookupError as exc:
            return {"status": "needs_clarification", "reason": str(exc)}
        doc_id = entry["doc_id"]
        if doc_id not in corpus.docs:
            return {"status": "needs_clarification", "reason": "resolved_document_not_in_verified_scope", "doc_id": doc_id}
    else:
        return {"status": "needs_clarification", "reason": "applicability_requires_an_explicit_document_or_prior_resolution"}

    import sys
    sys.path.insert(0, str(corpus.root / "src"))
    from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
    from mr_norm.config.indexing import IndexingConfig
    request = NormLookupRequest(query=query, filters={"doc_id": doc_id}, profile="fast", limit=limit,
                                mode="evidence", planner_backend="deterministic", final_answer_backend="evidence",
                                llm_provider="none", understand_query_mode="off", trace_id="sandbox_applicability")
    runner = lookup_runner or run_norm_lookup
    result = runner(request, config=config or IndexingConfig.from_env())
    local_chunk_map = {chunk["chunk_id"]: chunk for chunk in corpus.docs[doc_id]}
    source_rows = []
    invalid_refs = []
    for item in result.evidence:
        row = asdict(item) if is_dataclass(item) else dict(item)
        chunk_id = str(row.get("chunk_id") or "")
        source = local_chunk_map.get(chunk_id)
        if not source or row.get("doc_id") not in (None, "", doc_id):
            invalid_refs.append(chunk_id)
            continue
        source_rows.append({"chunk_id": chunk_id, "doc_id": doc_id,
                            "point_id": source["payload"].get("point_id"),
                            "point_number": source["payload"].get("point_number"),
                            "text": source["text"]})
    if invalid_refs:
        return {"status": "needs_review", "doc_id": doc_id, "registry_key": entry["registry_key"],
                "reason": "retrieval_returned_source_refs_outside_verified_document", "invalid_chunk_ids": invalid_refs}
    citation_ids = {citation.chunk_id for citation in result.citations}
    if not citation_ids.issubset(local_chunk_map):
        return {"status": "needs_review", "doc_id": doc_id, "registry_key": entry["registry_key"],
                "reason": "citation_outside_verified_document"}
    return {"status": "evidence_only" if source_rows else "no_evidence", "doc_id": doc_id,
            "registry_key": entry["registry_key"], "doc_name": entry["doc_name"],
            "decision": "not_assessed", "requires_case_specific_review": True,
            "returned_excerpts_verbatim": True, "condition_exclusion_completeness": "unknown",
            "evidence_limit": limit, "evidence_count": len(source_rows), "source_refs": source_rows,
            "citation_chunk_ids": sorted(citation_ids), "warnings": list(result.warnings),
            "coverage_note": "Ограниченная retrieval-выборка из указанного документа. Фрагменты приведены дословно; условия и исключения в них сохранены. Применимость и правовой вывод не определяются."}


def hybrid_applicability(
    corpus: CanonicalCorpus,
    query: str,
    *,
    document_hint: str = "",
    doc_id: str = "",
    doc_ids: tuple[str, ...] | list[str] = (),
    document_hints: tuple[str, ...] | list[str] = (),
    config: Any = None,
    limit: int = 8,
    top_k_per_branch: int = 30,
    vector_runner: Any = None,
    reranker: Any = None,
    expand_query: bool = False,
    query_rewriter: Any = None,
) -> dict[str, Any]:
    """Hybrid applicability evidence from a selected, completely Qdrant-verified document."""
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    selected_ids = list(dict.fromkeys([str(value) for value in doc_ids if value]))
    if doc_id:
        selected_ids = list(dict.fromkeys([*selected_ids, doc_id]))
    hints = list(dict.fromkeys([str(value) for value in document_hints if value] + ([document_hint] if document_hint else [])))
    try:
        for hint in hints:
            selected_ids.append(str(corpus.resolve_document(hint)["doc_id"]))
    except LookupError as exc:
        return {"status": "needs_clarification", "reason": str(exc)}
    selected_ids = list(dict.fromkeys(selected_ids))
    if not selected_ids:
        return {"status": "needs_clarification", "reason": "applicability_requires_an_explicit_document_or_prior_resolution"}
    out_of_scope = [value for value in selected_ids if value not in corpus.docs]
    if out_of_scope:
        return {"status": "needs_clarification", "reason": "selected_document_not_in_verified_scope", "doc_ids": out_of_scope}
    unverified = [value for value in selected_ids if value not in corpus.qdrant_verified_doc_ids]
    if unverified:
        return {"status": "needs_review", "reason": "hybrid_retrieval_requires_complete_live_qdrant_verification", "doc_ids": unverified}
    try:
        from hybrid_retrieval import retrieve_hybrid

        return retrieve_hybrid(corpus, query, selected_ids, config=config, top_k_per_branch=top_k_per_branch,
                               limit=limit, vector_runner=vector_runner, reranker=reranker,
                               expand_query=expand_query, query_rewriter=query_rewriter)
    except (FileNotFoundError, ImportError, RuntimeError, ValueError) as exc:
        return {"status": "needs_review", "doc_ids": selected_ids,
                "reason": "hybrid_retrieval_failed", "error": f"{type(exc).__name__}: {exc}"}


def dispatch_route(
    corpus: CanonicalCorpus,
    route: Any,
    query: str,
    *,
    document_hint: str = "",
    point_number: str = "",
    topic: str = "",
    config: Any = None,
    expand_query: bool = False,
    point_search_context: Any = None,
    find_all_config: Any = None,
    vector_runner: Any = None,
    reranker: Any = None,
) -> dict[str, Any]:
    """Bind router labels to skills while preserving multi-route output and uncertainty."""
    route_names = tuple(getattr(route, "routes", ()) or (getattr(route, "primary", ""),))
    hint = document_hint or getattr(route, "explicit_doc_hint", None) or ""
    point = point_number or getattr(route, "point_ref", None) or ""
    out: dict[str, Any] = {"routes": list(route_names), "uncertainty": getattr(route, "uncertainty", "high"), "results": {}}
    for name in route_names:
        if name == "exact_point":
            out["results"][name] = exact_point(corpus, hint, point, config=config) if hint and point else {
                "status": "needs_clarification", "reason": "exact point requires resolved document and point number"}
        elif name == "all_mentions":
            from corpus_point_search import CorpusPointSearchContext
            from find_all import find_all
            search_context = point_search_context or CorpusPointSearchContext(
                corpus=corpus, config=config, vector_runner=vector_runner, reranker=reranker)
            scoped_doc_id = ""
            if hint:
                try:
                    scoped_doc_id = str(corpus.resolve_document(hint)["doc_id"])
                except LookupError as exc:
                    out["results"][name] = {"status": "needs_clarification", "reason": str(exc),
                                             "document_hint": hint}
                    continue
            out["results"][name] = find_all(query, doc_id=scoped_doc_id or None,
                                             context=search_context, config=find_all_config)
        elif name == "corpus_point_search":
            from corpus_point_search import CorpusPointSearchContext, corpus_point_search
            search_context = point_search_context or CorpusPointSearchContext(
                corpus=corpus, config=config, vector_runner=vector_runner, reranker=reranker)
            scoped_doc_id = ""
            if hint:
                try:
                    scoped_doc_id = str(corpus.resolve_document(hint)["doc_id"])
                except LookupError as exc:
                    out["results"][name] = {"status": "needs_clarification", "reason": str(exc),
                                             "document_hint": hint}
                    continue
            out["results"][name] = corpus_point_search(
                query, doc_id=scoped_doc_id or None, context=search_context)
        elif name == "applicability":
            out["results"][name] = hybrid_applicability(corpus, query, document_hint=hint, config=config,
                                                          expand_query=expand_query)
        else:
            out["results"][name] = {"status": "unsupported_route", "route": name}
    return out


def route_and_dispatch(
    corpus: CanonicalCorpus,
    query: str,
    *,
    dialog_context: str | None = None,
    document_hint: str = "",
    point_number: str = "",
    topic: str = "",
    config: Any = None,
    point_search_context: Any = None,
    find_all_config: Any = None,
    vector_runner: Any = None,
    reranker: Any = None,
) -> dict[str, Any]:
    """Run the sandbox deterministic router and dispatch its one or more skills."""
    import sys
    sys.path.insert(0, str(corpus.root / "sandbox/skill_router_lab"))
    from router import route_query
    route = route_query(query, dialog_context=dialog_context)
    result = dispatch_route(corpus, route, query, document_hint=document_hint, point_number=point_number,
                           topic=topic, config=config, point_search_context=point_search_context,
                           find_all_config=find_all_config,
                           vector_runner=vector_runner, reranker=reranker)
    result["route"] = route.to_dict()
    return result
