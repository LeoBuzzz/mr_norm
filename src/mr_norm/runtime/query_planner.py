from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.document_catalog import (
    DocumentCatalog,
    DocumentCandidate,
    extract_point_number_hint,
    find_catalog_candidates,
    load_default_document_catalog,
    normalize_catalog_text,
)
from mr_norm.config.pue_aliases import PUE_ALIAS_KEY
from mr_norm.retrieval.query_intent import detect_query_intent, intent_search_terms
from mr_norm.retrieval.document_knowledge import (
    DocumentKnowledgeIndex,
    KnowledgeCandidate,
    QueryTermMatches,
    find_knowledge_candidates,
    is_pue_document_name,
    load_document_knowledge,
    match_query_terms,
    phrase_required_tokens,
    primary_exact_phrase,
)
from mr_norm.runtime.contracts import (
    DocumentResolution,
    PreparedQueryPlan,
    PreparedToolQuery,
    QueryPlannerTrace,
    QueryUnderstandingResult,
    QueryUnderstandingTrace,
)
from mr_norm.runtime.llm_providers import chat_json_with_model_fallback
from mr_norm.runtime.llm_profiles import resolve_role_models, resolve_role_profile
from mr_norm.runtime.prompts import load_prompt_pack_by_role

MIN_DOC_CONFIDENCE = 0.55
AMBIGUITY_SCORE_GAP = 0.08
ALLOWED_TOOLS = frozenset({"point", "payload", "vector"})
MAX_QUERIES_PER_TOOL = 4

LOOSE_SINGLE_WORDS = frozenset(
    {
        "напряжение",
        "напряжением",
        "безопасно",
        "безопасность",
        "безопасные",
        "заземление",
        "электроустановок",
        "пуэ",
    }
)


def _merge_candidates(
    catalog: DocumentCatalog,
    catalog_candidates: list[DocumentCandidate],
    knowledge_candidates: list[KnowledgeCandidate],
) -> list[dict[str, Any]]:
    by_doc_name = catalog.by_doc_name()
    merged: dict[str, dict[str, Any]] = {}

    for candidate in catalog_candidates:
        merged[candidate.doc_name] = {
            "catalog_id": candidate.catalog_id,
            "doc_name": candidate.doc_name,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "annotation": "",
            "source": "catalog",
        }

    for candidate in knowledge_candidates:
        entry = catalog.by_doc_name().get(candidate.doc_name)
        catalog_id = entry.catalog_id if entry else f"knowledge:{candidate.doc_id}"
        current = merged.get(candidate.doc_name)
        if current is None:
            merged[candidate.doc_name] = {
                "catalog_id": catalog_id,
                "doc_name": candidate.doc_name,
                "score": candidate.score,
                "reasons": list(candidate.reasons),
                "annotation": candidate.annotation,
                "source": "knowledge",
            }
            continue
        current["score"] = max(float(current["score"]), candidate.score)
        current["reasons"] = list(dict.fromkeys([*current["reasons"], *candidate.reasons]))
        if candidate.annotation:
            current["annotation"] = candidate.annotation
        current["source"] = "catalog+knowledge" if current["source"] == "catalog" else current["source"]

    ranked = sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)
    return ranked


def _topic_alias_ambiguous(candidates: list[dict[str, Any]], term_matches: QueryTermMatches) -> bool:
    if not term_matches.exact_phrase_terms:
        return False
    alias_hits = [
        candidate
        for candidate in candidates
        if any(str(reason).startswith("topic_alias:") for reason in candidate.get("reasons") or [])
    ]
    if len(alias_hits) < 2:
        return False
    scores = sorted((float(item["score"]) for item in alias_hits), reverse=True)
    return scores[0] - scores[1] < AMBIGUITY_SCORE_GAP


def _should_skip_title_phrase_doc_filter(
    term_matches: QueryTermMatches,
    resolved_doc_names: list[str],
) -> bool:
    if not resolved_doc_names or not term_matches.exact_phrase_terms:
        return False
    primary = primary_exact_phrase(term_matches.exact_phrase_terms)
    if not primary:
        return False
    resolved_norm = normalize_catalog_text(resolved_doc_names[0])
    primary_norm = normalize_catalog_text(primary)
    if len(primary_norm.split()) < 4:
        return False
    return primary_norm in resolved_norm or resolved_norm in primary_norm


def _should_skip_topic_alias_doc_filter(
    candidates: list[dict[str, Any]],
    term_matches: QueryTermMatches,
    resolved_doc_names: list[str],
    *,
    point_number_hints: list[str],
    enable_pue_aliases: bool,
    original_query: str,
) -> bool:
    if not term_matches.exact_phrase_terms or not resolved_doc_names or not candidates:
        return False
    if point_number_hints:
        return False
    if enable_pue_aliases and _query_mentions_pue(original_query):
        return False
    top = candidates[0]
    if str(top.get("doc_name") or "") != resolved_doc_names[0]:
        return False
    reasons = [str(reason) for reason in top.get("reasons") or []]
    if any(reason.startswith("known_alias:") for reason in reasons):
        return False
    if any(reason.startswith("order_number:") for reason in reasons):
        return False
    return any(reason.startswith("topic_alias:") for reason in reasons)


def _deterministic_resolve(candidates: list[dict[str, Any]]) -> tuple[list[str], float, bool, list[str]]:
    warnings: list[str] = []
    if not candidates:
        return [], 0.0, False, ["no document candidates matched the query"]

    top = candidates[0]
    second_score = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
    top_score = float(top["score"])
    ambiguous = len(candidates) > 1 and (top_score - second_score) < AMBIGUITY_SCORE_GAP
    confidence = min(1.0, top_score)

    if ambiguous:
        warnings.append("document resolution ambiguous; search will run without doc_name filter")
        return [], confidence, True, warnings

    if confidence < MIN_DOC_CONFIDENCE:
        warnings.append(
            f"document resolution confidence {confidence:.2f} below threshold; "
            "search will run without doc_name filter"
        )
        return [], confidence, False, warnings

    return [str(top["doc_name"])], confidence, False, warnings


def _phrase_context_queries(original_query: str, phrase: str) -> list[str]:
    original = re.sub(r"\s+", " ", original_query.strip())
    phrase_clean = re.sub(r"\s+", " ", phrase.strip())
    queries: list[str] = []
    if original:
        queries.append(original)
    if phrase_clean and phrase_clean not in queries:
        queries.append(phrase_clean)
    return queries


def _build_default_tool_queries(
    original_query: str,
    term_matches: QueryTermMatches,
    *,
    significant_words: list[str],
    question_type: str = "factual",
) -> dict[str, list[str]]:
    original = original_query.strip()
    exact_phrases = list(term_matches.exact_phrase_terms)
    support = list(
        dict.fromkeys(
            [
                *exact_phrases,
                *term_matches.abbreviation_expansions,
                *significant_words,
                *term_matches.loose_terms,
            ]
        )
    )

    payload_queries: list[str] = []
    vector_queries: list[str] = []

    if exact_phrases:
        primary = primary_exact_phrase(exact_phrases)
        for candidate in _phrase_context_queries(original, primary):
            if candidate not in payload_queries:
                payload_queries.append(candidate)
        if original and original not in vector_queries:
            vector_queries.append(original)
        for candidate in _phrase_context_queries(original, primary):
            if candidate not in vector_queries:
                vector_queries.append(candidate)
    else:
        core = [original, *support] if original else support
        payload_queries = [term for term in core if term]
        vector_queries = [term for term in core if term]

    intent = question_type or detect_query_intent(original_query)
    intent_terms = intent_search_terms(original_query, intent)
    if intent == "document_lookup" and intent_terms:
        payload_queries = list(
            dict.fromkeys([*intent_terms, *payload_queries])
        )[:MAX_QUERIES_PER_TOOL]
        vector_queries = list(
            dict.fromkeys([*intent_terms, *vector_queries])
        )[:MAX_QUERIES_PER_TOOL]
    else:
        for term in intent_terms:
            if term not in payload_queries:
                payload_queries.insert(1 if payload_queries else 0, term)
            if term not in vector_queries:
                vector_queries.insert(1 if vector_queries else 0, term)

    return {
        "point": [],
        "payload": payload_queries[:MAX_QUERIES_PER_TOOL],
        "vector": vector_queries[:MAX_QUERIES_PER_TOOL],
    }


def _normalize_tool_queries(
    raw: Any,
    original_query: str,
    *,
    term_matches: QueryTermMatches,
    significant_words: list[str],
    question_type: str = "factual",
) -> dict[str, list[str]]:
    defaults = _build_default_tool_queries(
        original_query,
        term_matches,
        significant_words=significant_words,
        question_type=question_type,
    )
    queries: dict[str, list[str]] = {tool: list(defaults.get(tool, [])) for tool in ALLOWED_TOOLS}
    if not isinstance(raw, dict):
        return queries

    for tool_name in ALLOWED_TOOLS:
        values = raw.get(tool_name, [])
        if isinstance(values, str):
            values = [part.strip() for part in values.split(",") if part.strip()]
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for value in values:
            text = re.sub(r"\s+", " ", str(value).strip())
            if text and text not in cleaned:
                cleaned.append(text)
        if cleaned:
            merged = [*queries[tool_name], *cleaned]
            queries[tool_name] = list(dict.fromkeys(merged))[:MAX_QUERIES_PER_TOOL]

    if term_matches.exact_phrase_terms:
        primary = primary_exact_phrase(term_matches.exact_phrase_terms)
        for tool_name in ("payload", "vector"):
            prioritized = _phrase_context_queries(original_query, primary)
            merged = [*prioritized, *queries[tool_name]]
            queries[tool_name] = list(dict.fromkeys(merged))[:MAX_QUERIES_PER_TOOL]

    return queries


def _query_mentions_pue(query: str) -> bool:
    return normalize_catalog_text(PUE_ALIAS_KEY) in normalize_catalog_text(query)


def _sanitize_llm_plan_fields(
    payload: dict[str, Any],
    *,
    original_query: str,
    enable_pue_aliases: bool,
    term_matches: QueryTermMatches,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    query_norm = normalize_catalog_text(original_query)
    mentions_pue = _query_mentions_pue(original_query)

    significant_raw = payload.get("significant_words", [])
    if isinstance(significant_raw, str):
        significant_words = [part.strip() for part in significant_raw.split(",") if part.strip()]
    elif isinstance(significant_raw, list):
        significant_words = [str(item).strip() for item in significant_raw if str(item).strip()]
    else:
        significant_words = []

    blocked = {normalize_catalog_text(PUE_ALIAS_KEY), "пуэ", "снн", "безопасное напряжение"}
    if not enable_pue_aliases:
        blocked.update(
            {
                normalize_catalog_text("правила устройства электроустановок"),
                normalize_catalog_text("электроустановок"),
            }
        )

    cleaned_significant: list[str] = []
    for word in significant_words:
        word_norm = normalize_catalog_text(word)
        if word_norm in blocked and not mentions_pue:
            warnings.append(f"removed llm significant_word not present in query: {word!r}")
            continue
        if term_matches.exact_phrase_terms:
            phrase_norms = [normalize_catalog_text(item) for item in term_matches.exact_phrase_terms]
            if word_norm in LOOSE_SINGLE_WORDS and not any(word_norm in phrase for phrase in phrase_norms):
                warnings.append(f"removed loose llm term in favor of exact phrase: {word!r}")
                continue
        cleaned_significant.append(word)
    payload["significant_words"] = cleaned_significant

    resolved = payload.get("resolved_doc_names", [])
    if isinstance(resolved, str):
        resolved = [part.strip() for part in resolved.split(",") if part.strip()]
    if isinstance(resolved, list):
        filtered_docs: list[str] = []
        for doc_name in resolved:
            if is_pue_document_name(str(doc_name)) and not mentions_pue and not enable_pue_aliases:
                warnings.append(f"removed llm resolved_doc_name without explicit ПУЭ mention: {doc_name!r}")
                continue
            filtered_docs.append(str(doc_name))
        payload["resolved_doc_names"] = filtered_docs

    tool_queries = payload.get("tool_queries")
    if isinstance(tool_queries, dict) and term_matches.exact_phrase_terms:
        primary = term_matches.exact_phrase_terms[0]
        for tool_name in ("payload", "vector"):
            values = tool_queries.get(tool_name, [])
            if isinstance(values, str):
                values = [part.strip() for part in values.split(",") if part.strip()]
            if not isinstance(values, list):
                continue
            sanitized: list[str] = []
            for value in values:
                value_norm = normalize_catalog_text(str(value))
                if value_norm in blocked and primary and value_norm != normalize_catalog_text(primary):
                    warnings.append(f"removed llm {tool_name} query replaced by exact phrase")
                    continue
                sanitized.append(str(value).strip())
            prioritized = _phrase_context_queries(original_query, primary)
            payload["tool_queries"][tool_name] = list(dict.fromkeys([*prioritized, *sanitized]))[
                :MAX_QUERIES_PER_TOOL
            ]

    if query_norm and not mentions_pue:
        payload.setdefault("warnings", [])
        if isinstance(payload["warnings"], list):
            payload["warnings"] = list(payload["warnings"])

    return payload, warnings


def _payload_required_tokens(term_matches: QueryTermMatches) -> tuple[str, ...]:
    primary = primary_exact_phrase(term_matches.exact_phrase_terms)
    if not primary:
        return ()
    return phrase_required_tokens(primary)


def _build_tool_query_objects(
    tool_queries: dict[str, list[str]],
    *,
    point_number_hints: list[str],
    resolved_doc_names: list[str],
    term_matches: QueryTermMatches,
    question_type: str = "factual",
) -> tuple[tuple[str, ...], tuple[PreparedToolQuery, ...]]:
    selected: list[str] = []
    prepared: list[PreparedToolQuery] = []
    required_tokens = _payload_required_tokens(term_matches)

    if point_number_hints and resolved_doc_names:
        selected.append("point")
        prepared.append(PreparedToolQuery(tool_name="point", queries=tuple(point_number_hints)))

    for tool_name in ("payload", "vector"):
        queries = [query for query in tool_queries.get(tool_name, []) if query.strip()]
        if not queries:
            continue
        selected.append(tool_name)
        prepared.append(
            PreparedToolQuery(
                tool_name=tool_name,
                queries=tuple(queries),
                required_tokens=required_tokens if tool_name == "payload" else (),
            )
        )

    if not selected:
        fallback_query = tool_queries.get("vector", []) or tool_queries.get("payload", [])
        query = fallback_query[0] if fallback_query else ""
        if query:
            selected = ["payload", "vector"]
            prepared = [
                PreparedToolQuery(
                    tool_name="payload",
                    queries=(query,),
                    required_tokens=required_tokens,
                ),
                PreparedToolQuery(tool_name="vector", queries=(query,)),
            ]

    if question_type == "document_lookup" and any(entry.tool_name == "payload" for entry in prepared):
        prepared = [entry for entry in prepared if entry.tool_name in {"payload", "point"}]
        selected = [entry.tool_name for entry in prepared]

    return tuple(dict.fromkeys(selected)), tuple(prepared)


def _normalize_llm_payload(
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    allowed_ids = {str(item["catalog_id"]) for item in candidates}
    catalog_by_id = {str(item["catalog_id"]): item for item in candidates}

    raw_ids = payload.get("selected_catalog_ids", [])
    if isinstance(raw_ids, str):
        raw_ids = [part.strip() for part in raw_ids.split(",") if part.strip()]
    if not isinstance(raw_ids, list):
        raise ValueError("selected_catalog_ids must be a list or string")

    selected_ids: list[str] = []
    for entry in raw_ids:
        catalog_id = str(entry).strip()
        if catalog_id in allowed_ids and catalog_id not in selected_ids:
            selected_ids.append(catalog_id)
        elif catalog_id:
            warnings.append(f"ignored unknown catalog_id: {catalog_id!r}")

    concepts_raw = payload.get("concepts", [])
    if isinstance(concepts_raw, str):
        concepts = [part.strip() for part in concepts_raw.split(",") if part.strip()]
    elif isinstance(concepts_raw, list):
        concepts = [str(item).strip() for item in concepts_raw if str(item).strip()]
    else:
        concepts = []

    significant_raw = payload.get("significant_words", [])
    if isinstance(significant_raw, str):
        significant_words = [part.strip() for part in significant_raw.split(",") if part.strip()]
    elif isinstance(significant_raw, list):
        significant_words = [str(item).strip() for item in significant_raw if str(item).strip()]
    else:
        significant_words = []

    point_hints_raw = payload.get("point_number_hints", [])
    if isinstance(point_hints_raw, str):
        point_number_hints = [part.strip() for part in point_hints_raw.split(",") if part.strip()]
    elif isinstance(point_hints_raw, list):
        point_number_hints = [str(item).strip() for item in point_hints_raw if str(item).strip()]
    else:
        point_number_hints = []

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
        warnings.append("invalid confidence value; defaulted to 0.0")

    llm_warnings = payload.get("warnings", [])
    if isinstance(llm_warnings, list):
        warnings.extend(str(item) for item in llm_warnings)
    elif isinstance(llm_warnings, str) and llm_warnings.strip():
        warnings.append(llm_warnings.strip())

    resolved_doc_names = [
        str(catalog_by_id[catalog_id]["doc_name"])
        for catalog_id in selected_ids
        if catalog_id in catalog_by_id
    ]
    return {
        "question_type": str(payload.get("question_type") or "factual"),
        "answer_shape": str(payload.get("answer_shape") or "narrow"),
        "concepts": concepts,
        "significant_words": significant_words,
        "resolved_doc_names": resolved_doc_names,
        "point_number_hints": point_number_hints,
        "confidence": max(0.0, min(1.0, confidence)),
        "tool_queries": payload.get("tool_queries", {}),
    }, warnings


def _llm_plan(
    query: str,
    candidates: list[dict[str, Any]],
    matched_terms: list[str],
    *,
    llm_provider: str,
    keys_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    pack = load_prompt_pack_by_role("query_planning")
    profile = resolve_role_profile(llm_provider, "query_planning")
    payload = chat_json_with_model_fallback(
        llm_provider,
        resolve_role_models(llm_provider, "query_planning"),
        keys_path=keys_path,
        system_prompt=str(pack.get("prompt") or ""),
        user_payload={
            "query": query,
            "candidates": candidates,
            "matched_terms": matched_terms,
            "output_contract": pack.get("output_contract"),
        },
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
    )
    return _normalize_llm_payload(payload, candidates)


def prepare_query(
    query: str,
    *,
    catalog: DocumentCatalog,
    knowledge: DocumentKnowledgeIndex,
    filters: dict[str, Any] | None = None,
    mode: str = "auto",
    llm_provider: str = "none",
    keys_path: Path | None = None,
    enable_pue_aliases: bool = False,
) -> PreparedQueryPlan:
    original_query = (query or "").strip()
    if mode == "off" or not original_query:
        return PreparedQueryPlan(
            original_query=original_query,
            trace=QueryPlannerTrace(mode="off", resolver="none"),
        )

    explicit_doc_name = str((filters or {}).get("doc_name") or "").strip()
    point_hint = extract_point_number_hint(original_query)
    point_number_hints = [point_hint] if point_hint else []
    term_matches = match_query_terms(
        original_query,
        knowledge,
        enable_pue_aliases=enable_pue_aliases,
    )
    matched_terms = term_matches.flat_terms()

    catalog_candidates = find_catalog_candidates(
        original_query,
        catalog,
        explicit_doc_name=explicit_doc_name,
        limit=8,
        enable_pue_aliases=enable_pue_aliases,
    )
    knowledge_candidates = find_knowledge_candidates(
        original_query,
        knowledge,
        limit=12,
        enable_pue_aliases=enable_pue_aliases,
    )
    candidates = _merge_candidates(catalog, catalog_candidates, knowledge_candidates)

    resolver = "deterministic"
    warnings: list[str] = []
    resolved_doc_names: list[str] = []
    confidence = 0.0
    ambiguous = False
    question_type = "point_lookup" if point_number_hints else detect_query_intent(original_query)
    answer_shape = "narrow"
    concepts: list[str] = list(
        dict.fromkeys([*term_matches.exact_phrase_terms, *term_matches.abbreviation_expansions[:4]])
    )[:8]
    significant_words: list[str] = list(
        dict.fromkeys(
            [
                *term_matches.exact_phrase_terms,
                *term_matches.document_hints,
                *term_matches.abbreviation_expansions,
                *term_matches.loose_terms,
            ]
        )
    )[:12]
    tool_queries: dict[str, list[str]] = {tool: [] for tool in ALLOWED_TOOLS}

    if mode == "llm" and llm_provider != "none" and candidates:
        resolver = "llm"
        try:
            llm_data, llm_warnings = _llm_plan(
                original_query,
                candidates,
                matched_terms,
                llm_provider=llm_provider,
                keys_path=keys_path,
            )
            llm_data, sanitize_warnings = _sanitize_llm_plan_fields(
                llm_data,
                original_query=original_query,
                enable_pue_aliases=enable_pue_aliases,
                term_matches=term_matches,
            )
            warnings.extend(llm_warnings)
            warnings.extend(sanitize_warnings)
            question_type = llm_data.get("question_type", question_type)
            answer_shape = llm_data.get("answer_shape", answer_shape)
            concepts = list(dict.fromkeys([*concepts, *llm_data.get("concepts", [])]))
            significant_words = list(
                dict.fromkeys([*significant_words, *llm_data.get("significant_words", [])])
            )
            point_number_hints = list(
                dict.fromkeys(point_number_hints + llm_data.get("point_number_hints", []))
            )
            confidence = float(llm_data.get("confidence", 0.0))
            candidate_names = llm_data.get("resolved_doc_names", [])
            if confidence >= MIN_DOC_CONFIDENCE and len(candidate_names) == 1:
                resolved_doc_names = candidate_names
            elif candidate_names:
                ambiguous = len(candidate_names) > 1
                warnings.append(
                    "llm document resolution below threshold or ambiguous; doc_name filter not applied"
                )
            else:
                warnings.append("llm returned no verified document; doc_name filter not applied")
            tool_queries = _normalize_tool_queries(
                llm_data.get("tool_queries"),
                original_query,
                term_matches=term_matches,
                significant_words=significant_words,
                question_type=question_type,
            )
        except Exception as exc:
            warnings.append(f"llm query planning failed: {type(exc).__name__}: {exc}")
            resolved_doc_names, confidence, ambiguous, det_warnings = _deterministic_resolve(candidates)
            warnings.extend(det_warnings)
            resolver = "deterministic_fallback"
            tool_queries = _normalize_tool_queries(
                {},
                original_query,
                term_matches=term_matches,
                significant_words=significant_words,
                question_type=question_type,
            )
    else:
        resolved_doc_names, confidence, ambiguous, det_warnings = _deterministic_resolve(candidates)
        warnings.extend(det_warnings)
        if resolved_doc_names and not _query_mentions_pue(original_query) and not enable_pue_aliases:
            if any(is_pue_document_name(name) for name in resolved_doc_names):
                warnings.append(
                    "removed deterministic ПУЭ doc filter without explicit mention or enable_pue_aliases"
                )
                resolved_doc_names = []
                ambiguous = True
        explicit_doc_hint = _query_mentions_pue(original_query) or bool(term_matches.document_hints)
        if resolved_doc_names and _should_skip_title_phrase_doc_filter(term_matches, resolved_doc_names):
            warnings.append(
                "resolved document title equals query phrase; doc_name filter not applied"
            )
            resolved_doc_names = []
            ambiguous = True
        elif resolved_doc_names and (
            (
                _topic_alias_ambiguous(candidates, term_matches)
                and not (enable_pue_aliases and explicit_doc_hint)
            )
            or _should_skip_topic_alias_doc_filter(
                candidates,
                term_matches,
                resolved_doc_names,
                point_number_hints=point_number_hints,
                enable_pue_aliases=enable_pue_aliases,
                original_query=original_query,
            )
        ):
            warnings.append(
                "exact phrase resolved via topic alias only; doc_name filter not applied"
            )
            resolved_doc_names = []
            ambiguous = True
        tool_queries = _normalize_tool_queries(
            {},
            original_query,
            term_matches=term_matches,
            significant_words=significant_words,
            question_type=question_type,
        )

    selected_tools, prepared_tool_queries = _build_tool_query_objects(
        tool_queries,
        point_number_hints=point_number_hints,
        resolved_doc_names=resolved_doc_names,
        term_matches=term_matches,
        question_type=question_type,
    )

    top_candidate = candidates[0] if candidates else {}
    document_resolution = DocumentResolution(
        catalog_id=str(top_candidate.get("catalog_id") or ""),
        doc_name=str(resolved_doc_names[0]) if resolved_doc_names else str(top_candidate.get("doc_name") or ""),
        confidence=confidence,
        ambiguous=ambiguous,
    )

    return PreparedQueryPlan(
        original_query=original_query,
        question_type=question_type,
        answer_shape=answer_shape,
        exact_phrase_terms=tuple(term_matches.exact_phrase_terms),
        concepts=tuple(concepts),
        significant_words=tuple(significant_words),
        document_resolution=document_resolution,
        resolved_doc_names=tuple(resolved_doc_names),
        point_number_hints=tuple(point_number_hints),
        selected_tools=selected_tools,
        tool_queries=prepared_tool_queries,
        confidence=confidence,
        ambiguous=ambiguous,
        warnings=tuple(warnings),
        trace=QueryPlannerTrace(
            mode=mode,
            resolver=resolver,
            knowledge_source=knowledge.source_path,
            catalog_source=catalog.source_path,
            candidates_total=len(candidates),
        ),
        candidates=tuple(candidates),
    )


def apply_prepared_plan(
    query: str,
    filters: dict[str, Any] | None,
    plan: PreparedQueryPlan,
) -> tuple[str, dict[str, Any]]:
    merged_filters = dict(filters or {})
    if plan.resolved_doc_names and not plan.ambiguous and len(plan.resolved_doc_names) == 1:
        merged_filters["doc_name"] = plan.resolved_doc_names[0]
    if plan.point_number_hints and "point_number" not in merged_filters:
        merged_filters["point_number"] = plan.point_number_hints[0]
    effective_query = plan.original_query or query
    return effective_query, merged_filters


def prepared_plan_to_understanding(plan: PreparedQueryPlan) -> QueryUnderstandingResult:
    search_query = plan.original_query
    if plan.tool_queries:
        for entry in plan.tool_queries:
            if entry.tool_name == "vector" and entry.queries:
                search_query = entry.queries[0]
                break
            if entry.tool_name == "payload" and entry.queries:
                search_query = entry.queries[0]
                break

    return QueryUnderstandingResult(
        original_query=plan.original_query,
        search_query=search_query,
        document_hints=list(plan.significant_words),
        resolved_doc_names=list(plan.resolved_doc_names),
        point_number_hints=list(plan.point_number_hints),
        tool_hints=list(plan.selected_tools),
        confidence=plan.confidence,
        ambiguous=plan.ambiguous,
        warnings=list(plan.warnings),
        trace=QueryUnderstandingTrace(
            mode=plan.trace.mode,
            resolver=plan.trace.resolver,
            catalog_source=plan.trace.catalog_source,
            candidates_total=plan.trace.candidates_total,
        ),
        candidates=list(plan.candidates),
    )


def load_default_knowledge() -> DocumentKnowledgeIndex:
    return load_document_knowledge()


def plan_query(
    query: str,
    *,
    catalog: DocumentCatalog | None = None,
    knowledge: DocumentKnowledgeIndex | None = None,
    filters: dict[str, Any] | None = None,
    mode: str = "auto",
    llm_provider: str = "none",
    keys_path: Path | None = None,
    project_paths: ProjectPaths | None = None,
    enable_pue_aliases: bool | None = None,
) -> PreparedQueryPlan:
    from mr_norm.config.pue_aliases import resolve_enable_pue_aliases

    paths = project_paths or ProjectPaths.from_root(None)
    return prepare_query(
        query,
        catalog=catalog or load_default_document_catalog(paths),
        knowledge=knowledge or load_default_knowledge(),
        filters=filters,
        mode=mode,
        llm_provider=llm_provider,
        keys_path=keys_path,
        enable_pue_aliases=resolve_enable_pue_aliases(enable_pue_aliases),
    )
