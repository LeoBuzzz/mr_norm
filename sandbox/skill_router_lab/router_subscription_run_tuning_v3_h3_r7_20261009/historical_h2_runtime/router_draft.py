"""Semantic draft v2 validation, source addressing, and deterministic assembly.

This module deliberately contains no LLM, search, or persistence calls.
"""
from __future__ import annotations

import re
import json
from typing import Any

from router_contracts import SkillQueryPlan

TOKENIZER_VERSION = "source_tokens.v1"
TOKEN_RE = re.compile(r"\d+(?:[._]\d+)*|[^\W\d_]+(?:[-’'][^\W\d_]+)*|[^\s]", re.UNICODE)
OPERATIONS = {"text", "search", "all", "define", "compare", "conclude", "summarize", "unknown"}
UNCERTAIN_FIELDS = {"document", "term", "point", "scope", "intent"}
TASK_OPTIONAL = {"topic", "document", "point", "terms", "conditions", "after", "replace"}
SEMANTIC_DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["dialog", "tasks"],
    "properties": {
        "dialog": {"type": "string", "enum": ["new", "continue", "correct", "clarify"]},
        "tasks": {"type": "array", "maxItems": 6, "items": {"type": "object", "additionalProperties": False, "required": ["operation", "source"], "properties": {
            "operation": {"type": "string", "enum": sorted(OPERATIONS)}, "source": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "topic": {"type": "string"}, "document": {"type": "string"}, "point": {"type": "string"},
            "terms": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["source"], "properties": {"source": {"type": "string"}, "meaning": {"type": "string"}}}},
            "conditions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["source", "kind", "polarity"], "properties": {"source": {"type": "string"}, "kind": {"type": "string", "enum": ["object", "action", "condition", "quantity", "exclusion"]}, "polarity": {"type": "string", "enum": ["include", "exclude"]}}}},
            "after": {"type": "array", "items": {"type": "integer", "minimum": 1}}, "replace": {"type": "array", "items": {"type": "string"}},
        }}},
        "uncertain": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["field", "source"], "properties": {
            "field": {"type": "string", "enum": sorted(UNCERTAIN_FIELDS)}, "source": {"type": "string"}, "task": {"type": "integer", "minimum": 1}, "candidates": {"type": "array", "items": {"type": "string"}},
        }}},
    },
}


def semantic_draft_schema_description() -> str:
    """Compact complete wire contract, generated from the authoritative schema."""
    required: list[str] = []
    enums: list[str] = []
    def walk(node: Any, path: str = "draft") -> None:
        if not isinstance(node, dict):
            return
        if node.get("required"):
            required.append(f"{path}.required=" + ",".join(node["required"]))
        if "enum" in node:
            enums.append(f"{path}=" + "|".join(str(value) for value in node["enum"]))
        for key, child in node.items():
            if key == "properties" and isinstance(child, dict):
                for name, value in child.items():
                    walk(value, f"{path}.{name}")
            elif key == "items":
                walk(child, f"{path}[]")
            elif key == "properties":
                continue
    walk(SEMANTIC_DRAFT_SCHEMA)
    return ("Return JSON only matching semantic draft v2. Required: " + "; ".join(required) +
            ". Enumerations from schema: " + "; ".join(enums) +
            ". Full property types, optional fields, nested shapes, and additionalProperties rules are in this normative compact schema: " +
            json.dumps(SEMANTIC_DRAFT_SCHEMA, ensure_ascii=False, separators=(",", ":")) +
            ". SourceRefs use turn_id@first:last inclusive token ordinals. Optional fields may be omitted; never emit null or unknown fields.")


class DraftValidationError(ValueError):
    pass


def build_source_manifest(query: str, context: dict[str, Any] | None = None, pending: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build immutable, offset-addressed token tables; source strings are preserved."""
    raw_turns = list((context or {}).get("turns", []))
    rows = [{"turn_id": "current", "text": query}]
    rows.extend({"turn_id": str(t["turn_id"]), "text": str(t.get("user_text", ""))} for t in raw_turns)
    if pending:
        if any(row["turn_id"] == "pending_original" for row in rows):
            raise DraftValidationError("reserved source ID pending_original already exists")
        rows.append({"turn_id": "pending_original", "text": str(pending["original_query"])})
    seen: set[str] = set()
    tokens: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    for row in rows:
        tid, text = row["turn_id"], row["text"]
        if not tid or tid in seen:
            raise DraftValidationError(f"duplicate or empty source turn ID: {tid!r}")
        seen.add(tid)
        sources[tid] = text
        tokens[tid] = [{"ordinal": i, "start": m.start(), "end": m.end(), "text": m.group(0)} for i, m in enumerate(TOKEN_RE.finditer(text), 1)]
    return {"tokenizer_version": TOKENIZER_VERSION, "sources": sources, "tokens": tokens}


def extract_source_ref(ref: Any, manifest: dict[str, Any]) -> dict[str, str]:
    if not isinstance(ref, str):
        raise DraftValidationError("SourceRef must be a string")
    match = re.fullmatch(r"([^@]+)@(\d+):(\d+)", ref)
    if not match:
        raise DraftValidationError(f"malformed SourceRef: {ref!r}")
    turn_id, first, last = match.group(1), int(match.group(2)), int(match.group(3))
    tokens = manifest["tokens"].get(turn_id)
    if tokens is None or first < 1 or last < first or last > len(tokens):
        raise DraftValidationError(f"SourceRef is outside available tokens: {ref!r}")
    source = manifest["sources"][turn_id]
    quote = source[tokens[first - 1]["start"]:tokens[last - 1]["end"]]
    return {"turn_id": turn_id, "quote": quote}


def validate_semantic_draft(draft: Any, manifest: dict[str, Any], *, max_tasks: int = 6, candidate_ids: set[str] | None = None, term_ids: set[str] | None = None, replace_ids: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(draft, dict) or set(draft) - {"dialog", "tasks", "uncertain"} or not {"dialog", "tasks"} <= set(draft):
        raise DraftValidationError("draft must contain dialog and tasks; only uncertain may be omitted")
    if draft["dialog"] not in {"new", "continue", "correct", "clarify"}:
        raise DraftValidationError("invalid dialog enum")
    tasks = draft["tasks"]
    if not isinstance(tasks, list) or len(tasks) > max_tasks:
        raise DraftValidationError("tasks must be an array within configured limit")
    uncertain = draft.get("uncertain", [])
    if not isinstance(uncertain, list):
        raise DraftValidationError("uncertain must be an array when present")
    if not tasks and not uncertain:
        raise DraftValidationError("empty tasks requires a concrete uncertain item")
    candidate_ids, term_ids, replace_ids = candidate_ids or set(), term_ids or set(), replace_ids or set()
    normalized_tasks = []
    if draft["dialog"] == "new" and any(not any(isinstance(ref, str) and ref.startswith("current@") for ref in task.get("source", [])) for task in tasks):
        raise DraftValidationError("new dialog tasks require source evidence from current")
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict) or set(task) - ({"operation", "source"} | TASK_OPTIONAL) or not {"operation", "source"} <= set(task):
            raise DraftValidationError(f"task {i} has unknown/missing fields")
        if task["operation"] not in OPERATIONS:
            raise DraftValidationError(f"task {i} has unsupported operation enum")
        source = task["source"]
        if not isinstance(source, list) or not source:
            raise DraftValidationError(f"task {i} requires source refs")
        evidence = [extract_source_ref(ref, manifest) for ref in source]
        row = dict(task)
        row["evidence"] = evidence
        if "topic" in row and (not isinstance(row["topic"], str) or not row["topic"].strip()):
            raise DraftValidationError(f"task {i} topic must be nonempty text when present")
        if task["operation"] in {"search", "all"} and "topic" not in task and not any(x.get("field") == "intent" and x.get("task", i) == i for x in uncertain):
            raise DraftValidationError(f"task {i} requires topic unless its intent is uncertain")
        if "document" in row and row["document"] not in {"corpus", "unresolved"} and row["document"] not in candidate_ids:
            raise DraftValidationError(f"task {i} document ID is not in candidates")
        if "point" in row:
            point_ev = extract_source_ref(row["point"], manifest)
            point_numbers = re.findall(r"(?<![\w.])\d+(?:[._]\d+)*(?![\w.])", point_ev["quote"])
            unit = re.compile(r"\s*(?:мвт|квт|вт|кв|в|а|гц|с|сек|мин|ч|м|кг|г|т)(?=$|[\s,;+/])", re.IGNORECASE)
            if len(point_numbers) != 1 or unit.search(point_ev["quote"].split(point_numbers[0], 1)[1]):
                raise DraftValidationError("point SourceRef must identify one point number, not a measured quantity")
            row["point_evidence"] = [point_ev]
        for key in ("terms", "conditions"):
            if key not in row:
                continue
            if not isinstance(row[key], list):
                raise DraftValidationError(f"task {i} {key} must be an array")
            values = []
            for item in row[key]:
                if key == "terms":
                    if not isinstance(item, dict) or set(item) - {"source", "meaning"} or "source" not in item:
                        raise DraftValidationError("term must contain source and optional meaning")
                    if "meaning" in item and item["meaning"] not in term_ids:
                        raise DraftValidationError("term meaning ID is not in prepared vocabulary")
                    values.append({**item, "evidence": extract_source_ref(item["source"], manifest)})
                else:
                    if not isinstance(item, dict) or set(item) != {"source", "kind", "polarity"} or item["kind"] not in {"object", "action", "condition", "quantity", "exclusion"} or item["polarity"] not in {"include", "exclude"}:
                        raise DraftValidationError("malformed semantic condition")
                    values.append({**item, "evidence": extract_source_ref(item["source"], manifest)})
            row[key] = values
        for key in ("after", "replace"):
            if key in row and not isinstance(row[key], list):
                raise DraftValidationError(f"{key} must be an array")
        for prior in row.get("after", []):
            if not isinstance(prior, int) or prior < 1 or prior >= i:
                raise DraftValidationError("after references must point to an earlier task")
        if "replace" in row and (draft["dialog"] != "correct" or any(x not in replace_ids for x in row["replace"])):
            raise DraftValidationError("replace is allowed only for correct and known condition IDs")
        normalized_tasks.append(row)
    for i, item in enumerate(uncertain):
        if not isinstance(item, dict) or set(item) - {"field", "source", "task", "candidates"} or not {"field", "source"} <= set(item) or item["field"] not in UNCERTAIN_FIELDS:
            raise DraftValidationError(f"uncertain item {i + 1} is malformed")
        extract_source_ref(item["source"], manifest)
        if "task" in item and (not isinstance(item["task"], int) or not 1 <= item["task"] <= len(tasks)):
            raise DraftValidationError("uncertain task reference is invalid")
        if "candidates" in item and (not isinstance(item["candidates"], list) or any(x not in candidate_ids | term_ids for x in item["candidates"])):
            raise DraftValidationError("uncertain candidates must be IDs from prepared manifest")
    return {"dialog": draft["dialog"], "tasks": normalized_tasks, "uncertain": list(uncertain)}


def assemble_skill_query_plan(query: str, complexity: str, draft: dict[str, Any], manifest: dict[str, Any], *, catalog_rows: list[dict[str, Any]], vocab: dict[str, Any] | None = None, trace: dict[str, Any] | None = None) -> SkillQueryPlan:
    """Assemble public v2 tasks and status deterministically from validated semantics."""
    rows_by_id = {str(row.get("doc_id")): row for row in catalog_rows if row.get("doc_id")}
    skill_map = {"text": ("exact_point", "exact"), "search": ("corpus_point_search", "ranked"), "all": ("find_all", "comprehensive")}
    tasks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for i, source_task in enumerate(draft["tasks"], 1):
        operation = source_task["operation"]
        skill, coverage = skill_map.get(operation, (None, "ranked"))
        doc = source_task.get("document", "corpus" if operation in {"search", "all"} else "unresolved")
        doc_id = doc if doc not in {"corpus", "unresolved"} else None
        scope = "document" if doc_id else ("corpus" if doc == "corpus" else "unresolved")
        evidence = list(dict.fromkeys((ev["turn_id"], ev["quote"]) for ev in source_task["evidence"]))
        evidence = [{"turn_id": turn, "quote": quote} for turn, quote in evidence]
        query_text = source_task.get("topic") or " ".join(ev["quote"] for ev in evidence)
        point_ref = None
        if operation == "text" and source_task.get("point"):
            point_ref = extract_source_ref(source_task["point"], manifest)["quote"]
            point_match = re.search(r"\d+(?:[._]\d+)*", point_ref)
            point_ref = point_match.group(0) if point_match else None
        task_issues = []
        if operation in {"define", "compare", "conclude", "summarize"}:
            task_issues.append("unsupported_intent")
        if operation == "unknown":
            task_issues.append("ambiguous_intent")
        if operation == "text" and (doc == "unresolved" or not doc_id):
            task_issues.append("ambiguous_document")
        if operation == "text" and not point_ref:
            task_issues.append("missing_point")
        constraints = []
        for n, item in enumerate(source_task.get("conditions", []), 1):
            ev = item["evidence"]
            constraints.append({"id": f"t{i}:c{n}", "kind": item["kind"], "text": ev["quote"], "polarity": item["polarity"], "evidence": [ev]})
        concepts = []
        for item in source_task.get("terms", []):
            ev = item["evidence"]
            variants = []
            if vocab and item.get("meaning"):
                for group in vocab.get("meaning_groups", []):
                    if group.get("meaning_id") == item["meaning"]:
                        variants = list(group.get("variants", []))
                        break
            concepts.append({"surface": ev["quote"], "variants": variants, "evidence": [ev]})
        tasks.append({"task_id": f"t{i}", "intent_text": f"{operation}: {query_text}", "evidence": evidence, "skill": skill, "query": query_text, "scope": scope, "doc_id": doc_id, "document_mention": None if doc_id is None else (rows_by_id.get(doc_id, {}).get("doc_name") or doc_id), "point_ref": point_ref, "coverage": coverage, "concepts": concepts, "constraints": constraints, "depends_on": [f"t{x}" for x in source_task.get("after", [])], "replaces": list(source_task.get("replace", []))})
        for issue in task_issues:
            code = {"ambiguous_document": "ambiguous_document", "missing_point": "missing_point", "unsupported_intent": "unsupported_intent", "ambiguous_intent": "ambiguous_intent"}[issue]
            issues.append({"code": code, "task_ids": [f"t{i}"], "message": f"operation {operation} requires {issue}"})
    uncertainties = draft.get("uncertain", [])
    for item in uncertainties:
        issue_code = {"document": "ambiguous_document", "term": "ambiguous_term", "point": "missing_point", "scope": "ambiguous_scope", "intent": "ambiguous_intent"}[item["field"]]
        index = item.get("task", 1)
        issues.append({"code": issue_code, "task_ids": [f"t{index}"] if 1 <= index <= len(tasks) else [], "message": f"semantic draft marks {item['field']} as uncertain"})
    priority = ("document", "term", "point", "scope", "intent")
    field_codes = {"document": "ambiguous_document", "term": "ambiguous_term", "point": "missing_point", "scope": "ambiguous_scope", "intent": "ambiguous_intent"}
    unresolved = [field for field, code in field_codes.items() if any(x["code"] == code for x in issues)]
    clarification = None
    if unresolved:
        field = min(unresolved, key=priority.index)
        ambiguity = next(x for x in uncertainties if x["field"] == field) if any(x["field"] == field for x in uncertainties) else None
        source = extract_source_ref(ambiguity["source"], manifest)["quote"] if ambiguity else query
        labels = []
        if ambiguity:
            for candidate_id in ambiguity.get("candidates", []):
                row = rows_by_id.get(candidate_id)
                meaning = next((group for group in (vocab or {}).get("meaning_groups", []) if group.get("meaning_id") == candidate_id), None)
                label = row.get("doc_name") if row else (meaning.get("label") if meaning else candidate_id)
                labels.append({"id": candidate_id, "label": str(label)})
        subject = source.strip()
        question = {
            "document": f"Какой документ вы имеете в виду под «{subject}»?",
            "term": f"Какое значение термина «{subject}» вы имеете в виду?",
            "point": f"Какой номер пункта вы имеете в виду под «{subject}»?",
            "scope": f"В какой области искать по теме «{subject}»?",
            "intent": f"Что нужно сделать с темой «{subject}»?",
        }[field]
        clarification = {"question": question, "target_task_ids": [f"t{ambiguity.get('task', 1)}"] if ambiguity and tasks else [], "field": field, "options": labels[:5]}
    if any(x["code"] == "unsupported_intent" for x in issues) or any(task["depends_on"] for task in tasks):
        for task in tasks:
            if task["depends_on"]:
                issues.append({"code": "dependency_execution_not_in_stage1", "task_ids": [task["task_id"]], "message": "dependent execution is deferred in stage 1"})
    status = "needs_clarification" if clarification else "deferred" if issues else "ready"
    versioned_trace = dict(trace or {})
    versioned_trace["semantic_draft"] = draft
    versioned_trace["source_manifest"] = manifest
    return SkillQueryPlan("skill_query_plan.v2", query, status, complexity, draft["dialog"], tasks, clarification, issues, versioned_trace)
