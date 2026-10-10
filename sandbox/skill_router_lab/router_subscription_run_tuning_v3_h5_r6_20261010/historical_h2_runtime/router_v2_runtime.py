"""Semantic draft v2 dry-run orchestration using an injected adapter."""
from __future__ import annotations

import json
import re
from typing import Any

from router_contracts import RouterPlanConfig, SkillQueryPlan
from router_draft import DraftValidationError, assemble_skill_query_plan, build_source_manifest, validate_semantic_draft
from router_prompts_v2 import CLASSIFIER_V2_SYSTEM, PARSER_V2_SYSTEM, REPAIR_V2_SUFFIX


def _metadata(raw: Any, requested_model: str) -> tuple[str, dict[str, Any]]:
    if isinstance(raw, str):
        raise ValueError("v2 adapter must return raw content plus capability metadata")
    elif isinstance(raw, dict):
        content = raw.get("content", raw.get("text"))
        metadata = raw.get("metadata", raw)
    else:
        content = getattr(raw, "content", None)
        metadata = getattr(raw, "metadata", {}) or {}
    if not isinstance(content, str):
        raise ValueError("adapter must return raw textual content")
    fields = ("transport_name", "authentication_mode", "supports_json_schema", "capability_evidence", "requested_model", "actual_model", "effort", "usage", "physical_request_count")
    if not isinstance(metadata, dict) or set(fields) - set(metadata):
        raise ValueError("v2 adapter metadata is incomplete")
    result = {key: metadata.get(key) for key in fields} if isinstance(metadata, dict) else {key: None for key in fields}
    result["requested_model"] = result.get("requested_model") or requested_model
    if result["requested_model"] != requested_model:
        raise ValueError("adapter requested_model metadata mismatch")
    actual = result.get("actual_model")
    if actual is not None and actual != requested_model:
        raise ValueError("adapter actual_model metadata mismatch")
    if not isinstance(result["supports_json_schema"], bool):
        raise ValueError("supports_json_schema metadata must be boolean")
    if not isinstance(result["transport_name"], str) or not result["transport_name"] or not isinstance(result["authentication_mode"], str) or not result["authentication_mode"] or not isinstance(result["capability_evidence"], str) or not result["capability_evidence"] or not isinstance(result["effort"], str) or not result["effort"]:
        raise ValueError("v2 adapter capability metadata values must be explicit")
    if result["physical_request_count"] is not None and (not isinstance(result["physical_request_count"], int) or result["physical_request_count"] < 0):
        raise ValueError("physical_request_count must be a nonnegative integer or null")
    return content, result


def _adapter_call(adapter: Any, role: str, model: str, system: str, payload: dict[str, Any], max_tokens: int, timeout: float) -> tuple[str, dict[str, Any]]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    if callable(adapter):
        raw = adapter(role=role, messages=messages, model=model, timeout=timeout, max_tokens=max_tokens)
    elif hasattr(adapter, "complete"):
        raw = adapter.complete(role=role, messages=messages, model=model, timeout=timeout, max_tokens=max_tokens)
    else:
        raise TypeError("adapter must be callable or expose complete")
    return _metadata(raw, model)


def _json(raw: str) -> Any:
    return json.loads(raw)


def _prepare_vocab(vocab: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    groups = []
    surfaces = []
    for key in ("exact_terms", "morphology_variants", "abbreviation_variants", "loose_terms"):
        surfaces.extend(str(value) for value in vocab.get(key, []) if str(value).strip())
    for group in vocab.get("abbreviation_groups", []):
        for expansion in group.get("expansions", []):
            surface = str(expansion).strip()
            if not surface:
                continue
            abbreviation = str(group.get("abbreviation", "")).strip()
            slug = "_".join(re.findall(r"[\w]+", surface.casefold()))[:48] or "meaning"
            mid = f"abbr:{abbreviation.casefold()}:{slug}"
            groups.append({"meaning_id": mid, "label": f"{abbreviation} — {surface}" if abbreviation else surface,
                           "surface": surface, "variants": list(dict.fromkeys(x for x in (abbreviation, surface) if x))})
    ids = {row["meaning_id"] for row in groups}
    return {**vocab, "meaning_groups": groups}, ids


def plan_skill_query_v2(query: str, *, dialog_context: Any = None, pending_clarification: Any = None, catalog: Any, validation_catalog: Any = None, knowledge: Any, llm_adapter: Any, config: RouterPlanConfig | dict[str, Any]) -> SkillQueryPlan:
    """Plan with v2 semantic draft; no skills or retrieval are executed."""
    # Reuse the established context/catalog/vocabulary adapters without changing baseline APIs.
    from router_planning import (
        _plain, _validate_pending_clarification, _context_snapshot, _catalog_entries,
        _vocabulary, _open_pending_value_is_grounded, _validate_pending_result, _pending_answer_matches,
    )
    cfg = config if isinstance(config, RouterPlanConfig) else RouterPlanConfig(**config)
    trace: dict[str, Any] = {"schema_version": "skill_query_plan.v2", "adapter_metadata": [], "calls": [], "logical_call_count": 0, "physical_call_count": None, "repair_count": 0}
    if not isinstance(query, str) or not query.strip():
        return SkillQueryPlan("skill_query_plan.v2", query if isinstance(query, str) else "", "error", None, issues=[{"code": "empty_query", "task_ids": [], "message": "query must be nonempty"}], trace=trace)
    if len(query) > cfg.max_query_chars:
        return SkillQueryPlan("skill_query_plan.v2", query, "error", None, issues=[{"code": "input_limit_exceeded", "task_ids": [], "message": "query exceeds configured limit"}], trace=trace)
    try:
        pending = _validate_pending_clarification(pending_clarification, dialog_context)
        selected_option = _pending_answer_matches(query, pending)
        context = _context_snapshot(dialog_context, pending)
        rows = _catalog_entries(catalog)
        validation_rows = _catalog_entries(validation_catalog) if validation_catalog is not None else rows
        valid_doc_ids = {str(row.get("doc_id")) for row in validation_rows if row.get("doc_id")}
        if pending is not None and pending["original_task"].get("doc_id") and pending["original_task"]["doc_id"] not in valid_doc_ids:
            raise ValueError("pending clarification original_task doc_id is absent from catalog")
        for turn in context.get("turns", []):
            doc_id = turn.get("resolved_doc_id", turn.get("doc_id"))
            if doc_id and doc_id not in valid_doc_ids:
                raise ValueError(f"context doc_id is absent from catalog: {doc_id}")
        search_basis = f"{pending['original_query']} {query}" if pending else query
        base_vocab = _vocabulary(search_basis, knowledge)
        vocab, meaning_ids = _prepare_vocab(base_vocab)
        from mr_norm.retrieval.document_catalog import find_catalog_candidates
        candidates = []
        if catalog is not None:
            entries_by_id = {str(row.get("catalog_id")): row for row in rows}
            for candidate in find_catalog_candidates(search_basis, catalog, limit=8):
                row = entries_by_id.get(str(candidate.catalog_id), {})
                candidates.append({**candidate.to_dict(), "doc_id": row.get("doc_id") or None})
        source_context = {**context, "turns": [turn for turn in context.get("turns", []) if turn.get("turn_id") != "pending_original"]}
        source_manifest = build_source_manifest(query, source_context, pending)
    except Exception as exc:
        message = str(exc)
        code = "invalid_context_doc_id" if message.startswith("context doc_id is absent from catalog:") else "preparation_error"
        return SkillQueryPlan("skill_query_plan.v2", query, "error", None, issues=[{"code": code, "task_ids": [], "message": message}], trace=trace)
    candidate_ids = {str(row.get("doc_id")) for row in candidates if row.get("doc_id")}
    payload = {"original_query": query, "context": context, "pending_original_query": pending.get("original_query") if pending else None,
               "pending_original_task": pending.get("original_task") if pending else None, "source_manifest": source_manifest,
               "document_candidates": candidates, "term_candidates": vocab, "max_tasks": cfg.max_tasks}
    if pending:
        payload["pending_resolution"] = {"selected_option_id": selected_option, "open": not bool(pending["options"])}
    if len(json.dumps(payload, ensure_ascii=False)) > cfg.max_context_chars:
        return SkillQueryPlan("skill_query_plan.v2", query, "error", None, issues=[{"code": "input_limit_exceeded", "task_ids": [], "message": "serialized inputs exceed configured limit"}], trace=trace)
    def physical_count() -> int | None:
        if len(trace["adapter_metadata"]) != trace["logical_call_count"]:
            return None
        values = [m.get("physical_request_count") for m in trace["adapter_metadata"]]
        return sum(values) if all(value is not None for value in values) else None

    try:
        trace["logical_call_count"] += 1
        raw, meta = _adapter_call(llm_adapter, "classifier", cfg.light_model, CLASSIFIER_V2_SYSTEM, payload, cfg.classifier_max_tokens, cfg.timeout_sec)
        trace["calls"].append({"role": "classifier", "raw_reply": raw, "metadata": meta})
        trace["adapter_metadata"].append(meta)
        classifier = _json(raw)
        if not isinstance(classifier, dict) or set(classifier) != {"complexity"} or classifier["complexity"] not in {"simple", "complex"}:
            raise DraftValidationError("classifier must return only simple or complex complexity")
        complexity = classifier["complexity"]
    except Exception as exc:
        trace["physical_call_count"] = physical_count()
        return SkillQueryPlan("skill_query_plan.v2", query, "error", None, issues=[{"code": "classifier_error", "task_ids": [], "message": str(exc)}], trace=trace)
    role, model = ("simple_parser", cfg.light_model) if complexity == "simple" else ("complex_parser", cfg.heavy_model)
    parser_payload = {**payload, "classifier": classifier}
    if len(json.dumps(parser_payload, ensure_ascii=False)) > cfg.max_context_chars:
        trace["physical_call_count"] = physical_count()
        return SkillQueryPlan("skill_query_plan.v2", query, "error", complexity, issues=[{"code": "input_limit_exceeded", "task_ids": [], "message": "parser input exceeds configured limit"}], trace=trace)
    replace_ids = {condition["id"] for condition in context.get("confirmed_conditions", [])}
    raw = ""
    for attempt in range(2):
        system = PARSER_V2_SYSTEM if attempt == 0 else PARSER_V2_SYSTEM + "\n" + REPAIR_V2_SUFFIX
        if attempt:
            parser_payload = {**payload, "classifier": classifier, "invalid_response": raw, "validation_errors": validation_errors}
            if len(json.dumps(parser_payload, ensure_ascii=False)) > cfg.max_context_chars:
                trace["physical_call_count"] = physical_count()
                return SkillQueryPlan("skill_query_plan.v2", query, "error", complexity, issues=[{"code": "input_limit_exceeded", "task_ids": [], "message": "repair context exceeds configured limit"}], trace=trace)
        try:
            trace["logical_call_count"] += 1
            raw, meta = _adapter_call(llm_adapter, role, model, system, parser_payload, cfg.parser_max_tokens, cfg.timeout_sec)
            trace["calls"].append({"role": role, "attempt": attempt + 1, "raw_reply": raw, "metadata": meta})
            trace["adapter_metadata"].append(meta)
        except Exception as exc:
            trace["physical_call_count"] = physical_count()
            return SkillQueryPlan("skill_query_plan.v2", query, "error", complexity, issues=[{"code": "parser_transport_error", "task_ids": [], "message": str(exc)}], trace=trace)
        try:
            draft = _json(raw)
            draft = validate_semantic_draft(draft, source_manifest, max_tasks=cfg.max_tasks, candidate_ids=candidate_ids, term_ids=meaning_ids, replace_ids=replace_ids)
            result = assemble_skill_query_plan(query, complexity, draft, source_manifest, catalog_rows=validation_rows, vocab=vocab, trace=trace)
            if pending and selected_option is not None:
                _validate_pending_result(pending, selected_option, result.tasks, result.clarification)
            elif pending and not pending["options"] and _open_pending_value_is_grounded(query, pending, result.tasks, rows, candidate_ids, result.clarification):
                _validate_pending_result(pending, None, result.tasks, result.clarification, open_resolution=True)
            elif pending:
                code = {"document": "ambiguous_document", "term": "ambiguous_term", "point": "missing_point", "scope": "ambiguous_scope", "intent": "ambiguous_intent"}[pending["field"]]
                result.tasks = []
                result.status = "needs_clarification"
                result.clarification = {"question": pending["question"], "target_task_ids": pending.get("target_task_ids", []), "field": pending["field"], "options": pending["options"]}
                result.issues.append({"code": code, "task_ids": [], "message": "the answer did not uniquely resolve the saved pending clarification"})
            total_physical = physical_count()
            trace["physical_call_count"] = total_physical
            # The assembler snapshots the trace; publish the final aggregate on
            # that returned snapshot as well as on the working trace.
            result.trace["physical_call_count"] = total_physical
            return result
        except Exception as exc:
            if attempt:
                break
            validation_errors = [str(exc)]
            trace["repair_count"] = 1
    trace["physical_call_count"] = physical_count()
    return SkillQueryPlan("skill_query_plan.v2", query, "error", complexity, issues=[{"code": "parser_error", "task_ids": [], "message": validation_errors[0] if validation_errors else "parser failed"}], trace=trace)
