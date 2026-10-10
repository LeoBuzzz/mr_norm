"""One user turn: resolve a pending clarification, or read the question and run it.

The clarification answer is applied to the stored plan and its field. Other
conditions stay. A reply that does not resolve the field does not start a search.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from question_parser.catalog import Catalog, load_catalog
from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.corpus import LocalCorpus
from question_parser.draft import refresh_plan_status
from question_parser.execute import Execution, execute_chain
from question_parser.interpret import dialog_action
from question_parser.memory import SessionStore, UserMemory
from question_parser.run import dry_run

_POINT_ANSWER = re.compile(r"\d+(?:[._]\d+)*")


@dataclass
class TurnResult:
    plan: SkillQueryPlan | TechnicalFailure | None
    execution: Execution
    user_id: str

    def to_dict(self) -> dict[str, Any]:
        plan: dict[str, Any] | None
        if isinstance(self.plan, (SkillQueryPlan, TechnicalFailure)):
            plan = self.plan.to_dict()
        else:
            plan = None
        return {"user_id": self.user_id, "plan": plan, "execution": self.execution.to_dict()}


def handle_turn(
    store: SessionStore,
    user_id: str,
    query: str,
    *,
    corpus: LocalCorpus,
    catalog: Catalog | None = None,
) -> TurnResult:
    catalog = catalog or load_catalog()
    memory = store.for_user(user_id)
    if memory.pending is not None:
        return _answer_clarification(memory, user_id, query, corpus, catalog)
    action = dialog_action(query, memory.context(inherit_document=False))
    if action == "new":
        memory.confirmed_doc_id = None
        memory.confirmed_point = None
        memory.conditions = []
    plan = dry_run(query, context=memory.context(inherit_document=action != "new"), catalog=catalog)
    if isinstance(plan, SkillQueryPlan):
        _bind_dialog(plan, memory, catalog, action)
        if plan.clarification:
            memory.pending = plan
            memory.plan = plan.to_dict()
            memory.remember_turn(query)
            return TurnResult(plan, Execution("waiting_clarification"), user_id)
        execution = execute_chain(plan, corpus, prior_results=memory.results)
        _remember(memory, plan, execution, action)
    else:
        execution = execute_chain(plan, corpus)
    memory.remember_turn(query)
    return TurnResult(plan, execution, user_id)


def resolve_clarification(plan: SkillQueryPlan, answer: str, catalog: Catalog) -> SkillQueryPlan | None:
    """Return the stored plan with one field filled in, or None when the answer does not match."""
    clarification = plan.clarification
    if not clarification:
        return plan
    chosen = _match_option(answer, clarification.get("options") or [])
    if chosen is None and clarification["field"] == "point":
        point = _POINT_ANSWER.fullmatch(answer.strip())
        if point:
            chosen = {"id": point.group(0), "label": point.group(0)}
    if chosen is None:
        return None
    updated = copy.deepcopy(plan)
    targets = clarification.get("target_task_ids") or [task["task_id"] for task in updated.tasks]
    field = clarification["field"]
    if field == "document":
        card = catalog.document(chosen["id"])
        for task in updated.tasks:
            if task["task_id"] not in targets:
                continue
            task["doc_id"] = chosen["id"]
            task["scope"] = "document"
            task["document_mention"] = card.name if card else chosen["label"]
        _drop(updated, "ambiguous_document", targets)
    elif field == "term":
        meaning = catalog.meaning(chosen["id"])
        for task in updated.tasks:
            if task["task_id"] not in targets:
                continue
            if meaning and task.get("concepts"):
                for concept in task["concepts"]:
                    concept["meaning_id"] = meaning.meaning_id
                    concept["variants"] = list(meaning.variants)
            elif meaning:
                task["concepts"] = [
                    {
                        "surface": meaning.label,
                        "meaning_id": meaning.meaning_id,
                        "variants": list(meaning.variants),
                        "evidence": [],
                    }
                ]
        _drop(updated, "ambiguous_term", targets)
    elif field == "point":
        for task in updated.tasks:
            if task["task_id"] in targets:
                task["point_ref"] = chosen["id"]
        _drop(updated, "missing_point", targets)
    else:
        return None
    return refresh_plan_status(updated, catalog)


def _answer_clarification(memory: UserMemory, user_id: str, query: str, corpus: LocalCorpus, catalog: Catalog) -> TurnResult:
    resolved = resolve_clarification(memory.pending, query, catalog)
    if resolved is None or resolved.clarification:
        if resolved is not None and resolved.clarification:
            memory.pending = resolved
        memory.remember_turn(query)
        plan = resolved or memory.pending
        return TurnResult(plan, Execution("waiting_clarification"), user_id)
    memory.pending = None
    execution = execute_chain(resolved, corpus, prior_results=memory.results)
    _remember(memory, resolved, execution, resolved.dialog_action)
    memory.plan = resolved.to_dict()
    memory.remember_turn(query)
    return TurnResult(resolved, execution, user_id)


def _bind_dialog(plan: SkillQueryPlan, memory: UserMemory, catalog: Catalog, action: str) -> None:
    if action == "correct" and memory.plan and memory.plan.get("tasks"):
        _apply_correction(plan, memory)
        return
    if action != "continue":
        return
    if memory.confirmed_doc_id and not _explicit_document_choice(plan):
        card = catalog.document(memory.confirmed_doc_id)
        for task in plan.tasks:
            if task.get("doc_id"):
                continue
            task["doc_id"] = memory.confirmed_doc_id
            task["scope"] = "document"
            task["document_mention"] = card.name if card else task.get("document_mention")
        plan.issues = [issue for issue in plan.issues if issue["code"] != "ambiguous_document"]
        refresh_plan_status(plan, catalog)
    if memory.conditions:
        for task in plan.tasks:
            have = {(item.get("kind"), item.get("polarity"), item.get("text")) for item in task.get("constraints") or []}
            for condition in memory.conditions:
                key = (condition.get("kind"), condition.get("polarity"), condition.get("text"))
                if key not in have:
                    task.setdefault("constraints", []).append(dict(condition))


def _explicit_document_choice(plan: SkillQueryPlan) -> bool:
    clarification = plan.clarification or {}
    return clarification.get("field") == "document" and bool(clarification.get("options"))


def _apply_correction(plan: SkillQueryPlan, memory: UserMemory) -> None:
    previous = memory.plan or {}
    fresh = [item for task in plan.tasks for item in task.get("constraints") or []]
    kinds = {item.get("kind") for item in fresh}
    kept = [item for item in memory.conditions if item.get("kind") not in kinds]
    merged = [dict(item) for item in kept] + [dict(item) for item in fresh]
    plan.tasks = []
    for task in previous.get("tasks") or []:
        row = copy.deepcopy(task)
        row["constraints"] = [dict(item) for item in merged]
        plan.tasks.append(row)
    plan.issues = copy.deepcopy(previous.get("issues") or [])
    plan.original_query = previous.get("original_query") or plan.original_query
    plan.complexity = previous.get("complexity")
    plan.dialog_action = "correct"
    plan.clarification = previous.get("clarification")
    plan.status = previous.get("status") or plan.status
    plan.trace = copy.deepcopy(previous.get("trace") or {})
    memory.conditions = merged


def _remember(memory: UserMemory, plan: SkillQueryPlan, execution: Execution, action: str) -> None:
    current = _condition_rows(plan)
    if action == "correct":
        memory.conditions = current
    elif action == "continue":
        memory.conditions = _union(memory.conditions, current)
    else:
        memory.conditions = current
    if action == "new":
        memory.confirmed_doc_id = None
        memory.confirmed_point = None
    for task in plan.tasks:
        if task.get("doc_id"):
            memory.confirmed_doc_id = task["doc_id"]
        if task.get("point_ref"):
            memory.confirmed_point = task["point_ref"]
    for result in execution.results.values():
        memory.cache_result(result)
    memory.plan = plan.to_dict()
    memory.pending = None


def _condition_rows(plan: SkillQueryPlan) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for task in plan.tasks:
        for item in task.get("constraints") or []:
            key = (item.get("kind"), item.get("polarity"), item.get("text"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"kind": item.get("kind"), "polarity": item.get("polarity"), "text": item.get("text")})
    return rows


def _union(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _unique(list(left) + list(right))


def _unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    kept = []
    for item in rows:
        key = (item.get("kind"), item.get("polarity"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(item))
    return kept


def _drop(plan: SkillQueryPlan, code: str, targets: list[str]) -> None:
    plan.issues = [
        issue
        for issue in plan.issues
        if not (issue["code"] == code and (not issue.get("task_ids") or set(issue["task_ids"]) & set(targets)))
    ]


def _match_option(answer: str, options: list[dict[str, str]]) -> dict[str, str] | None:
    folded = answer.strip().casefold().replace("ё", "е")
    if not folded:
        return None
    for option in options:
        if folded == str(option.get("id", "")).casefold():
            return option
    hits = []
    for option in options:
        label = str(option.get("label", "")).casefold().replace("ё", "е")
        if label and (label == folded or (len(folded) >= 8 and folded in label)):
            hits.append(option)
    if len(hits) == 1:
        return hits[0]
    return None
