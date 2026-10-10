"""Separate parser, dispatcher, and search failures, and compare a fixed baseline.

No model is called. Cost is therefore unknown rather than estimated. Connecting
this sandbox to a production path is intentionally not decided here.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from question_parser.catalog import Catalog, load_catalog
from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.corpus import LocalCorpus, build_demo_corpus
from question_parser.execute import execute_chain, execute_independent
from question_parser.find_all_plan import compare_aspect_plans
from question_parser.memory import SessionStore
from question_parser.run import dry_run
from question_parser.session import handle_turn
from question_parser.skills import find_all

REPORT_NAME = "eval_report.json"


def legacy_primary(query: str) -> str:
    """Skill priority of the deterministic router this sandbox is compared with."""
    text = " ".join(query.lower().replace("ё", "е").split())
    point = re.search(r"\bпункт\w*\s+(\d+(?:[._]\d+)*)\b", text)
    has_all = bool(re.search(r"\b(все|всё|всех|вытяни|перечень)\b", text))
    if re.search(r"\b(что такое|что означает|дай определение|определение)\b", text):
        return "definition_search"
    if point and not has_all:
        return "exact_point"
    if has_all:
        return "find_all"
    if re.search(r"\b(можно ли|применимо ли|должен|должна|должны)\b", text):
        return "applicability"
    return "corpus_point_search"


def _parser_skill(plan: SkillQueryPlan | TechnicalFailure) -> str | None:
    if not isinstance(plan, SkillQueryPlan) or not plan.tasks:
        return None
    return plan.tasks[0].get("skill")


def _case(case_id: str, *, expected_layer: str | None, observed_layer: str | None, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "expected_layer": expected_layer,
        "observed_layer": observed_layer,
        "passed": expected_layer == observed_layer,
        "detail": detail,
    }


def _first_layer(execution) -> str | None:
    if not execution.errors:
        return None
    return execution.errors[0]["layer"]


def build_report(catalog: Catalog | None = None, corpus: LocalCorpus | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    corpus = corpus or build_demo_corpus(catalog)
    started = time.perf_counter()
    cases = [
        _ready_point(catalog, corpus),
        _ambiguous_document(catalog, corpus),
        _definition_stays_deferred(catalog),
        _abbreviation_and_exclusion(catalog, corpus),
        _paraphrase_keeps_exclusion(catalog),
        _quantity_is_not_a_point(catalog),
        _new_question_drops_scope(catalog, corpus),
        _unsupported_conclusion(catalog),
        _parser_contract_error(catalog),
        _dispatcher_cycle(),
        _search_miss(catalog, corpus),
    ]
    comparisons = [_baseline_row(catalog, query) for query in _BASELINE_QUESTIONS]
    planner = _planner_comparison(corpus)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    unexpected = [case["case_id"] for case in cases if not case["passed"]]
    return {
        "schema": "question_parser_eval.v1",
        "production_connection": "not_connected",
        "runtime_model": None,
        "prompt_version": None,
        "model_calls": 0,
        "cost": None,
        "vector_store": "not_used",
        "elapsed_ms": elapsed_ms,
        "unexpected_errors": unexpected,
        "layer_counts": {
            "parser": sum(case["observed_layer"] == "parser" for case in cases),
            "dispatcher": sum(case["observed_layer"] == "dispatcher" for case in cases),
            "search": sum(case["observed_layer"] == "search" for case in cases),
            "none": sum(case["observed_layer"] is None for case in cases),
        },
        "cases": cases,
        "baseline": comparisons,
        "baseline_differences": [row["query"] for row in comparisons if row["skill"] != row["legacy"]],
        "planner_comparison": planner,
    }


_BASELINE_QUESTIONS = (
    "пункт 2 ГОСТ 19431",
    "что такое электроэнергетика",
    "все упоминания ЛЭП",
    "заземление",
    "применимо ли заземление",
    "напряжение 110 кВ",
)


def _baseline_row(catalog: Catalog, query: str) -> dict[str, Any]:
    plan = dry_run(query, catalog=catalog)
    skill = _parser_skill(plan)
    legacy = legacy_primary(query)
    return {"query": query, "skill": skill, "legacy": legacy, "same_skill": skill == legacy}


def _ready_point(catalog: Catalog, corpus: LocalCorpus) -> dict[str, Any]:
    plan = dry_run("пункт 2 ГОСТ 19431", catalog=catalog)
    execution = execute_independent(plan, corpus)
    layer = _first_layer(execution)
    ok = isinstance(plan, SkillQueryPlan) and plan.status == "ready" and execution.results["t1"]["status"] == "ok"
    return _case("ready-point", expected_layer=None, observed_layer=None if ok else layer or "parser", detail={"status": getattr(plan, "status", None), "calls": len(execution.calls)})


def _ambiguous_document(catalog: Catalog, corpus: LocalCorpus) -> dict[str, Any]:
    plan = dry_run("пункт 1.2.18 ПУЭ", catalog=catalog)
    execution = execute_independent(plan, corpus)
    ok = isinstance(plan, SkillQueryPlan) and plan.status == "needs_clarification" and execution.calls == []
    return _case(
        "ambiguous-document",
        expected_layer=None,
        observed_layer=None if ok else "parser",
        detail={"status": getattr(plan, "status", None), "calls": len(execution.calls)},
    )


def _definition_stays_deferred(catalog: Catalog) -> dict[str, Any]:
    plan = dry_run("что такое электроэнергетика", catalog=catalog)
    codes = [issue["code"] for issue in plan.issues] if isinstance(plan, SkillQueryPlan) else []
    ok = isinstance(plan, SkillQueryPlan) and plan.status == "deferred" and "unsupported_intent" in codes and plan.tasks[0]["skill"] is None
    return _case("definition-deferred", expected_layer=None, observed_layer=None if ok else "parser", detail={"issues": codes})


def _abbreviation_and_exclusion(catalog: Catalog, corpus: LocalCorpus) -> dict[str, Any]:
    query = "все упоминания ЛЭП, то есть линия электропередачи, кроме кабельных"
    plan = dry_run(query, catalog=catalog)
    execution = execute_independent(plan, corpus)
    result = execution.results.get("t1", {})
    refs = {hit["point_ref"] for hit in result.get("hits") or []}
    kept = isinstance(plan, SkillQueryPlan) and plan.original_query == query
    queries = (result.get("query_plan") or {}).get("executed_queries") or []
    ok = (
        kept
        and result.get("status") == "ok"
        and refs == {"3"}
        and queries == [query]
        and len(plan.tasks[0]["concepts"]) == 1
    )
    return _case("abbreviation-exclusion", expected_layer=None, observed_layer=None if ok else "search", detail={"points": sorted(refs), "queries": queries})


def _paraphrase_keeps_exclusion(catalog: Catalog) -> dict[str, Any]:
    plan = dry_run("все случаи линия электропередачи, кроме кабельных", catalog=catalog)
    kinds = [item["kind"] for item in plan.tasks[0]["constraints"]] if isinstance(plan, SkillQueryPlan) else []
    ok = isinstance(plan, SkillQueryPlan) and plan.tasks[0]["skill"] == "find_all" and "exclusion" in kinds
    return _case("paraphrase-exclusion", expected_layer=None, observed_layer=None if ok else "parser", detail={"constraints": kinds})


def _quantity_is_not_a_point(catalog: Catalog) -> dict[str, Any]:
    plan = dry_run("напряжение 110 кВ", catalog=catalog)
    ok = isinstance(plan, SkillQueryPlan) and plan.tasks[0]["skill"] == "corpus_point_search" and plan.tasks[0]["point_ref"] is None
    return _case("quantity-not-point", expected_layer=None, observed_layer=None if ok else "parser", detail={"skill": _parser_skill(plan)})


def _new_question_drops_scope(catalog: Catalog, corpus: LocalCorpus) -> dict[str, Any]:
    store = SessionStore()
    handle_turn(store, "eva", "пункт 2 ГОСТ 19431", corpus=corpus, catalog=catalog)
    follow = handle_turn(store, "eva", "заземление", corpus=corpus, catalog=catalog)
    call = follow.execution.calls[0]["arguments"] if follow.execution.calls else {}
    ok = call.get("doc_id") is None and call.get("skill") == "corpus_point_search"
    return _case("new-question-scope", expected_layer=None, observed_layer=None if ok else "dispatcher", detail={"doc_id": call.get("doc_id"), "skill": call.get("skill")})


def _unsupported_conclusion(catalog: Catalog) -> dict[str, Any]:
    plan = dry_run("применимо ли заземление", catalog=catalog)
    ok = isinstance(plan, SkillQueryPlan) and plan.status == "deferred" and plan.tasks[0]["skill"] is None
    return _case("unsupported-conclusion", expected_layer=None, observed_layer=None if ok else "parser", detail={"status": getattr(plan, "status", None)})


def _parser_contract_error(catalog: Catalog) -> dict[str, Any]:
    draft = {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:1"], "topic": "тема current@1:1", "document": "corpus"}]}
    plan = dry_run("заземление", catalog=catalog, draft=draft)
    layer = "parser" if isinstance(plan, TechnicalFailure) else None
    return _case("parser-contract", expected_layer="parser", observed_layer=layer, detail={"code": getattr(plan, "code", None)})


def _dispatcher_cycle() -> dict[str, Any]:
    from question_parser.contracts import SkillQueryPlan

    plan = SkillQueryPlan(
        "skill_query_plan.v1",
        "заземление",
        "deferred",
        "complex",
        "new",
        [
            _search_task("t1", "заземление", ["t2"]),
            _search_task("t2", "сеть", ["t1"]),
        ],
    )
    execution = execute_chain(plan, LocalCorpus([]))
    layer = "dispatcher" if any(error["code"] == "cycle" for error in execution.errors) and execution.calls == [] else _first_layer(execution)
    return _case("dispatcher-cycle", expected_layer="dispatcher", observed_layer=layer, detail={"calls": len(execution.calls)})


def _search_miss(catalog: Catalog, corpus: LocalCorpus) -> dict[str, Any]:
    plan = dry_run("пункт 99 ГОСТ 19431", catalog=catalog)
    execution = execute_independent(plan, corpus)
    layer = "search" if any(error["code"] == "not_found" for error in execution.errors) else _first_layer(execution)
    return _case("search-miss", expected_layer="search", observed_layer=layer, detail={"status": execution.status})


def _search_task(task_id: str, query: str, depends_on: list[str]) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "operation": "search",
        "skill": "corpus_point_search",
        "query": query,
        "scope": "corpus",
        "doc_id": None,
        "document_mention": None,
        "point_ref": None,
        "coverage": "ranked",
        "concepts": [],
        "constraints": [],
        "depends_on": depends_on,
    }


def _planner_comparison(corpus: LocalCorpus) -> dict[str, Any]:
    query = "линия электропередачи кроме кабельных"
    applied = ["кабельных"]

    def good(question: str) -> dict[str, Any]:
        return {
            "aspects": [{"text": "воздушной линии", "rationale": "отдельная опора"}],
            "constraints": ["кроме кабельных"],
        }

    def bad(question: str) -> dict[str, Any]:
        return {"aspects": [{"text": "воздушной линии", "rationale": "отдельная опора"}], "constraints": []}

    kept = compare_aspect_plans(query, applied_constraints=applied, planner_adapter=good)
    lost = compare_aspect_plans(query, applied_constraints=applied, planner_adapter=bad)
    base = find_all(corpus, original_query=query, needles=["линия электропередачи"], doc_id=None, constraints=[{"kind": "exclusion", "polarity": "exclude", "text": "кабельных"}], config=_off())
    wider = find_all(
        corpus,
        original_query=query,
        needles=["линия электропередачи"],
        doc_id=None,
        constraints=[{"kind": "exclusion", "polarity": "exclude", "text": "кабельных"}],
        config=_llm(),
        planner_adapter=good,
    )
    return {
        "original_query": query,
        "off_points": sorted(hit["point_ref"] for hit in base["hits"]),
        "aspect_points": sorted(hit["point_ref"] for hit in wider["hits"]),
        "good_adapter": {"status": kept["llm_status"], "dropped_constraints": kept["dropped_constraints"], "aspects": kept["llm_aspects"]},
        "bad_adapter": {"status": lost["llm_status"], "failure": lost["planner_failure"], "aspects": lost["llm_aspects"]},
    }


def _off():
    from question_parser.find_all_plan import FindAllConfig

    return FindAllConfig(planner_mode="off")


def _llm():
    from question_parser.find_all_plan import FindAllConfig

    return FindAllConfig(planner_mode="llm")


def write_report(path: Path | None = None, catalog: Catalog | None = None, corpus: LocalCorpus | None = None) -> dict[str, Any]:
    report = build_report(catalog, corpus)
    target = path or Path(__file__).resolve().parent / REPORT_NAME
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    write_report()
