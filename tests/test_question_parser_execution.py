import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

from question_parser.catalog import load_catalog
from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.corpus import LocalCorpus, build_demo_corpus
from question_parser.evaluate import build_report, legacy_primary, write_report
from question_parser.execute import execute_chain, execute_independent
from question_parser.find_all_plan import FindAllConfig, build_query_plan
from question_parser.memory import SessionStore
from question_parser.run import dry_run
from question_parser.session import handle_turn

CATALOG = load_catalog()
ROOT = Path(__file__).resolve().parents[1]


def _corpus() -> LocalCorpus:
    return build_demo_corpus(CATALOG)


def test_independent_steps_run_once_and_keep_both_results():
    corpus = _corpus()
    draft = {
        "dialog": "new",
        "tasks": [
            {"operation": "search", "source": ["current@1:1"], "topic": "заземление", "document": "corpus"},
            {"operation": "search", "source": ["current@1:1"], "topic": "электроэнергетика", "document": "corpus"},
        ],
    }
    plan = dry_run("заземление электроэнергетика", draft=draft, catalog=CATALOG)
    execution = execute_independent(plan, corpus)
    assert execution.status == "executed"
    assert [call["task_id"] for call in execution.calls] == ["t1", "t2"]
    assert execution.calls[0]["skill"] == execution.calls[1]["skill"] == "corpus_point_search"
    assert execution.results["t1"]["arguments"]["query"] == "заземление"
    assert execution.results["t2"]["arguments"]["query"] == "электроэнергетика"
    assert {hit["point_ref"] for hit in execution.results["t1"]["hits"]} == {"1.7.1"}
    assert {hit["point_ref"] for hit in execution.results["t2"]["hits"]} == {"2"}


def test_clarification_does_not_search_until_the_field_is_answered():
    corpus = _corpus()
    store = SessionStore()
    first = handle_turn(store, "anna", "пункт 1.2.18 ПУЭ кроме резерва", corpus=corpus, catalog=CATALOG)
    assert first.execution.status == "waiting_clarification"
    assert first.execution.calls == []
    assert corpus.point_reads == 0
    vague = handle_turn(store, "anna", "ПУЭ", corpus=corpus, catalog=CATALOG)
    assert vague.execution.calls == []
    assert corpus.point_reads == 0
    chosen = next(point.doc_id for point in corpus.points if point.point_ref == "1.2.18")
    done = handle_turn(store, "anna", chosen, corpus=corpus, catalog=CATALOG)
    assert done.execution.calls[0]["skill"] == "exact_point"
    assert done.execution.calls[0]["arguments"]["doc_id"] == chosen
    assert done.execution.calls[0]["arguments"]["point_ref"] == "1.2.18"
    assert any(item["text"] == "резерва" for item in done.execution.calls[0]["arguments"]["constraints"])
    assert done.execution.results["t1"]["status"] == "ok"
    assert len(done.execution.calls) == 1


def test_chain_runs_dependencies_and_independent_mode_does_not():
    corpus = _corpus()
    pue = next(point.doc_id for point in corpus.points if point.point_ref == "1.7.1")
    plan = SkillQueryPlan(
        "skill_query_plan.v1",
        "заземление",
        "deferred",
        "complex",
        tasks=[
            _task("t1", []),
            {
                "task_id": "t2",
                "operation": "text",
                "skill": "exact_point",
                "query": "заземление",
                "scope": "document",
                "doc_id": pue,
                "document_mention": None,
                "point_ref": "1.7.1",
                "coverage": "exact",
                "concepts": [],
                "constraints": [],
                "depends_on": ["t1"],
            },
        ],
        issues=[{"code": "dependency_execution_not_in_stage1", "task_ids": ["t2"], "message": "deferred"}],
    )
    held = execute_independent(plan, corpus)
    assert [call["task_id"] for call in held.calls] == ["t1"]
    assert held.results["t2"]["status"] == "skipped"
    ran = execute_chain(plan, corpus)
    assert [call["task_id"] for call in ran.calls] == ["t1"]
    assert ran.results["t1"]["status"] == "ok"
    assert ran.results["t2"]["status"] == "reused"
    assert corpus.point_reads == 0


def test_cycle_unknown_result_and_spawn_cap_are_dispatcher_errors():
    corpus = _corpus()
    cycle = SkillQueryPlan(
        "skill_query_plan.v1",
        "заземление",
        "deferred",
        "complex",
        tasks=[_task("t1", ["t2"]), _task("t2", ["t1"])],
    )
    cycled = execute_chain(cycle, corpus)
    assert cycled.calls == []
    assert {error["code"] for error in cycled.errors} == {"cycle"}
    unknown = SkillQueryPlan(
        "skill_query_plan.v1",
        "заземление",
        "deferred",
        "complex",
        tasks=[_task("t1", []), _task("t2", ["t9"])],
    )
    missed = execute_chain(unknown, corpus)
    assert [call["task_id"] for call in missed.calls] == ["t1"]
    assert any(error["code"] == "unknown_result" and error["task_id"] == "t2" for error in missed.errors)
    wide = SkillQueryPlan(
        "skill_query_plan.v1",
        "заземление",
        "ready",
        "simple",
        tasks=[_task(f"t{index}", [], query="заземление") for index in range(1, 8)],
    )
    capped = execute_chain(wide, corpus)
    assert len(capped.calls) == 6
    assert capped.results["t7"]["reason"] == "spawn_cap"


def test_full_point_text_is_not_read_twice_and_users_do_not_share_it():
    corpus = _corpus()
    store = SessionStore()
    first = handle_turn(store, "alice", "пункт 2 ГОСТ 19431", corpus=corpus, catalog=CATALOG)
    assert first.execution.results["t1"]["status"] == "ok"
    assert corpus.point_reads == 1
    second = handle_turn(store, "alice", "пункт 2 ГОСТ 19431", corpus=corpus, catalog=CATALOG)
    assert second.execution.results["t1"]["status"] == "reused"
    assert corpus.point_reads == 1
    other = handle_turn(store, "bob", "пункт 2 ГОСТ 19431", corpus=corpus, catalog=CATALOG)
    assert other.execution.results["t1"]["status"] == "ok"
    assert corpus.point_reads == 2
    assert store.for_user("alice").confirmed_doc_id == store.for_user("bob").confirmed_doc_id
    assert store.for_user("alice") is not store.for_user("bob")


def test_continue_inherits_document_new_question_does_not_and_correction_replaces_condition():
    corpus = _corpus()
    store = SessionStore()
    opened = handle_turn(store, "mira", "пункт 2 ГОСТ 19431", corpus=corpus, catalog=CATALOG)
    gost = opened.execution.results["t1"]["doc_id"]
    follow = handle_turn(store, "mira", "а пункт 3", corpus=corpus, catalog=CATALOG)
    assert follow.execution.calls[0]["arguments"]["doc_id"] == gost
    assert follow.execution.calls[0]["arguments"]["point_ref"] == "3"
    fresh = handle_turn(store, "mira", "пункт 3", corpus=corpus, catalog=CATALOG)
    assert fresh.execution.calls == []
    assert fresh.execution.status == "waiting_clarification"
    assert store.for_user("mira").confirmed_doc_id is None
    wide = handle_turn(
        store,
        "nika",
        "все упоминания ЛЭП, то есть линия электропередачи, кроме кабельных",
        corpus=corpus,
        catalog=CATALOG,
    )
    assert {hit["point_ref"] for hit in wide.execution.results["t1"]["hits"]} == {"3"}
    corrected = handle_turn(store, "nika", "нет, кроме воздушных", corpus=corpus, catalog=CATALOG)
    texts = [item["text"] for item in store.for_user("nika").conditions]
    assert "воздушных" in texts
    assert "кабельных" not in texts
    assert {hit["point_ref"] for hit in corrected.execution.results["t1"]["hits"]} == {"4"}
    assert corrected.plan.original_query.startswith("все упоминания")


def test_partial_result_and_search_failure_stay_on_the_search_layer():
    corpus = _corpus()
    draft = {
        "dialog": "new",
        "tasks": [
            {"operation": "search", "source": ["current@1:1"], "topic": "заземление", "document": "corpus"},
            {"operation": "search", "source": ["current@1:1"], "topic": "отсутствующееслово", "document": "corpus"},
        ],
    }
    plan = dry_run("заземление", draft=draft, catalog=CATALOG)
    execution = execute_independent(plan, corpus)
    assert execution.status == "partial"
    assert execution.results["t1"]["status"] == "ok"
    assert execution.results["t2"]["status"] == "not_found"
    assert execution.errors[0]["layer"] == "search"

    class Boom(LocalCorpus):
        def matching(self, doc_id=None):
            self.point_reads += 0
            raise RuntimeError("disk read failed")

    failed = execute_independent(plan, Boom(corpus.points))
    assert failed.results["t1"]["status"] == "error"
    assert failed.errors[0]["layer"] == "search"
    assert failed.errors[0]["code"] == "skill_failed"


def test_find_all_keeps_one_plan_and_rejects_a_mismatched_saved_plan():
    corpus = _corpus()
    query = "все упоминания ЛЭП, то есть линия электропередачи, кроме кабельных"
    plan = dry_run(query, catalog=CATALOG)
    called = {"count": 0}

    def adapter(_question: str):
        called["count"] += 1
        raise AssertionError("planner must stay off")

    execution = execute_independent(plan, corpus, planner_adapter=adapter, find_all_config=FindAllConfig(planner_mode="off"))
    result = execution.results["t1"]
    assert called["count"] == 0
    assert result["query_plan"]["original_query"] == query
    assert result["query_plan"]["executed_queries"] == [query]
    assert result["query_plan"]["aspects"] == []
    plan.tasks[0]["saved_query_plan"] = {"original_query": "другой вопрос"}
    rejected = execute_independent(plan, corpus)
    assert rejected.results["t1"]["code"] == "saved_query_plan_query_mismatch"
    assert rejected.errors[0]["layer"] == "dispatcher"
    assert rejected.results["t1"]["hits"] == []


def test_aspect_cap_and_comparison_record_constraint_loss():
    plan = build_query_plan("заземление", caller_aspects=["один", "два", "три", "четыре", "пять"], config=FindAllConfig(max_aspects=4))
    assert len(plan["aspects"]) == 4
    assert plan["skipped_aspects"][-1]["reason"] == "max_aspects"
    try:
        build_query_plan("заземление", saved_query_plan={"original_query": "другое"})
    except ValueError as exc:
        assert str(exc) == "saved_query_plan_query_mismatch"
    else:
        raise AssertionError("mismatched saved plan was accepted")
    report = build_report(CATALOG, _corpus())
    comparison = report["planner_comparison"]
    assert comparison["off_points"] == ["3"]
    assert comparison["aspect_points"] == ["3", "5"]
    assert comparison["good_adapter"]["dropped_constraints"] == []
    assert comparison["bad_adapter"]["status"] == "fallback"
    assert comparison["bad_adapter"]["aspects"] == []
    assert "что такое электроэнергетика" in report["baseline_differences"]
    assert report["model_calls"] == 0
    assert report["cost"] is None
    assert report["production_connection"] == "not_connected"
    assert report["unexpected_errors"] == []
    assert {row["legacy"] for row in report["baseline"]} >= {"definition_search", "exact_point", "find_all", "applicability"}
    assert legacy_primary("пункт 2 ГОСТ 19431") == "exact_point"


def test_definition_request_is_not_executed():
    corpus = _corpus()
    plan = dry_run("что такое электроэнергетика", catalog=CATALOG)
    execution = execute_chain(plan, corpus)
    assert execution.calls == []
    assert execution.results["t1"]["status"] == "skipped"


def test_technical_failure_is_a_parser_error_and_sandbox_has_no_vector_client():
    plan = dry_run("заземление", catalog=CATALOG, draft={"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:1"], "topic": "тема current@1:1", "document": "corpus"}]})
    execution = execute_independent(plan, _corpus())
    assert isinstance(plan, TechnicalFailure)
    assert execution.calls == []
    assert execution.errors[0]["layer"] == "parser"
    for path in (ROOT / "sandbox" / "question_parser").glob("*.py"):
        assert "qdrant" not in path.read_text(encoding="utf-8").lower()


def test_three_skills_read_a_real_glossary_file():
    path = Path("/tmp/corpus/rtf/ГОСТ 19431-2023 Энергетика и электрификация. Термины и определения.rtf")
    if not path.exists():
        pytest.skip("glossary file is not in the local cache")
    sys.path.insert(0, str(ROOT))
    from scripts.extract_glossary_cards import extract_definitions, rtf_to_plain

    from question_parser.corpus import LocalPoint
    from question_parser.skills import corpus_point_search, exact_point, find_all

    cards = extract_definitions(rtf_to_plain(path))
    corpus = LocalCorpus(
        [LocalPoint("gost", card["id"], f"{card['head']}: {card['context']}") for card in cards]
    )
    point = exact_point(corpus, doc_id="gost", point_ref="2")
    assert point["status"] == "ok"
    assert "Раздел энергетики" in point["text"]
    found = corpus_point_search(corpus, needles=["электроэнергетика"], doc_id="gost", constraints=[])
    assert found["status"] == "ok"
    assert any(hit["point_ref"] == "2" for hit in found["hits"])
    wide = find_all(
        corpus,
        original_query="линия электропередачи кроме кабельных",
        needles=["линия электропередачи"],
        doc_id="gost",
        constraints=[{"kind": "exclusion", "polarity": "exclude", "text": "кабельных"}],
    )
    assert wide["status"] == "ok"
    assert any(hit["point_ref"] == "38" for hit in wide["hits"])
    assert all("кабельных" not in hit["text"].casefold() for hit in wide["hits"])
    assert wide["query_plan"]["original_query"] == "линия электропередачи кроме кабельных"


def test_committed_eval_report_matches_a_fresh_run():
    fresh = build_report(CATALOG, _corpus())
    target = ROOT / "sandbox" / "question_parser" / "eval_report.json"
    write_report(target, CATALOG, _corpus())
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert _without_time(saved) == _without_time(fresh)


def _without_time(report: dict) -> dict:
    copy = dict(report)
    copy.pop("elapsed_ms", None)
    return copy


def _task(task_id: str, depends_on: list[str], query: str = "заземление") -> dict:
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
        "concepts": [{"surface": query, "meaning_id": None, "variants": [query], "evidence": []}],
        "constraints": [],
        "depends_on": depends_on,
    }
