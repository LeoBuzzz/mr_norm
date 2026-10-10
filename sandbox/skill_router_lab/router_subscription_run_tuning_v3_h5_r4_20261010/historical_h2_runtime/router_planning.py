"""Two-call LLM orchestration and fail-closed validation for the router lab."""
from __future__ import annotations

import json
import hashlib
import copy
import re
import time
from dataclasses import asdict, is_dataclass
from typing import Any

from router_contracts import RouterPlanConfig, SkillQueryPlan
from router_prompts import CLASSIFIER_SYSTEM, PARSER_SYSTEM

SCHEMA_VERSION = "skill_query_plan.v1"
SKILLS = {"exact_point", "corpus_point_search", "find_all"}
REASONS = {"single_intent", "multiple_intents", "multiple_targets", "dependency", "comparison", "context_correction", "ambiguity", "unsupported_intent", "uncertain"}
ISSUE_CODES = {"ambiguous_document", "ambiguous_term", "missing_point", "ambiguous_scope", "ambiguous_intent", "unsupported_intent", "dependency_execution_not_in_stage1"}
TASK_KEYS = {"task_id", "intent_text", "evidence", "skill", "query", "scope", "doc_id", "document_mention", "point_ref", "coverage", "concepts", "constraints", "depends_on", "replaces"}
ISSUE_KEYS = {"code", "task_ids", "message"}


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(x) for x in value]
    return value


def _validate_pending_clarification(value: Any, dialog_context: Any = None) -> dict[str, Any] | None:
    if value is None:
        return None
    row = copy.deepcopy(_plain(value))
    required = {"question", "field", "options", "original_query", "original_task"}
    if not isinstance(row, dict) or not required <= set(row) or set(row) - (required | {"target_task_ids"}):
        raise ValueError("pending clarification must contain question, field, options, original_query, and original_task only")
    if not isinstance(row["question"], str) or not row["question"].strip() or row["field"] not in {"document", "term", "point", "scope", "intent"}:
        raise ValueError("pending clarification has invalid question or field")
    if not isinstance(row["original_query"], str) or not row["original_query"].strip():
        raise ValueError("pending clarification original_query must be nonempty")
    task = row["original_task"]
    task_keys = {"intent_text", "skill", "query", "scope", "doc_id", "document_mention", "point_ref", "coverage", "concepts", "constraints", "semantic_elements"}
    if not isinstance(task, dict) or not {"intent_text", "skill", "coverage", "semantic_elements"} <= set(task) or set(task) - task_keys:
        raise ValueError("pending clarification original_task must include intent_text, skill, coverage, and semantic_elements")
    if not isinstance(task["intent_text"], str) or not task["intent_text"].strip() or (task["skill"] not in SKILLS and task["skill"] is not None) or task["coverage"] not in {"exact", "ranked", "comprehensive"}:
        raise ValueError("pending clarification original_task has invalid intent, skill, or coverage")
    if task.get("point_ref") is not None and (not isinstance(task["point_ref"], str) or not task["point_ref"].strip()):
        raise ValueError("pending clarification point_ref must be a nonempty string or null")
    for key in ("doc_id", "document_mention", "query", "scope"):
        if key in task and task[key] is not None and not isinstance(task[key], str):
            raise ValueError(f"pending clarification original_task {key} must be a string or null")
    if task.get("scope") is not None and task["scope"] not in {"corpus", "document", "unresolved"}:
        raise ValueError("pending clarification original_task has invalid scope")
    for key in ("concepts", "constraints", "semantic_elements"):
        if key in task and not isinstance(task[key], list):
            raise ValueError(f"pending clarification original_task {key} must be an array")
    for constraint in task.get("constraints", []):
        if not isinstance(constraint, dict) or not {"kind", "text", "polarity"} <= set(constraint) or constraint["kind"] not in {"object", "action", "condition", "quantity", "exclusion"} or constraint["polarity"] not in {"include", "exclude"} or not isinstance(constraint["text"], str) or not constraint["text"].strip():
            raise ValueError("pending clarification original_task contains malformed constraints")
    for element in task.get("semantic_elements", []):
        if not isinstance(element, dict) or not {"label", "variants"} <= set(element) or not isinstance(element["label"], str) or not element["label"].strip() or not isinstance(element["variants"], list) or not element["variants"] or any(not isinstance(value, str) or not value.strip() for value in element["variants"]):
            raise ValueError("pending clarification original_task contains malformed semantic_elements")
    if not task["semantic_elements"]:
        raise ValueError("pending clarification original_task semantic_elements must preserve the prior topic")
    sources = [row["original_query"]]
    raw_context = _plain(dialog_context) or {}
    if isinstance(raw_context, dict):
        sources.extend(str(turn.get("user_text", turn.get("user", ""))) for turn in raw_context.get("turns", []) if isinstance(turn, dict))
    source_text = query_normalized(" ".join(sources)).casefold()
    source_tokens = re.findall(r"\w+", source_text)
    for constraint in task.get("constraints", []):
        quote = constraint.get("quote")
        grounded_text = query_normalized(constraint["text"]).casefold()
        grounded_quote = query_normalized(quote).casefold() if isinstance(quote, str) else ""
        constraint_tokens = re.findall(r"\w+", grounded_text)
        constraint_grounded = bool(constraint_tokens) and all(
            any(
                token == source_token
                or not token.isdigit() and not source_token.isdigit()
                and min(len(token), len(source_token)) >= 4
                and token[:min(len(token), len(source_token), 7)] == source_token[:min(len(token), len(source_token), 7)]
                for source_token in source_tokens
            )
            for token in constraint_tokens
        )
        if (grounded_quote and grounded_quote not in source_text) or (grounded_text not in source_text and not constraint_grounded):
            raise ValueError("pending clarification constraint is not grounded in original_query")
    for element in task["semantic_elements"]:
        variants = [query_normalized(v).casefold() for v in element["variants"]]
        if not any(v and v in source_text for v in variants):
            raise ValueError("pending clarification semantic element is not grounded in original_query")
    if not isinstance(row["options"], list) or len(row["options"]) > 5:
        raise ValueError("pending clarification options must be an array of at most 5 entries")
    for option in row["options"]:
        if not isinstance(option, dict) or set(option) != {"id", "label"} or not all(isinstance(option[key], str) and option[key].strip() for key in ("id", "label")):
            raise ValueError("pending clarification option must contain nonempty id and label")
    if len({option["id"] for option in row["options"]}) != len(row["options"]):
        raise ValueError("pending clarification option IDs must be unique")
    if row["field"] == "document" and any(re.fullmatch(r"doc_[0-9a-f]{16}", option["id"]) is None for option in row["options"]):
        raise ValueError("pending document option IDs must be canonical doc_id values")
    targets = row.get("target_task_ids", [])
    if not isinstance(targets, list) or any(not isinstance(x, str) or not x for x in targets):
        raise ValueError("pending clarification target_task_ids must be an array of nonempty IDs")
    row["source_turn_id"] = "pending_original"
    return row


def _pending_answer_matches(query: str, pending: dict[str, Any] | None) -> str | None:
    if pending is None:
        return None
    answer = query_normalized(query).casefold()
    matches = []
    for option in pending["options"]:
        for value in (option["id"], option["label"]):
            candidate = query_normalized(value).casefold()
            occurrences = re.finditer(rf"(?<!\w){re.escape(candidate)}(?!\w)", answer) if candidate else ()
            if any(not re.search(r"\bне(?:\s+\w+){0,3}\s*$", answer[:match.start()]) for match in occurrences):
                matches.append(option["id"])
                break
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def query_normalized(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("ё", "е").strip())


def _context_snapshot(dialog_context: Any, pending: Any) -> dict[str, Any]:
    # DialogContext.to_dict intentionally omits internal resolution IDs for its
    # normal LLM history. The plan parser needs verified IDs, so adapt the
    # existing immutable turns directly without changing or persisting them.
    if dialog_context is not None and hasattr(dialog_context, "turns"):
        turns = []
        for i, turn in enumerate(dialog_context.turns):
            def attr(name: str, default: Any = None) -> Any:
                return getattr(turn, name, default)
            turns.append({
                "turn_id": str(attr("turn_id", "") or f"turn_{i + 1}"),
                "user_text": str(attr("user_text", "") or ""),
                "resolved_doc_id": str(attr("resolved_doc_id", "") or ""),
                "resolved_doc_names": list(attr("resolved_doc_names", ()) or ()),
                "point_number_hints": list(attr("point_number_hints", ()) or ()),
                "source_references": [_plain(x) for x in (attr("source_references", ()) or ())],
                "concepts": list(attr("concepts", ()) or ()),
                "confirmed_conditions": list(attr("confirmed_conditions", ()) or ()),
            })
        raw = {"turns": turns}
    else:
        raw = _plain(dialog_context) or {}
    if not isinstance(raw, dict):
        raw = {"value": str(raw)}
    turns = raw.get("turns")
    if turns is None and hasattr(dialog_context, "to_llm_turns"):
        turns = dialog_context.to_llm_turns()
    safe_turns = []
    conditions = []
    seen_turn_ids: set[str] = set()
    for i, turn in enumerate(turns or []):
        row = _plain(turn)
        if not isinstance(row, dict):
            continue
        turn_id = str(row.get("turn_id") or f"turn_{i + 1}")
        if turn_id in seen_turn_ids or turn_id == "current":
            raise ValueError(f"dialog turn_id must be unique and cannot be 'current': {turn_id}")
        seen_turn_ids.add(turn_id)
        safe = {"turn_id": turn_id, "user_text": str(row.get("user_text", row.get("user", "")))}
        for key in ("resolved_doc_id", "doc_id", "resolved_doc_names", "point_number_hints", "source_references", "concepts", "confirmed_conditions"):
            if row.get(key):
                safe[key] = row[key]
        safe_turns.append(safe)
        for j, condition in enumerate(row.get("confirmed_conditions", []) or []):
            if not isinstance(condition, dict) or condition.get("confirmed", True) is not True:
                continue
            text = str(condition.get("text") or "").strip()
            quote = str(condition.get("quote") or "").strip()
            if not text or not quote or quote not in safe["user_text"]:
                raise ValueError(f"confirmed condition {turn_id}:c{j + 1} must have text and an exact source quote")
            conditions.append({"id": str(condition.get("id") or f"{turn_id}:c{j + 1}"), "turn_id": turn_id, "text": text, "quote": quote})
    pending_row = _plain(pending) if pending is not None else None
    if pending_row is not None:
        if "pending_original" in seen_turn_ids:
            raise ValueError("dialog already contains reserved pending_original turn_id")
        safe_turns.append({"turn_id": "pending_original", "user_text": pending_row["original_query"]})
        for i, condition in enumerate(pending_row["original_task"].get("constraints", [])):
            if isinstance(condition, dict) and condition.get("text"):
                quote = condition.get("quote")
                if isinstance(quote, str) and quote and quote in pending_row["original_query"]:
                    conditions.append({"id": str(condition.get("id") or f"pending_original:c{i + 1}"), "turn_id": "pending_original", "text": str(condition["text"]), "quote": quote})
    return {"turns": safe_turns, "confirmed_conditions": conditions, "pending_clarification": pending_row}


def _answer_mentions_value(answer: str, value: str) -> bool:
    normalized = query_normalized(answer).casefold()
    candidate = query_normalized(value).casefold()
    if not candidate:
        return False
    for match in re.finditer(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized):
        prefix = normalized[:match.start()]
        if not re.search(r"\bне(?:\s+\w+){0,3}\s*$", prefix):
            return True
    return False


def _extract_open_point_refs(answer: str) -> set[str]:
    """Extract complete numeric references, treating dotted refs as atomic."""
    normalized = query_normalized(answer).casefold()
    numeric_tokens = list(re.finditer(r"(?<![\w.])\d+(?:[._]\d+)*(?![\w.])", normalized))
    refs: set[str] = set()
    unit_after_number = re.compile(
        r"\s*(?:мвт|квт|вт|мва|ква|мв|кв|в|ка|ма|а|мгц|кгц|ггц|гц|сек(?:\.?|унд(?:а|ы)?)|с\.?|мин(?:\.?|ут(?:а|ы)?)|ч(?:ас(?:а|ов)?)?|км/ч|м/с|км|мм|см|м|кг|мг|г|т|ком|ом|°с|°c|процент(?:а|ов)?|%)(?=$|[\s,;+/])",
        re.IGNORECASE,
    )
    for token in numeric_tokens:
        value = token.group(0)
        if not _answer_mentions_value(normalized, value):
            continue
        has_unit = bool(unit_after_number.match(normalized[token.end():]))
        if not has_unit:
            refs.add(value)
    return refs


def _open_pending_value_is_grounded(query: str, pending: dict[str, Any], tasks: list[dict[str, Any]], catalog_rows: list[dict[str, Any]], current_candidate_ids: set[str], clarification: dict[str, Any] | None) -> bool:
    if clarification is not None:
        return False
    answer = query_normalized(query)
    if re.search(r"\b(?:не\s+знаю|не\s+уверен|не\s+помню|без\s+понятия|затрудняюсь)\b", answer, re.IGNORECASE):
        return False
    if pending["field"] == "point":
        task_points = {str(task.get("point_ref")) for task in tasks if task.get("skill") == "exact_point" and task.get("point_ref") is not None}
        grounded_points = _extract_open_point_refs(answer)
        return len(grounded_points) == 1 and task_points == grounded_points
    if pending["field"] == "document":
        by_id = {row.get("doc_id"): row for row in catalog_rows}
        grounded_ids = {
            doc_id for doc_id in current_candidate_ids
            if doc_id in by_id and re.fullmatch(r"doc_[0-9a-f]{16}", str(doc_id))
            and (_answer_mentions_value(answer, str(doc_id)) or any(_answer_mentions_value(answer, str(value)) for value in [by_id[doc_id].get("doc_name", ""), *by_id[doc_id].get("aliases", [])] if value))
        }
        task_doc_ids = {str(task.get("doc_id")) for task in tasks if task.get("doc_id") and task.get("doc_id") in current_candidate_ids and re.fullmatch(r"doc_[0-9a-f]{16}", str(task.get("doc_id")))}
        return len(grounded_ids) == 1 and task_doc_ids == grounded_ids
    if pending["field"] == "term":
        return any(_answer_mentions_value(answer, str(value)) for task in tasks for concept in task.get("concepts", []) for value in [concept.get("surface", ""), *concept.get("variants", [])] if value)
    if pending["field"] == "scope":
        aliases = {"corpus": ["корпус", "все документы", "везде"], "document": ["документ", "этот документ", "в документе"]}
        return any(task.get("scope") in aliases and any(_answer_mentions_value(answer, term) for term in aliases[task["scope"]]) for task in tasks)
    if pending["field"] == "intent":
        aliases = {"exact_point": ["текст пункта", "номер пункта", "пункт"], "corpus_point_search": ["найти", "поиск", "требования"], "find_all": ["все пункты", "все требования", "все упоминания"]}
        return any(task.get("skill") in aliases and any(_answer_mentions_value(answer, term) for term in aliases[task["skill"]]) for task in tasks)
    return False


def _validate_pending_result(pending: dict[str, Any], selected_option: str | None, tasks: list[dict[str, Any]], clarification: dict[str, Any] | None, *, open_resolution: bool = False) -> None:
    if selected_option is None:
        if not open_resolution and (clarification is None or clarification.get("field") != pending["field"]):
            raise ValueError("unmatched clarification answer must leave the original clarification pending")
        if not open_resolution:
            return
    original = pending["original_task"]
    option = next((item for item in pending["options"] if item["id"] == selected_option), None) if selected_option is not None else None
    if selected_option is not None and option is None:
        raise ValueError("selected pending option ID is not present in the clarification options")
    matches = [task for task in tasks if task.get("skill") == original["skill"] and task.get("coverage") == original["coverage"]]
    if not matches:
        raise ValueError("resolved pending answer dropped the original task")
    task = matches[0]
    selected_label = query_normalized(option["label"]).casefold() if option else ""
    selected_id = query_normalized(option["id"]).casefold() if option else ""
    if option and pending["field"] == "point" and query_normalized(str(task.get("point_ref") or "")).casefold() != selected_id:
        raise ValueError("selected point option does not match resolved point_ref")
    if option and pending["field"] == "document":
        if task.get("doc_id") != option["id"]:
            raise ValueError("selected document option does not match resolved document")
    if option and pending["field"] == "scope" and task.get("scope") not in {option["id"], option["label"]}:
        raise ValueError("selected scope option does not match resolved scope")
    if option and pending["field"] == "intent" and task.get("skill") not in {option["id"], option["label"]}:
        raise ValueError("selected intent option does not match resolved skill")
    if option and pending["field"] == "term":
        terms = {query_normalized(str(value)).casefold() for concept in task.get("concepts", []) for value in [concept.get("surface"), *concept.get("variants", [])]}
        if selected_label not in terms and selected_id not in terms:
            raise ValueError("selected term option does not match resolved concept")
    for key in ("point_ref", "doc_id", "document_mention"):
        if original.get(key) is not None and task.get(key) != original[key]:
            raise ValueError(f"resolved pending answer changed original {key}")
    required_constraints = original.get("constraints", [])
    actual_constraints = {(x.get("kind"), x.get("text"), x.get("polarity")) for x in task.get("constraints", [])}
    if any((x.get("kind"), x.get("text"), x.get("polarity")) not in actual_constraints for x in required_constraints):
        raise ValueError("resolved pending answer dropped an original condition")
    original_concepts = original.get("concepts", [])
    actual_concepts = task.get("concepts", [])
    for concept in original_concepts:
        wanted = {str(value).casefold() for value in [concept.get("surface"), *concept.get("variants", [])]}
        found = any(wanted & {str(value).casefold() for value in [item.get("surface"), *item.get("variants", [])]} for item in actual_concepts)
        if not found:
            raise ValueError("resolved pending answer dropped an original concept or its variants")
    query_and_concepts = str(task.get("query") or "") + " " + " ".join(str(x.get("surface") or "") for x in task.get("concepts", []))
    for element in original.get("semantic_elements", []):
        variants = element.get("variants") or [element.get("label", "")]
        normalized = query_normalized(query_and_concepts).casefold()
        if not any(re.search(rf"(?<!\w){re.escape(query_normalized(str(value)).casefold())}(?!\w)", normalized) for value in variants if value):
            raise ValueError("resolved pending answer dropped an original topic")


def _catalog_entries(catalog: Any) -> list[dict[str, Any]]:
    raw = _plain(catalog)
    entries = raw.get("entries", []) if isinstance(raw, dict) else getattr(catalog, "entries", [])
    out = []
    for item in entries or []:
        row = _plain(item)
        if isinstance(row, dict):
            out.append({"catalog_id": row.get("catalog_id", ""), "doc_id": row.get("doc_id", ""), "doc_name": row.get("doc_name", ""), "aliases": list(row.get("aliases", ()) or ()), "order_numbers": list(row.get("order_numbers", ()) or ()), "registry_key": row.get("registry_key", "")})
    return out


def _vocabulary(query: str, knowledge: Any) -> dict[str, Any]:
    from mr_norm.retrieval.document_knowledge import match_query_terms

    matches = match_query_terms(query, knowledge)
    groups: list[dict[str, Any]] = []
    # match_query_terms intentionally returns a bounded flat list. Rehydrate all
    # values for matched, non-common abbreviations from the already loaded index.
    allowed = {str(x).casefold() for x in matches.document_hints}
    for row in getattr(knowledge, "abbreviations", []) or []:
        key = str(row.get("abbreviation") or "").strip()
        expansion = str(row.get("expansion") or "").strip()
        if not key or not expansion or key.casefold() not in allowed:
            continue
        group = next((g for g in groups if g["abbreviation"].casefold() == key.casefold()), None)
        if group is None:
            group = {"abbreviation": key, "expansions": []}
            groups.append(group)
        if expansion not in group["expansions"]:
            group["expansions"].append(expansion)
    return {
        "exact_terms": list(matches.exact_phrase_terms),
        "morphology_variants": list(matches.morphology_terms),
        "abbreviation_variants": list(matches.abbreviation_expansions),
        "abbreviation_groups": groups,
        "loose_terms": list(matches.loose_terms),
        "document_hints": list(matches.document_hints),
    }


def _adapter_call(adapter: Any, *, role: str, messages: list[dict[str, str]], model: str, timeout: float, max_tokens: int) -> Any:
    if callable(adapter):
        return adapter(role=role, messages=messages, model=model, timeout=timeout, max_tokens=max_tokens)
    for method in ("complete", "call", "invoke"):
        fn = getattr(adapter, method, None)
        if fn:
            return fn(role=role, messages=messages, model=model, timeout=timeout, max_tokens=max_tokens)
    chat = getattr(adapter, "chat", None)
    if chat:
        from mr_norm.runtime.llm_clients import LLMRequest
        response = chat(LLMRequest(messages=messages, model=model, max_tokens=max_tokens))
        return response
    raise TypeError("llm_adapter must be callable or expose complete/call/invoke/chat")


def _response(value: Any, requested_model: str) -> tuple[str, str, Any]:
    row = _plain(value)
    if isinstance(row, str):
        return row, requested_model, None
    if isinstance(row, dict):
        content = row.get("content", row.get("text", ""))
        return str(content), str(row.get("model") or requested_model), row.get("usage")
    raw = getattr(value, "raw", {}) or {}
    usage = getattr(value, "usage", None)
    if usage is None and isinstance(raw, dict):
        usage = raw.get("usage")
    return str(getattr(value, "content", "")), str(getattr(value, "model", requested_model)), usage


def _call(adapter: Any, role: str, system: str, payload: dict[str, Any], model: str, timeout: float, max_tokens: int, trace: dict[str, Any]) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    start = time.perf_counter()
    call_trace = {"role": role, "requested_model": model, "actual_model": None, "elapsed_sec": None, "usage": None, "raw_reply": None}
    trace["calls"].append(call_trace)
    trace["logical_call_count"] = len(trace["calls"])
    trace["physical_call_count"] = len(trace["calls"])
    try:
        raw = _adapter_call(adapter, role=role, messages=messages, model=model, timeout=timeout, max_tokens=max_tokens)
        content, actual_model, usage = _response(raw, model)
    except Exception as exc:
        call_trace["elapsed_sec"] = round(time.perf_counter() - start, 4)
        call_trace["error"] = f"{type(exc).__name__}: {exc}"
        raise
    call_trace.update({"actual_model": actual_model, "elapsed_sec": round(time.perf_counter() - start, 4), "usage": usage, "raw_reply": content})
    if actual_model != model:
        raise ValueError(f"adapter returned unexpected model {actual_model!r}; requested {model!r}")
    return content


def _json_object(content: str) -> dict[str, Any]:
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("response must be a JSON object")
    return value


def _validate_classifier(data: dict[str, Any]) -> tuple[str, list[str]]:
    if set(data) != {"complexity", "reason_codes"}:
        raise ValueError("classifier must contain exactly complexity and reason_codes")
    complexity = data["complexity"]
    reasons = data["reason_codes"]
    if complexity not in {"simple", "complex"} or not isinstance(reasons, list) or not reasons:
        raise ValueError("invalid classifier complexity or empty reason_codes")
    if any(not isinstance(x, str) or x not in REASONS for x in reasons):
        raise ValueError("classifier contains an unknown reason code")
    complex_reasons = REASONS - {"single_intent"}
    if complexity == "simple" and any(reason in complex_reasons for reason in reasons):
        raise ValueError("simple classification contains a complex-only reason code")
    if complexity == "complex" and not any(reason in complex_reasons for reason in reasons):
        raise ValueError("complex classification requires a complex reason code")
    if "single_intent" in reasons and any(reason in complex_reasons for reason in reasons):
        raise ValueError("single_intent cannot be combined with a complex reason code")
    return complexity, reasons


def _validate_parser(data: dict[str, Any], *, query: str, context: dict[str, Any], catalog_rows: list[dict[str, Any]], current_candidate_ids: set[str], max_tasks: int) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    if set(data) != {"dialog_action", "tasks", "clarification", "issues"}:
        raise ValueError("parser response must contain exactly dialog_action, tasks, clarification, issues")
    action, tasks, clarification, issues = data["dialog_action"], data["tasks"], data["clarification"], data["issues"]
    if action not in {"new", "continue", "correct", "clarify"} or not isinstance(tasks, list) or len(tasks) > max_tasks:
        raise ValueError("invalid dialog_action or task count")
    if not isinstance(issues, list) or (clarification is not None and not isinstance(clarification, dict)):
        raise ValueError("issues must be an array and clarification an object or null")
    context_turns = {str(x["turn_id"]): str(x.get("user_text") or "") for x in context["turns"]}
    context_turns["current"] = query
    context_conditions = {str(x["id"]) for x in context["confirmed_conditions"]}
    known_docs = {str(row.get("doc_id")) for row in catalog_rows if row.get("doc_id")}
    current_document_option_ids = set(current_candidate_ids)
    current_document_option_ids.update(str(row.get("catalog_id")) for row in catalog_rows if row.get("doc_id") in current_candidate_ids and row.get("catalog_id"))
    seen: set[str] = set()
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict) or set(task) != TASK_KEYS:
            raise ValueError(f"task {i} has unknown or missing fields")
        tid = task["task_id"]
        if tid != f"t{i}" or tid in seen:
            raise ValueError(f"task IDs must be unique and sequential (expected t{i})")
        seen.add(tid)
        if not isinstance(task["intent_text"], str) or not task["intent_text"].strip() or not isinstance(task["query"], str) or not task["query"].strip():
            raise ValueError(f"{tid} requires intent_text and query")
        if task["skill"] not in SKILLS and task["skill"] is not None:
            raise ValueError(f"{tid} has unknown skill")
        if task["scope"] not in {"corpus", "document", "unresolved"} or task["coverage"] not in {"exact", "ranked", "comprehensive"}:
            raise ValueError(f"{tid} has invalid scope or coverage")
        if task["scope"] == "corpus" and task["doc_id"] is not None:
            raise ValueError(f"{tid} corpus scope requires null doc_id")
        if task["scope"] == "document" and (not task["doc_id"] or task["doc_id"] not in known_docs):
            raise ValueError(f"{tid} document scope requires catalog doc_id")
        if task["scope"] == "unresolved" and task["doc_id"] is not None:
            raise ValueError(f"{tid} unresolved scope requires null doc_id")
        if task["doc_id"] is not None and not isinstance(task["doc_id"], str):
            raise ValueError(f"{tid} doc_id must be a string or null")
        if task["document_mention"] is not None and not isinstance(task["document_mention"], str):
            raise ValueError(f"{tid} document_mention must be a string or null")
        if task["point_ref"] is not None and (not isinstance(task["point_ref"], str) or not task["point_ref"].strip()):
            raise ValueError(f"{tid} point_ref must be a nonempty string or null")
        expected_shape = {
            "exact_point": ("exact", {"document"}),
            "corpus_point_search": ("ranked", {"corpus", "document"}),
            "find_all": ("comprehensive", {"corpus", "document"}),
        }
        if task["skill"] in expected_shape:
            expected_coverage, allowed_scopes = expected_shape[task["skill"]]
            if task["coverage"] != expected_coverage or task["scope"] not in allowed_scopes:
                raise ValueError(f"{tid} has coverage/scope incompatible with {task['skill']}")
        if task["skill"] == "exact_point" and (not task["doc_id"] or not task["point_ref"]):
            raise ValueError(f"{tid} exact_point requires doc_id and point_ref")
        if task["skill"] in {"corpus_point_search", "find_all"} and task["point_ref"] is not None:
            raise ValueError(f"{tid} topic-search skill requires null point_ref")
        if not isinstance(task["evidence"], list) or not task["evidence"]:
            raise ValueError(f"{tid} evidence must be nonempty")
        for ev in task["evidence"]:
            if not isinstance(ev, dict) or set(ev) != {"turn_id", "quote"} or ev["turn_id"] not in context_turns or not isinstance(ev["quote"], str) or not ev["quote"] or ev["quote"] not in context_turns[ev["turn_id"]]:
                raise ValueError(f"{tid} evidence quote is not an exact source substring")
        if task["skill"] == "exact_point":
            ref = re.escape(str(task["point_ref"]))
            point_marker = re.compile(rf"(?:\bп(?:ункт(?:а|е|у|ом)?|\.)\s*{ref}\b|\b{ref}\s+пункт)", re.IGNORECASE)
            separated_ref = bool(re.search(r"[._]", str(task["point_ref"])))
            grounded = any(point_marker.search(ev["quote"]) or separated_ref and re.search(rf"\b{ref}\b(?!\s*(?:кв|в|а|м|гц)\b)", ev["quote"], re.IGNORECASE) for ev in task["evidence"])
            pending_open_point = context.get("pending_clarification")
            if not grounded and isinstance(pending_open_point, dict) and pending_open_point.get("field") == "point" and not pending_open_point.get("options") and re.fullmatch(rf"(?:пункт\s*)?{ref}", query_normalized(query), re.IGNORECASE):
                grounded = any(ev["turn_id"] == "current" and re.search(rf"(?<!\w){ref}(?!\w)", ev["quote"]) for ev in task["evidence"])
            if not grounded:
                raise ValueError(f"{tid} point_ref is not grounded as a point number and may be a quantity, date, or document number")
        if action == "new" and not any(ev["turn_id"] == "current" for ev in task["evidence"]):
            raise ValueError(f"{tid} new dialog action requires current-message evidence")
        if task["doc_id"] and task["doc_id"] not in current_candidate_ids:
            has_context_evidence = any(ev["turn_id"] != "current" for ev in task["evidence"])
            if action == "new" or not has_context_evidence:
                raise ValueError(f"{tid} selected doc_id is neither a current candidate nor grounded in prior-turn evidence")
        if not isinstance(task["concepts"], list) or not isinstance(task["constraints"], list) or not isinstance(task["depends_on"], list) or not isinstance(task["replaces"], list):
            raise ValueError(f"{tid} concepts, constraints, depends_on, replaces must be arrays")
        if any(x not in context_conditions for x in task["replaces"]):
            raise ValueError(f"{tid} replaces unknown context condition")
        if task["replaces"] and action != "correct":
            raise ValueError(f"{tid} replaces is allowed only for dialog_action correct")
        if task["replaces"] and not any(ev["turn_id"] == "current" for ev in task["evidence"]):
            raise ValueError(f"{tid} condition replacement needs current-message evidence")
        if any(not isinstance(d, str) or d == tid or d not in seen for d in task["depends_on"]):
            raise ValueError(f"{tid} has unknown or forward dependency")
        for concept in task["concepts"]:
            if not isinstance(concept, dict) or set(concept) != {"surface", "variants", "evidence"} or not isinstance(concept["surface"], str) or not concept["surface"].strip() or not isinstance(concept["variants"], list):
                raise ValueError(f"{tid} malformed concept")
            if not isinstance(concept["evidence"], list) or not concept["evidence"] or any(not isinstance(ev, dict) or set(ev) != {"turn_id", "quote"} or ev["turn_id"] not in context_turns or not ev["quote"] or ev["quote"] not in context_turns[ev["turn_id"]] for ev in concept["evidence"]):
                raise ValueError(f"{tid} concept evidence is not grounded in a source turn")
            if any(not isinstance(value, str) for value in concept["variants"]):
                raise ValueError(f"{tid} concept variants must be strings")
        constraint_ids: set[str] = set()
        for constraint in task["constraints"]:
            if not isinstance(constraint, dict) or set(constraint) != {"id", "kind", "text", "polarity", "evidence"} or constraint["kind"] not in {"object", "action", "condition", "quantity", "exclusion"} or constraint["polarity"] not in {"include", "exclude"}:
                raise ValueError(f"{tid} malformed constraint")
            if not isinstance(constraint["id"], str) or not constraint["id"] or constraint["id"] in constraint_ids or not isinstance(constraint["text"], str) or not constraint["text"].strip():
                raise ValueError(f"{tid} constraint IDs and text must be nonempty and IDs unique")
            constraint_ids.add(constraint["id"])
            if not isinstance(constraint["evidence"], list) or not constraint["evidence"] or any(not isinstance(ev, dict) or set(ev) != {"turn_id", "quote"} or ev["turn_id"] not in context_turns or not ev["quote"] or ev["quote"] not in context_turns[ev["turn_id"]] for ev in constraint["evidence"]):
                raise ValueError(f"{tid} constraint evidence is not grounded in a source turn")
            if action == "new" and not any(ev["turn_id"] == "current" for ev in constraint["evidence"]):
                raise ValueError(f"{tid} new dialog action cannot inherit a constraint without current evidence")
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != ISSUE_KEYS or issue["code"] not in ISSUE_CODES or not isinstance(issue["task_ids"], list) or any(not isinstance(x, str) or x not in seen for x in issue["task_ids"]) or not isinstance(issue["message"], str) or not issue["message"].strip():
            raise ValueError("malformed issue or unsupported issue code")
    ambiguity_codes = {"ambiguous_document", "ambiguous_term", "missing_point", "ambiguous_scope", "ambiguous_intent"}
    if any(issue["code"] in ambiguity_codes for issue in issues) and clarification is None:
        raise ValueError("ambiguity issue requires a concrete clarification question")
    if clarification is not None:
        if set(clarification) != {"question", "target_task_ids", "field", "options"} or not isinstance(clarification["question"], str) or not clarification["question"].strip() or clarification["field"] not in {"document", "term", "point", "scope", "intent"} or not isinstance(clarification["target_task_ids"], list) or any(x not in seen for x in clarification["target_task_ids"]):
            raise ValueError("malformed clarification")
        if not isinstance(clarification["options"], list) or len(clarification["options"]) > 5 or any(not isinstance(x, dict) or set(x) != {"id", "label"} or not isinstance(x["id"], str) or not x["id"].strip() or not isinstance(x["label"], str) or not x["label"].strip() for x in clarification["options"]):
            raise ValueError("clarification options must be 0-5 id/label objects")
        issue_field = {"ambiguous_document": "document", "ambiguous_term": "term", "missing_point": "point", "ambiguous_scope": "scope", "ambiguous_intent": "intent"}
        priorities = {"document": 0, "term": 1, "point": 2, "scope": 3, "intent": 4}
        unresolved_fields = [issue_field[x["code"]] for x in issues if x["code"] in issue_field]
        if not unresolved_fields or clarification["field"] != min(unresolved_fields, key=priorities.__getitem__):
            raise ValueError("clarification field must match the highest-priority ambiguity issue")
        if clarification["field"] == "document":
            allowed_labels: dict[str, set[str]] = {}
            for row in catalog_rows:
                if row.get("doc_id") not in current_candidate_ids:
                    continue
                labels = {str(row.get("doc_name") or ""), *(str(x) for x in row.get("aliases", []))}
                allowed_labels[str(row.get("doc_id"))] = labels
                allowed_labels[str(row.get("catalog_id"))] = labels
            if any(option["id"] not in current_document_option_ids or option["label"] not in allowed_labels.get(option["id"], set()) for option in clarification["options"]):
                raise ValueError("document clarification options must come from supplied catalog candidates")
    return action, tasks, clarification, issues


def _validate_vocab(tasks: list[dict[str, Any]], vocab: dict[str, Any]) -> None:
    permitted = {str(x).casefold() for key in ("exact_terms", "morphology_variants", "abbreviation_variants", "loose_terms", "document_hints") for x in vocab[key]}
    permitted.update(str(x.get("abbreviation", "")).casefold() for x in vocab["abbreviation_groups"])
    for task in tasks:
        for concept in task["concepts"]:
            for variant in concept["variants"]:
                if str(variant).casefold() not in permitted:
                    raise ValueError(f"unverified vocabulary variant: {variant}")


def _validate_term_clarification(clarification: dict[str, Any] | None, vocab: dict[str, Any]) -> None:
    if clarification is None or clarification["field"] != "term":
        return
    permitted = {str(x).casefold() for key in ("exact_terms", "morphology_variants", "abbreviation_variants", "loose_terms", "document_hints") for x in vocab[key]}
    for group in vocab["abbreviation_groups"]:
        permitted.add(str(group.get("abbreviation", "")).casefold())
        permitted.update(str(x).casefold() for x in group.get("expansions", []))
    if any(option["id"].casefold() not in permitted and option["label"].casefold() not in permitted for option in clarification["options"]):
        raise ValueError("term clarification options must come from supplied vocabulary candidates")


def _validate_document_choice(tasks: list[dict[str, Any]], clarification: dict[str, Any] | None, issues: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> None:
    scored = [candidate for candidate in candidates if candidate.get("doc_id") and isinstance(candidate.get("score"), (int, float))]
    if not scored:
        return
    top_score = max(float(candidate["score"]) for candidate in scored)
    top_ids = {str(candidate["doc_id"]) for candidate in scored if abs(float(candidate["score"]) - top_score) < 1e-9}
    selected = {str(task["doc_id"]) for task in tasks if task.get("doc_id")}
    if len(top_ids) > 1 and selected & top_ids and not top_ids <= selected:
        has_document_clarification = clarification is not None and clarification.get("field") == "document" and any(issue.get("code") == "ambiguous_document" for issue in issues)
        if not has_document_clarification:
            raise ValueError("equally ranked document candidates require clarification or explicit tasks for every selected document")


def plan_skill_query(query: str, *, dialog_context: Any = None, pending_clarification: Any = None, catalog: Any, validation_catalog: Any = None, knowledge: Any, llm_adapter: Any, config: RouterPlanConfig | dict[str, Any]) -> SkillQueryPlan:
    """Classify and parse a request without calling search skills or mutating memory."""
    cfg = config if isinstance(config, RouterPlanConfig) else RouterPlanConfig(**config)
    trace: dict[str, Any] = {
        "provider": cfg.provider,
        "prompt_version": cfg.prompt_version,
        "prompt_hashes": {
            "classifier": hashlib.sha256(CLASSIFIER_SYSTEM.encode("utf-8")).hexdigest(),
            "parser": hashlib.sha256(PARSER_SYSTEM.encode("utf-8")).hexdigest(),
        },
        "calls": [], "physical_call_count": 0, "logical_call_count": 0, "repair_count": 0,
    }
    if not isinstance(query, str) or not query.strip():
        return SkillQueryPlan(SCHEMA_VERSION, query if isinstance(query, str) else "", "error", None, issues=[{"code": "empty_query", "task_ids": [], "message": "query must be a nonempty string"}], trace=trace)
    if len(query) > cfg.max_query_chars:
        return SkillQueryPlan(SCHEMA_VERSION, query, "input_limit_exceeded", None, issues=[{"code": "query_too_long", "task_ids": [], "message": "query exceeds configured character limit"}], trace=trace)
    try:
        pending = _validate_pending_clarification(pending_clarification, dialog_context)
        selected_option = _pending_answer_matches(query, pending)
        context = _context_snapshot(dialog_context, pending)
        rows = _catalog_entries(catalog)
        validation_rows = _catalog_entries(validation_catalog) if validation_catalog is not None else rows
        if pending is not None and pending["original_task"].get("doc_id"):
            valid_doc_ids = {row.get("doc_id") for row in validation_rows}
            if pending["original_task"]["doc_id"] not in valid_doc_ids:
                raise ValueError("pending clarification original_task doc_id is absent from catalog")
        if dialog_context is not None:
            # Reject stale inherited document references rather than asking the model to trust them.
            ids = {row.get("doc_id") for row in validation_rows}
            for turn in context["turns"]:
                doc_id = turn.get("resolved_doc_id", turn.get("doc_id"))
                if doc_id and doc_id not in ids:
                    raise ValueError(f"context doc_id is absent from catalog: {doc_id}")
        search_basis = f"{pending['original_query']} {query}" if pending else query
        vocab = _vocabulary(search_basis, knowledge)
        from mr_norm.retrieval.document_catalog import find_catalog_candidates
        candidates = []
        if catalog is not None:
            entries_by_id = {str(row.get("catalog_id")): row for row in rows}
            for candidate in find_catalog_candidates(search_basis, catalog, limit=8):
                row = entries_by_id.get(str(candidate.catalog_id), {})
                candidates.append({**candidate.to_dict(), "doc_id": row.get("doc_id") or None, "registry_key": row.get("registry_key") or None})
    except Exception as exc:
        message = str(exc)
        code = "invalid_context_doc_id" if message.startswith("context doc_id is absent from catalog:") else "preparation_error"
        return SkillQueryPlan(SCHEMA_VERSION, query, "error", None, issues=[{"code": code, "task_ids": [], "message": message}], trace=trace)
    current_candidate_ids = {str(x["doc_id"]) for x in candidates if x.get("doc_id")}
    payload = {"original_query": query, "context": context, "pending_original_query": pending["original_query"] if pending else None, "pending_original_task": pending["original_task"] if pending else None, "pending_resolution": {"selected_option_id": selected_option, "resolved": selected_option is not None, "open": not bool(pending["options"])} if pending else None, "document_candidates": candidates, "term_candidates": vocab}
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > cfg.max_context_chars:
        return SkillQueryPlan(SCHEMA_VERSION, query, "input_limit_exceeded", None, issues=[{"code": "context_too_long", "task_ids": [], "message": "serialized context and candidates exceed configured character limit"}], trace=trace)
    try:
        classifier = _json_object(_call(llm_adapter, "classifier", CLASSIFIER_SYSTEM, payload, cfg.light_model, cfg.timeout_sec, cfg.classifier_max_tokens, trace))
        complexity, reasons = _validate_classifier(classifier)
        trace["classification_reasons"] = reasons
    except Exception as exc:
        trace["physical_call_count"] = len(trace["calls"])
        return SkillQueryPlan(SCHEMA_VERSION, query, "error", None, issues=[{"code": "classifier_error", "task_ids": [], "message": str(exc)}], trace=trace)
    role = "simple_parser" if complexity == "simple" else "complex_parser"
    model = cfg.light_model if complexity == "simple" else cfg.heavy_model
    parser_payload = {**payload, "max_tasks": cfg.max_tasks, "classifier": classifier}
    if len(json.dumps(parser_payload, ensure_ascii=False)) > cfg.max_context_chars:
        return SkillQueryPlan(SCHEMA_VERSION, query, "input_limit_exceeded", complexity, issues=[{"code": "context_too_long", "task_ids": [], "message": "serialized parser context exceeds configured character limit"}], trace=trace)
    try:
        pending_resolved = False
        raw = _call(llm_adapter, role, PARSER_SYSTEM, parser_payload, model, cfg.timeout_sec, cfg.parser_max_tokens, trace)
        try:
            parser_data = _json_object(raw)
            action, tasks, clarification, issues = _validate_parser(parser_data, query=query, context=context, catalog_rows=rows, current_candidate_ids=current_candidate_ids, max_tasks=cfg.max_tasks)
            _validate_vocab(tasks, vocab)
            _validate_term_clarification(clarification, vocab)
            _validate_document_choice(tasks, clarification, issues, candidates)
            if pending is not None and selected_option is not None:
                _validate_pending_result(pending, selected_option, tasks, clarification)
                pending_resolved = True
            elif pending is not None and not pending["options"] and _open_pending_value_is_grounded(query, pending, tasks, rows, current_candidate_ids, clarification):
                _validate_pending_result(pending, None, tasks, clarification, open_resolution=True)
                pending_resolved = True
        except Exception as first_error:
            repair_payload = {**parser_payload, "invalid_response": raw, "validation_errors": [str(first_error)], "instruction": "Repair only the contract errors. Preserve all user meaning; return the exact schema."}
            if len(json.dumps(repair_payload, ensure_ascii=False)) > cfg.max_context_chars:
                return SkillQueryPlan(SCHEMA_VERSION, query, "input_limit_exceeded", complexity, issues=[{"code": "context_too_long", "task_ids": [], "message": "serialized repair context exceeds configured character limit"}], trace=trace)
            trace["repair_count"] = 1
            raw = _call(llm_adapter, role, PARSER_SYSTEM, repair_payload, model, cfg.timeout_sec, cfg.parser_max_tokens, trace)
            parser_data = _json_object(raw)
            action, tasks, clarification, issues = _validate_parser(parser_data, query=query, context=context, catalog_rows=rows, current_candidate_ids=current_candidate_ids, max_tasks=cfg.max_tasks)
            _validate_vocab(tasks, vocab)
            _validate_term_clarification(clarification, vocab)
            _validate_document_choice(tasks, clarification, issues, candidates)
            if pending is not None and selected_option is not None:
                _validate_pending_result(pending, selected_option, tasks, clarification)
                pending_resolved = True
            elif pending is not None and not pending["options"] and _open_pending_value_is_grounded(query, pending, tasks, rows, current_candidate_ids, clarification):
                _validate_pending_result(pending, None, tasks, clarification, open_resolution=True)
                pending_resolved = True
    except Exception as exc:
        trace["physical_call_count"] = len(trace["calls"])
        return SkillQueryPlan(SCHEMA_VERSION, query, "error", complexity, issues=[{"code": "parser_error", "task_ids": [], "message": str(exc)}], trace=trace)
    # A task with a dependency or unsupported skill is preserved but cannot be ready.
    issue_codes = {x["code"] for x in issues}
    if any(task["skill"] is None for task in tasks):
        issue_codes.add("unsupported_intent")
        if not any(x["code"] == "unsupported_intent" for x in issues):
            issues.append({"code": "unsupported_intent", "task_ids": [x["task_id"] for x in tasks if x["skill"] is None], "message": "one or more requested intents are unsupported in stage 1"})
    if any(task["depends_on"] for task in tasks):
        issue_codes.add("dependency_execution_not_in_stage1")
        if not any(x["code"] == "dependency_execution_not_in_stage1" for x in issues):
            issues.append({"code": "dependency_execution_not_in_stage1", "task_ids": [x["task_id"] for x in tasks if x["depends_on"]], "message": "dependent execution is not supported in stage 1"})
    if clarification is not None or issue_codes & {"ambiguous_document", "ambiguous_term", "missing_point", "ambiguous_scope", "ambiguous_intent"}:
        status = "needs_clarification"
    elif issue_codes & {"unsupported_intent", "dependency_execution_not_in_stage1"} or any(x["skill"] is None or x["depends_on"] for x in tasks):
        status = "deferred"
    elif tasks:
        status = "ready"
    else:
        status = "error"
    if pending is not None and not pending_resolved:
        # A response that does not identify exactly one offered option cannot
        # close or replace the original question, even if the parser guessed.
        ambiguity_issue = {
            "document": "ambiguous_document", "term": "ambiguous_term", "point": "missing_point",
            "scope": "ambiguous_scope", "intent": "ambiguous_intent",
        }[pending["field"]]
        issues = [issue for issue in issues if issue.get("code") != "unsupported_intent"]
        issues.append({"code": ambiguity_issue, "task_ids": [], "message": "the response did not select exactly one offered clarification option"})
        clarification = {
            "question": pending["question"], "target_task_ids": pending.get("target_task_ids", []),
            "field": pending["field"], "options": pending["options"],
        }
        tasks = []
        status = "needs_clarification"
    trace["physical_call_count"] = len(trace["calls"])
    trace["logical_call_count"] = len(trace["calls"])
    return SkillQueryPlan(SCHEMA_VERSION, query, status, complexity, action, tasks, clarification, issues, trace)


def plan_skill_query_v2(query: str, *, dialog_context: Any = None, pending_clarification: Any = None, catalog: Any, validation_catalog: Any = None, knowledge: Any, llm_adapter: Any, config: RouterPlanConfig | dict[str, Any]) -> SkillQueryPlan:
    """Explicit v2 semantic-draft entry point; legacy v1 remains available."""
    from router_v2_runtime import plan_skill_query_v2 as run_v2
    return run_v2(query, dialog_context=dialog_context, pending_clarification=pending_clarification,
                  catalog=catalog, validation_catalog=validation_catalog, knowledge=knowledge,
                  llm_adapter=llm_adapter, config=config)
