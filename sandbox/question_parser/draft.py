"""Check a semantic draft and assemble a skill plan. No retrieval calls."""

from __future__ import annotations

import re
from typing import Any

from question_parser.catalog import Catalog
from question_parser.contracts import SkillQueryPlan
from question_parser.sources import LOCATOR_RE, DraftValidationError, build_source_manifest, extract_source_ref

OPERATIONS = {"all", "compare", "conclude", "define", "search", "summarize", "text", "unknown"}
UNSUPPORTED = {"define", "compare", "conclude", "summarize"}
SKILLS = {"text": ("exact_point", "exact"), "search": ("corpus_point_search", "ranked"), "all": ("find_all", "comprehensive")}
POINT_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[._]\d+)*(?![\w.])")
UNIT_RE = re.compile(r"\s*(?:мвт|квт|вт|кв|в|а|гц|с|сек|мин|ч|м|кг|г|т)(?=$|[\s,;+/])", re.IGNORECASE)


def validate_semantic_draft(
    draft: Any,
    manifest: dict[str, Any],
    *,
    candidate_ids: set[str],
    term_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(draft, dict) or set(draft) - {"dialog", "tasks", "uncertain"} or not {"dialog", "tasks"} <= set(draft):
        raise DraftValidationError("draft must contain dialog and tasks")
    if draft["dialog"] not in {"new", "continue", "correct", "clarify"}:
        raise DraftValidationError("invalid dialog enum")
    tasks = draft["tasks"]
    uncertain = draft.get("uncertain", [])
    if not isinstance(tasks, list) or len(tasks) > 6:
        raise DraftValidationError("tasks must be an array of at most 6 items")
    if not isinstance(uncertain, list):
        raise DraftValidationError("uncertain must be an array when present")
    if not tasks and not uncertain:
        raise DraftValidationError("empty tasks requires a concrete uncertain item")
    normalized = []
    for index, task in enumerate(tasks, 1):
        if not isinstance(task, dict) or not {"operation", "source"} <= set(task):
            raise DraftValidationError(f"task {index} has unknown or missing fields")
        if task["operation"] not in OPERATIONS:
            raise DraftValidationError(f"task {index} has unsupported operation enum")
        source = task["source"]
        if not isinstance(source, list) or not source:
            raise DraftValidationError(f"task {index} requires source refs")
        evidence = [extract_source_ref(ref, manifest) for ref in source]
        row = dict(task)
        row["evidence"] = evidence
        if "topic" in row:
            if not isinstance(row["topic"], str) or not row["topic"].strip():
                raise DraftValidationError(f"task {index} topic must be nonempty text when present")
            if LOCATOR_RE.search(row["topic"]):
                raise DraftValidationError(
                    f"task {index} TOPIC_MUST_BE_LITERAL_TEXT",
                    code="TOPIC_MUST_BE_LITERAL_TEXT",
                )
        if task["operation"] in {"search", "all"} and "topic" not in task:
            raise DraftValidationError(f"task {index} requires topic unless its intent is uncertain")
        if "document" in row and row["document"] not in {"corpus", "unresolved"} and row["document"] not in candidate_ids:
            raise DraftValidationError(f"task {index} document ID is not in candidates")
        if "point" in row:
            point = extract_source_ref(row["point"], manifest)
            numbers = POINT_NUMBER_RE.findall(point["quote"])
            tail = point["quote"].split(numbers[0], 1)[1] if numbers else ""
            if len(numbers) != 1 or UNIT_RE.search(tail):
                raise DraftValidationError("point SourceRef must identify one point number, not a measured quantity")
            row["point_evidence"] = point
        for key in ("terms", "conditions"):
            if key not in row:
                continue
            values = []
            for item in row[key]:
                if key == "terms":
                    if not isinstance(item, dict) or "source" not in item:
                        raise DraftValidationError("term must contain source and optional meaning")
                    if "meaning" in item and item["meaning"] not in term_ids:
                        raise DraftValidationError("term meaning ID is not in prepared vocabulary")
                    values.append({**item, "evidence": extract_source_ref(item["source"], manifest)})
                else:
                    if item.get("kind") not in {"object", "action", "condition", "quantity", "exclusion"} or item.get("polarity") not in {"include", "exclude"}:
                        raise DraftValidationError("malformed semantic condition")
                    values.append({**item, "evidence": extract_source_ref(item["source"], manifest)})
            row[key] = values
        for prior in row.get("after", []):
            if not isinstance(prior, int) or prior < 1 or prior >= index:
                raise DraftValidationError("after references must point to an earlier task")
        normalized.append(row)
    for item in uncertain:
        if not isinstance(item, dict) or item.get("field") not in {"document", "intent", "point", "scope", "term"} or "source" not in item:
            raise DraftValidationError("uncertain item is malformed")
        extract_source_ref(item["source"], manifest)
        for candidate in item.get("candidates", []):
            if candidate not in candidate_ids and candidate not in term_ids:
                raise DraftValidationError("uncertain candidates must be IDs from the prepared catalog")
    return {"dialog": draft["dialog"], "tasks": normalized, "uncertain": list(uncertain)}


def assemble_skill_query_plan(
    query: str,
    complexity: str,
    draft: dict[str, Any],
    manifest: dict[str, Any],
    catalog: Catalog,
) -> SkillQueryPlan:
    tasks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, source_task in enumerate(draft["tasks"], 1):
        operation = source_task["operation"]
        skill, coverage = SKILLS.get(operation, (None, "ranked"))
        document = source_task.get("document", "corpus" if operation in {"search", "all"} else "unresolved")
        doc_id = None if document in {"corpus", "unresolved"} else document
        card = catalog.document(doc_id) if doc_id else None
        scope = "document" if doc_id else ("corpus" if document == "corpus" else "unresolved")
        evidence = [{"turn_id": item["turn_id"], "quote": item["quote"]} for item in source_task["evidence"]]
        topic = source_task.get("topic") or " ".join(item["quote"] for item in evidence)
        point_ref = None
        if operation == "text" and source_task.get("point_evidence"):
            numbers = POINT_NUMBER_RE.findall(source_task["point_evidence"]["quote"])
            point_ref = numbers[0] if len(numbers) == 1 else None
        task_issues = []
        if operation in UNSUPPORTED:
            task_issues.append("unsupported_intent")
        if operation == "unknown":
            task_issues.append("ambiguous_intent")
        if operation == "text" and not doc_id:
            task_issues.append("ambiguous_document")
        if operation == "text" and not point_ref:
            task_issues.append("missing_point")
        constraints = []
        for number, item in enumerate(source_task.get("conditions", []), 1):
            constraints.append(
                {
                    "id": f"t{index}:c{number}",
                    "kind": item["kind"],
                    "text": item["evidence"]["quote"],
                    "polarity": item["polarity"],
                    "evidence": [item["evidence"]],
                }
            )
        concepts = []
        for item in source_task.get("terms", []):
            meaning = catalog.meaning(item["meaning"]) if item.get("meaning") else None
            concepts.append(
                {
                    "surface": item["evidence"]["quote"],
                    "meaning_id": item.get("meaning"),
                    "variants": list(meaning.variants) if meaning else [],
                    "evidence": [item["evidence"]],
                }
            )
        tasks.append(
            {
                "task_id": f"t{index}",
                "operation": operation,
                "skill": skill,
                "query": topic,
                "scope": scope,
                "doc_id": doc_id,
                "document_mention": card.name if card else None,
                "point_ref": point_ref,
                "coverage": coverage,
                "concepts": concepts,
                "constraints": constraints,
                "depends_on": [f"t{prior}" for prior in source_task.get("after", [])],
            }
        )
        for issue in task_issues:
            issues.append({"code": issue, "task_ids": [f"t{index}"], "message": f"operation {operation} requires {issue}"})
        if source_task.get("after"):
            issues.append(
                {
                    "code": "dependency_execution_not_in_stage1",
                    "task_ids": [f"t{index}"],
                    "message": "dependent execution is deferred in stage 1",
                }
            )
    for item in draft.get("uncertain", []):
        code = {
            "document": "ambiguous_document",
            "term": "ambiguous_term",
            "point": "missing_point",
            "scope": "ambiguous_scope",
            "intent": "ambiguous_intent",
        }[item["field"]]
        task_index = item.get("task", 1)
        issues.append(
            {
                "code": code,
                "task_ids": [f"t{task_index}"] if 1 <= task_index <= len(tasks) else [],
                "message": f"semantic draft marks {item['field']} as uncertain",
            }
        )
    clarification = _clarification(query, draft, manifest, catalog, issues, tasks)
    status = "needs_clarification" if clarification else "deferred" if issues else "ready"
    return SkillQueryPlan(
        "skill_query_plan.v1",
        query,
        status,
        complexity,
        draft["dialog"],
        tasks,
        clarification,
        issues,
        {"semantic_draft": {"dialog": draft["dialog"], "uncertain": draft.get("uncertain", [])}},
    )


def refresh_plan_status(plan: SkillQueryPlan, catalog: Catalog) -> SkillQueryPlan:
    """Recompute clarification and status after a field was resolved or inherited."""
    field_by_code = {
        "ambiguous_document": "document",
        "ambiguous_term": "term",
        "missing_point": "point",
        "ambiguous_scope": "scope",
        "ambiguous_intent": "intent",
    }
    active = {field_by_code[issue["code"]] for issue in plan.issues if issue["code"] in field_by_code}
    uncertain = [
        item
        for item in plan.trace.get("semantic_draft", {}).get("uncertain", [])
        if item.get("field") in active
    ]
    plan.trace = {**plan.trace, "semantic_draft": {"dialog": plan.dialog_action, "uncertain": uncertain}}
    manifest = build_source_manifest(plan.original_query)
    draft = {"dialog": plan.dialog_action, "uncertain": uncertain}
    plan.clarification = _clarification(plan.original_query, draft, manifest, catalog, plan.issues, plan.tasks)
    plan.status = "needs_clarification" if plan.clarification else "deferred" if plan.issues else "ready"
    return plan


def _clarification(query, draft, manifest, catalog: Catalog, issues, tasks) -> dict[str, Any] | None:
    order = ("document", "term", "point", "scope", "intent")
    codes = {
        "document": "ambiguous_document",
        "term": "ambiguous_term",
        "point": "missing_point",
        "scope": "ambiguous_scope",
        "intent": "ambiguous_intent",
    }
    pending = [field for field in order if any(issue["code"] == codes[field] for issue in issues)]
    if not pending:
        return None
    field = pending[0]
    marker = next((item for item in draft.get("uncertain", []) if item["field"] == field), None)
    subject = extract_source_ref(marker["source"], manifest)["quote"] if marker else query
    question = {
        "document": f"Какой документ вы имеете в виду под «{subject.strip()}»?",
        "term": f"Какое значение термина «{subject.strip()}» вы имеете в виду?",
        "point": f"Какой номер пункта вы имеете в виду под «{subject.strip()}»?",
        "scope": f"В какой области искать по теме «{subject.strip()}»?",
        "intent": f"Что нужно сделать с темой «{subject.strip()}»?",
    }[field]
    options = []
    for candidate_id in (marker or {}).get("candidates", []):
        document = catalog.document(candidate_id)
        meaning = catalog.meaning(candidate_id)
        label = document.name if document else (meaning.label if meaning else candidate_id)
        options.append({"id": candidate_id, "label": label})
    task_ids = []
    if marker and tasks:
        task_ids = [f"t{marker.get('task', 1)}"]
    return {"question": question, "field": field, "target_task_ids": task_ids, "options": options}
