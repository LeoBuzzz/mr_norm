"""Run a checked plan. Results are stored by task id.

A clarification holds the whole plan. A dependent task runs only when chain
execution is asked for. A point whose full text is already in hand is not
read again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.corpus import LocalCorpus
from question_parser.find_all_plan import FindAllConfig
from question_parser.definitions import definition_search
from question_parser.skills import corpus_point_search, exact_point, find_all

MAX_SKILL_CALLS = 6
_BLOCKING = {
    "unsupported_intent",
    "ambiguous_intent",
    "ambiguous_document",
    "ambiguous_term",
    "ambiguous_scope",
    "missing_point",
}
_DEPENDENCY_ONLY = "dependency_execution_not_in_stage1"


@dataclass
class Execution:
    status: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "calls": self.calls, "results": self.results, "errors": self.errors}


def skill_arguments(task: dict[str, Any], original_query: str) -> dict[str, Any]:
    concepts = task.get("concepts") or []
    needles = []
    for concept in concepts:
        needles.extend(concept.get("variants") or [])
        if concept.get("surface"):
            needles.append(concept["surface"])
    if not needles and task.get("query"):
        needles.append(task["query"])
    return {
        "task_id": task["task_id"],
        "skill": task.get("skill"),
        "original_query": original_query,
        "query": task.get("query"),
        "doc_id": task.get("doc_id"),
        "point_ref": task.get("point_ref"),
        "scope": task.get("scope"),
        "needles": needles,
        "constraints": list(task.get("constraints") or []),
        "depends_on": list(task.get("depends_on") or []),
    }


def execute_independent(
    plan: SkillQueryPlan | TechnicalFailure,
    corpus: LocalCorpus,
    *,
    prior_results: dict[str, dict[str, Any]] | None = None,
    find_all_config: FindAllConfig | None = None,
    planner_adapter: Callable[..., Any] | None = None,
) -> Execution:
    return execute(
        plan,
        corpus,
        allow_dependencies=False,
        prior_results=prior_results,
        find_all_config=find_all_config,
        planner_adapter=planner_adapter,
    )


def execute_chain(
    plan: SkillQueryPlan | TechnicalFailure,
    corpus: LocalCorpus,
    *,
    prior_results: dict[str, dict[str, Any]] | None = None,
    find_all_config: FindAllConfig | None = None,
    planner_adapter: Callable[..., Any] | None = None,
) -> Execution:
    return execute(
        plan,
        corpus,
        allow_dependencies=True,
        prior_results=prior_results,
        find_all_config=find_all_config,
        planner_adapter=planner_adapter,
    )


def execute(
    plan: SkillQueryPlan | TechnicalFailure,
    corpus: LocalCorpus,
    *,
    allow_dependencies: bool,
    prior_results: dict[str, dict[str, Any]] | None = None,
    find_all_config: FindAllConfig | None = None,
    planner_adapter: Callable[..., Any] | None = None,
) -> Execution:
    if isinstance(plan, TechnicalFailure):
        return Execution(
            "not_executed",
            errors=[{"layer": "parser", "code": plan.code, "task_id": None, "message": plan.message}],
        )
    if plan.clarification:
        return Execution("waiting_clarification")
    execution = Execution("not_executed")
    known = prior_results or {}
    order, blocked = _schedule(plan, allow_dependencies, execution)
    calls = 0
    for task_id in order:
        task = _task(plan, task_id)
        if task_id in blocked:
            _skip(execution, task, blocked[task_id])
            continue
        dependency_error = _dependency_error(task, execution.results)
        if dependency_error:
            _skip(execution, task, dependency_error)
            execution.errors.append(
                {"layer": "dispatcher", "code": dependency_error, "task_id": task_id, "message": dependency_error}
            )
            continue
        arguments = skill_arguments(task, plan.original_query)
        reused = _reuse(task, execution.results, known)
        if reused is not None:
            source_id, payload = reused
            execution.results[task_id] = {
                "task_id": task_id,
                "skill": task.get("skill"),
                "status": "reused",
                "doc_id": payload.get("doc_id"),
                "point_ref": payload.get("point_ref"),
                "text": payload.get("text"),
                "full_point_text": True,
                "hits": [],
                "reused_from": source_id,
                "arguments": arguments,
            }
            continue
        if calls >= MAX_SKILL_CALLS:
            _skip(execution, task, "spawn_cap")
            execution.errors.append(
                {"layer": "dispatcher", "code": "spawn_cap", "task_id": task_id, "message": "spawned call cap reached"}
            )
            continue
        outcome = _invoke(task, arguments, corpus, find_all_config, planner_adapter, execution)
        calls += 1
        execution.calls.append({"task_id": task_id, "skill": task.get("skill"), "arguments": arguments})
        execution.results[task_id] = outcome
    execution.status = _status(execution)
    return execution


def _task(plan: SkillQueryPlan, task_id: str) -> dict[str, Any]:
    return next(task for task in plan.tasks if task["task_id"] == task_id)


def _issue_codes(plan: SkillQueryPlan, task_id: str) -> set[str]:
    return {issue["code"] for issue in plan.issues if task_id in issue.get("task_ids", [])}


def _schedule(plan: SkillQueryPlan, allow_dependencies: bool, execution: Execution) -> tuple[list[str], dict[str, str]]:
    by_id = {task["task_id"]: task for task in plan.tasks}
    blocked: dict[str, str] = {}
    for task in plan.tasks:
        task_id = task["task_id"]
        codes = _issue_codes(plan, task_id)
        if not task.get("skill"):
            blocked[task_id] = "unsupported_intent"
        elif codes & _BLOCKING:
            blocked[task_id] = sorted(codes & _BLOCKING)[0]
        elif not allow_dependencies and (task.get("depends_on") or _DEPENDENCY_ONLY in codes):
            blocked[task_id] = _DEPENDENCY_ONLY
        else:
            missing = [dep for dep in task.get("depends_on") or [] if dep not in by_id]
            if missing:
                blocked[task_id] = "unknown_result"
                execution.errors.append(
                    {
                        "layer": "dispatcher",
                        "code": "unknown_result",
                        "task_id": task_id,
                        "message": "dependency does not name a task in this plan",
                    }
                )
    remaining = [task["task_id"] for task in plan.tasks if task["task_id"] not in blocked]
    indegree = {task_id: 0 for task_id in remaining}
    for task_id in remaining:
        for dep in by_id[task_id].get("depends_on") or []:
            if dep in indegree:
                indegree[task_id] += 1
    ready = [task_id for task_id in remaining if indegree[task_id] == 0]
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for other in remaining:
            deps = by_id[other].get("depends_on") or []
            if task_id in deps:
                indegree[other] -= 1
                if indegree[other] == 0 and other not in order and other not in ready:
                    ready.append(other)
    for task_id in remaining:
        if task_id not in order:
            blocked[task_id] = "cycle"
            execution.errors.append(
                {"layer": "dispatcher", "code": "cycle", "task_id": task_id, "message": "dependency cycle"}
            )
    runnable = [task_id for task_id in order if task_id not in blocked]
    skipped = [task["task_id"] for task in plan.tasks if task["task_id"] not in runnable]
    return runnable + skipped, blocked


def _dependency_error(task: dict[str, Any], results: dict[str, dict[str, Any]]) -> str | None:
    for dep in task.get("depends_on") or []:
        result = results.get(dep)
        if result is None or result.get("status") in {"skipped", "error"}:
            return "dependency_not_ready"
        if result.get("status") == "not_found":
            return "dependency_failed"
    return None


def _skip(execution: Execution, task: dict[str, Any], code: str) -> None:
    execution.results[task["task_id"]] = {
        "task_id": task["task_id"],
        "skill": task.get("skill"),
        "status": "skipped",
        "reason": code,
        "doc_id": task.get("doc_id"),
        "point_ref": task.get("point_ref"),
        "text": None,
        "full_point_text": False,
        "hits": [],
        "reused_from": None,
    }


def _reuse(
    task: dict[str, Any],
    results: dict[str, dict[str, Any]],
    prior: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    if task.get("skill") != "exact_point" or not task.get("doc_id") or not task.get("point_ref"):
        return None
    pools = list(results.items()) + list(prior.items())
    for source_id, result in pools:
        found = _full_point(result, task["doc_id"], task["point_ref"])
        if found is not None:
            return source_id, found
    return None


def _full_point(result: dict[str, Any], doc_id: str, point_ref: str) -> dict[str, Any] | None:
    if result.get("full_point_text") and result.get("doc_id") == doc_id and result.get("point_ref") == point_ref and result.get("text"):
        return result
    for hit in result.get("hits") or []:
        if hit.get("full_point_text") and hit.get("doc_id") == doc_id and hit.get("point_ref") == point_ref and hit.get("text"):
            return hit
    return None


def _invoke(
    task: dict[str, Any],
    arguments: dict[str, Any],
    corpus: LocalCorpus,
    find_all_config: FindAllConfig | None,
    planner_adapter: Callable[..., Any] | None,
    execution: Execution,
) -> dict[str, Any]:
    skill = task.get("skill")
    base = {
        "task_id": task["task_id"],
        "skill": skill,
        "reused_from": None,
        "arguments": arguments,
    }
    try:
        if skill == "exact_point":
            if not arguments["doc_id"] or not arguments["point_ref"]:
                execution.errors.append(
                    {
                        "layer": "dispatcher",
                        "code": "missing_argument",
                        "task_id": task["task_id"],
                        "message": "exact_point requires a document and a point",
                    }
                )
                return {**base, "status": "error", "code": "missing_argument", "doc_id": arguments["doc_id"], "point_ref": arguments["point_ref"], "text": None, "full_point_text": False, "hits": []}
            payload = exact_point(corpus, doc_id=arguments["doc_id"], point_ref=arguments["point_ref"])
        elif skill == "definition_search":
            payload = definition_search(query=arguments.get("query") or "", doc_id=arguments.get("doc_id"))
        elif skill == "vector_search":
            execution.errors.append(
                {
                    "layer": "search",
                    "code": "vector_store_not_connected",
                    "task_id": task["task_id"],
                    "message": "vector store is not connected",
                }
            )
            return {
                **base,
                "status": "skipped",
                "code": "vector_store_not_connected",
                "doc_id": arguments.get("doc_id"),
                "point_ref": None,
                "text": None,
                "full_point_text": False,
                "hits": [],
            }
        elif skill == "corpus_point_search":
            payload = corpus_point_search(
                corpus,
                needles=arguments["needles"],
                doc_id=arguments["doc_id"],
                constraints=arguments["constraints"],
            )
        elif skill == "find_all":
            payload = find_all(
                corpus,
                original_query=arguments["original_query"],
                needles=arguments["needles"],
                doc_id=arguments["doc_id"],
                constraints=arguments["constraints"],
                config=find_all_config,
                caller_aspects=task.get("caller_aspects"),
                planner_adapter=planner_adapter,
                saved_query_plan=task.get("saved_query_plan"),
            )
        else:
            execution.errors.append(
                {"layer": "dispatcher", "code": "unknown_skill", "task_id": task["task_id"], "message": f"unknown skill {skill}"}
            )
            return {**base, "status": "error", "code": "unknown_skill", "doc_id": arguments["doc_id"], "point_ref": arguments["point_ref"], "text": None, "full_point_text": False, "hits": []}
    except Exception as exc:
        execution.errors.append(
            {"layer": "search", "code": "skill_failed", "task_id": task["task_id"], "message": str(exc)}
        )
        return {**base, "status": "error", "code": "skill_failed", "doc_id": arguments["doc_id"], "point_ref": arguments["point_ref"], "text": None, "full_point_text": False, "hits": []}
    if payload.get("status") == "not_found":
        execution.errors.append(
            {"layer": "search", "code": "not_found", "task_id": task["task_id"], "message": "no point text matched"}
        )
    if payload.get("status") == "error":
        code = payload.get("code") or "skill_rejected"
        layer = "dispatcher" if str(code).startswith("saved_query_plan") else "search"
        execution.errors.append({"layer": layer, "code": code, "task_id": task["task_id"], "message": code})
    return {**base, **payload}


def _status(execution: Execution) -> str:
    statuses = [result.get("status") for result in execution.results.values()]
    if not statuses:
        return "not_executed"
    useful = {"ok", "reused"}
    if any(status in useful for status in statuses) and any(status not in useful for status in statuses):
        return "partial"
    if any(status in useful for status in statuses):
        return "executed"
    if any(error["layer"] == "dispatcher" for error in execution.errors) or any(status == "error" for status in statuses):
        return "failed"
    if any(status == "not_found" for status in statuses):
        return "missed"
    return "not_executed"
