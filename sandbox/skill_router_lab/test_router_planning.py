from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from mr_norm.retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
from mr_norm.retrieval.document_knowledge import DocumentKnowledgeIndex

from router_contracts import RouterPlanConfig
from router_planning import _extract_open_point_refs, _open_pending_value_is_grounded, _validate_pending_clarification, _validate_pending_result, plan_skill_query
from run_router_plan_eval import compare_case, compare_metamorphic_pair, validate_cases


DOC_PUE = "doc_1e4a178aa5114b30"
DOC_POT = "doc_ad116a1f6b41adb1"


class QueueAdapter:
    def __init__(self, responses: list[dict | str], *, fail_roles: set[str] | None = None) -> None:
        self.responses = list(responses)
        self.fail_roles = fail_roles or set()
        self.calls: list[dict] = []

    def __call__(self, *, role: str, messages: list[dict[str, str]], model: str, timeout: float, max_tokens: int) -> dict:
        self.calls.append({"role": role, "messages": messages, "model": model, "timeout": timeout, "max_tokens": max_tokens})
        if role in self.fail_roles:
            raise TimeoutError(f"{role} timed out")
        if not self.responses:
            raise AssertionError("adapter called more times than expected")
        item = self.responses.pop(0)
        if isinstance(item, str):
            return {"content": item, "model": model, "usage": {"total_tokens": 7}}
        result = dict(item)
        result.setdefault("model", model)
        return result


@pytest.fixture()
def catalog() -> DocumentCatalog:
    return DocumentCatalog(
        entries=[
            DocumentCatalogEntry(catalog_id="pue", doc_id=DOC_PUE, doc_name="ПУЭ", aliases=("пуэ", "правила устройства электроустановок")),
            DocumentCatalogEntry(catalog_id="pot", doc_id=DOC_POT, doc_name="Правила по охране труда", aliases=("правила по охране труда",)),
        ]
    )


@pytest.fixture()
def knowledge() -> DocumentKnowledgeIndex:
    return DocumentKnowledgeIndex(
        abbreviations=[
            {"abbreviation": "ЛЭП", "expansion": "линия электропередачи"},
            {"abbreviation": "ЛЭП", "expansion": "воздушная линия электропередачи"},
        ],
        terms=[{"label": "заземление"}, {"label": "работа под наведённым напряжением"}],
    )


@pytest.fixture()
def config() -> RouterPlanConfig:
    return RouterPlanConfig("light-model", "heavy-model", "fake-provider", max_query_chars=120, max_context_chars=4000)


def classifier(complexity: str = "simple", reasons: list[str] | None = None) -> str:
    return json.dumps({"complexity": complexity, "reason_codes": reasons or ["single_intent"]}, ensure_ascii=False)


def evidence(quote: str, turn_id: str = "current") -> list[dict[str, str]]:
    return [{"turn_id": turn_id, "quote": quote}]


def task(
    *,
    task_id: str = "t1",
    skill: str | None = "corpus_point_search",
    query: str = "Найди требования к заземлению",
    intent_text: str | None = None,
    ev: list[dict[str, str]] | None = None,
    scope: str = "corpus",
    doc_id: str | None = None,
    document_mention: str | None = None,
    point_ref: str | None = None,
    coverage: str = "ranked",
    concepts: list[dict] | None = None,
    constraints: list[dict] | None = None,
    depends_on: list[str] | None = None,
    replaces: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "intent_text": intent_text or query,
        "evidence": ev or evidence(query),
        "skill": skill,
        "query": query,
        "scope": scope,
        "doc_id": doc_id,
        "document_mention": document_mention,
        "point_ref": point_ref,
        "coverage": coverage,
        "concepts": concepts or [],
        "constraints": constraints or [],
        "depends_on": depends_on or [],
        "replaces": replaces or [],
    }


def parser(tasks: list[dict], *, action: str = "new", clarification: dict | None = None, issues: list[dict] | None = None) -> str:
    return json.dumps(
        {"dialog_action": action, "tasks": tasks, "clarification": clarification, "issues": issues or []},
        ensure_ascii=False,
    )


def run_plan(query: str, adapter: QueueAdapter, catalog: DocumentCatalog, knowledge: DocumentKnowledgeIndex, config: RouterPlanConfig, **kwargs):
    return plan_skill_query(query, catalog=catalog, knowledge=knowledge, llm_adapter=adapter, config=config, **kwargs)


def test_simple_request_uses_light_parser_and_strict_trace(catalog, knowledge, config):
    adapter = QueueAdapter([
        classifier("simple"),
        parser([task(query="Найди требования к заземлению")]),
    ])

    plan = run_plan("Найди требования к заземлению", adapter, catalog, knowledge, config)

    assert plan.status == "ready"
    assert plan.complexity == "simple"
    assert [call["role"] for call in adapter.calls] == ["classifier", "simple_parser"]
    assert [call["model"] for call in adapter.calls] == ["light-model", "light-model"]
    assert adapter.calls[0]["max_tokens"] == config.classifier_max_tokens
    assert adapter.calls[1]["max_tokens"] == config.parser_max_tokens
    assert plan.trace["physical_call_count"] == 2
    assert plan.trace["logical_call_count"] == 2


def test_complex_request_uses_heavy_parser_and_preserves_multiple_exact_points(catalog, knowledge, config):
    quote = "Покажи пункты 6.25 и 6.26 правил по охране труда"
    adapter = QueueAdapter([
        classifier("complex", ["multiple_targets"]),
        parser([
            task(task_id="t1", skill="exact_point", query="пункт 6.25", ev=evidence(quote), scope="document", doc_id=DOC_POT, document_mention="правил по охране труда", point_ref="6.25", coverage="exact"),
            task(task_id="t2", skill="exact_point", query="пункт 6.26", ev=evidence(quote), scope="document", doc_id=DOC_POT, document_mention="правил по охране труда", point_ref="6.26", coverage="exact"),
        ]),
    ])

    plan = run_plan(quote, adapter, catalog, knowledge, config)

    assert plan.status == "ready"
    assert [call["role"] for call in adapter.calls] == ["classifier", "complex_parser"]
    assert adapter.calls[1]["model"] == "heavy-model"
    assert [item["point_ref"] for item in plan.tasks] == ["6.25", "6.26"]


def test_malformed_parser_response_gets_one_repair_and_stays_within_three_logical_calls(catalog, knowledge, config):
    adapter = QueueAdapter([
        classifier("simple"),
        '{"dialog_action":"new","tasks":[],"clarification":null,"issues":[],"extra":true}',
        parser([task(query="Найди требования к заземлению")]),
    ])

    plan = run_plan("Найди требования к заземлению", adapter, catalog, knowledge, config)

    assert plan.status == "ready"
    assert plan.trace["repair_count"] == 1
    assert plan.trace["logical_call_count"] == 3
    assert [call["role"] for call in adapter.calls] == ["classifier", "simple_parser", "simple_parser"]
    repair_payload = json.loads(adapter.calls[2]["messages"][1]["content"])
    assert "validation_errors" in repair_payload


@pytest.mark.parametrize(
    "broken_task, message",
    [
        (lambda: {**task(skill="definition_search"), "skill": "definition_search"}, "unknown skill"),
        (lambda: {**task(scope="document", doc_id="missing_doc"), "scope": "document", "doc_id": "missing_doc"}, "document scope"),
        (lambda: {**task(ev=evidence("нет такой цитаты")), "evidence": evidence("нет такой цитаты")}, "evidence quote"),
        (lambda: {**task(depends_on=["t1"]), "depends_on": ["t1"]}, "unknown or forward dependency"),
    ],
)
def test_invalid_skill_doc_evidence_and_self_dependency_fail_closed(broken_task, message, catalog, knowledge, config):
    adapter = QueueAdapter([classifier("simple"), parser([broken_task()]), parser([broken_task()])])

    plan = run_plan("Найди требования к заземлению", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert plan.issues[0]["code"] == "parser_error"
    assert message in plan.issues[0]["message"]
    assert plan.trace["physical_call_count"] == 3


def test_more_than_six_tasks_fail_closed(catalog, knowledge, config):
    too_many = [task(task_id=f"t{i}", query="Найди требования к заземлению") for i in range(1, 8)]
    adapter = QueueAdapter([classifier("complex", ["multiple_targets"]), parser(too_many), parser(too_many)])

    plan = run_plan("Найди требования к заземлению", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert "task count" in plan.issues[0]["message"]


def test_input_limits_stop_before_llm_calls(catalog, knowledge, config):
    adapter = QueueAdapter([])

    plan = run_plan("x" * (config.max_query_chars + 1), adapter, catalog, knowledge, config)

    assert plan.status == "input_limit_exceeded"
    assert plan.issues[0]["code"] == "query_too_long"
    assert adapter.calls == []


def test_context_snapshot_is_immutable_and_context_doc_must_exist(catalog, knowledge, config):
    context = {"turns": [{"turn_id": "turn_1", "user_text": "Покажи пункт 1.1 ПУЭ", "resolved_doc_id": DOC_PUE}]}
    before = copy.deepcopy(context)
    adapter = QueueAdapter([
        classifier("simple"),
        parser(
            [
                task(
                    skill="exact_point",
                    query="пункт 3.2",
                    ev=[*evidence("пункт 3.2"), *evidence("ПУЭ", "turn_1")],
                    scope="document",
                    doc_id=DOC_PUE,
                    document_mention="ПУЭ",
                    point_ref="3.2",
                    coverage="exact",
                )
            ],
            action="continue",
        ),
    ])

    plan = run_plan(
        "пункт 3.2",
        adapter,
        catalog,
        knowledge,
        config,
        dialog_context=context,
    )

    assert context == before
    assert plan.status == "ready"
    stale = {"turns": [{"turn_id": "turn_1", "user_text": "Вопрос по исчезнувшему документу", "resolved_doc_id": "doc_missing"}]}
    stale_plan = run_plan("пункт 3.2", QueueAdapter([]), catalog, knowledge, config, dialog_context=stale)
    assert stale_plan.status == "error"
    assert stale_plan.issues[0]["code"] == "invalid_context_doc_id"


@pytest.mark.parametrize("action", ["new", "continue", "correct", "clarify"])
def test_dialog_actions_are_accepted_when_grounded(action, catalog, knowledge, config):
    context = {
        "turns": [
            {
                "turn_id": "turn_1",
                "user_text": "Найди все требования к электростанциям",
                "confirmed_conditions": [{"id": "turn_1:c1", "text": "электростанции", "quote": "электростанциям", "confirmed": True}],
            }
        ]
    }
    constraints = [{"id": "c1", "kind": "object", "text": "подстанции", "polarity": "include", "evidence": evidence("подстанции")}]
    replaces = ["turn_1:c1"] if action == "correct" else []
    adapter = QueueAdapter([
        classifier("complex", ["context_correction"]),
        parser([task(skill="find_all", query="подстанции", ev=evidence("подстанции"), coverage="comprehensive", constraints=constraints, replaces=replaces)], action=action),
    ])

    plan = run_plan("Не электростанции, а подстанции", adapter, catalog, knowledge, config, dialog_context=context)

    assert plan.dialog_action == action
    assert plan.status == "ready"


def test_abbreviation_variants_from_dictionary_are_allowed_and_preserved(catalog, knowledge, config):
    concept = {
        "surface": "ЛЭП",
        "variants": ["линия электропередачи", "воздушная линия электропередачи", "ЛЭП"],
        "evidence": evidence("ЛЭП"),
    }
    constraint = {"id": "c1", "kind": "object", "text": "ЛЭП", "polarity": "include", "evidence": evidence("ЛЭП")}
    adapter = QueueAdapter([
        classifier("simple"),
        parser([task(skill="find_all", query="работа под наведённым напряжением на ЛЭП", ev=evidence("ЛЭП"), coverage="comprehensive", concepts=[concept], constraints=[constraint])]),
    ])

    plan = run_plan("Найди все пункты про работу под наведённым напряжением на ЛЭП", adapter, catalog, knowledge, config)

    assert plan.status == "ready"
    assert plan.tasks[0]["concepts"][0]["variants"] == concept["variants"]


def test_unknown_vocabulary_variant_is_rejected(catalog, knowledge, config):
    concept = {"surface": "ЛЭП", "variants": ["выдуманный вариант"], "evidence": evidence("ЛЭП")}
    adapter = QueueAdapter([
        classifier("simple"),
        parser([task(query="Найди пункты о ЛЭП", ev=evidence("ЛЭП"), concepts=[concept])]),
        parser([task(query="Найди пункты о ЛЭП", ev=evidence("ЛЭП"), concepts=[concept])]),
    ])

    plan = run_plan("Найди пункты о ЛЭП", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert "unverified vocabulary variant" in plan.issues[0]["message"]


def test_unsupported_and_dependent_tasks_are_preserved_as_deferred(catalog, knowledge, config):
    unsupported = task(task_id="t1", skill=None, query="сравни требования", intent_text="сравни требования", ev=evidence("сравни требования"))
    dependent = task(task_id="t2", skill=None, query="подготовь вывод", intent_text="подготовь вывод", ev=evidence("подготовь вывод"), depends_on=["t1"])
    adapter = QueueAdapter([
        classifier("complex", ["dependency", "unsupported_intent"]),
        parser([unsupported, dependent], issues=[{"code": "unsupported_intent", "task_ids": ["t1", "t2"], "message": "comparison is unsupported"}]),
    ])

    plan = run_plan("сравни требования и подготовь вывод", adapter, catalog, knowledge, config)

    assert plan.status == "deferred"
    assert plan.tasks == [unsupported, dependent]
    assert {"unsupported_intent", "dependency_execution_not_in_stage1"} <= {issue["code"] for issue in plan.issues}


def test_ambiguity_requires_clarification(catalog, knowledge, config):
    issues = [{"code": "ambiguous_document", "task_ids": [], "message": "document is ambiguous"}]
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([], issues=issues), parser([], issues=issues)])

    plan = run_plan("Покажи пункт 5.1", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert "requires a concrete clarification" in plan.issues[0]["message"]


def test_classifier_timeout_is_a_technical_error(catalog, knowledge, config):
    adapter = QueueAdapter([], fail_roles={"classifier"})

    plan = run_plan("Найди требования к заземлению", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert plan.issues[0]["code"] == "classifier_error"
    assert "timed out" in plan.issues[0]["message"]
    assert plan.trace["physical_call_count"] == 1
    assert plan.trace["logical_call_count"] == 1
    assert plan.trace["calls"][0]["error"].startswith("TimeoutError:")


def test_classifier_rejects_simple_with_complex_reason(catalog, knowledge, config):
    adapter = QueueAdapter([classifier("simple", ["ambiguity"])])

    plan = run_plan("Покажи пункт 5.1", adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert plan.issues[0]["code"] == "classifier_error"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "broken",
    [
        lambda: task(skill="exact_point", scope="document", doc_id=DOC_PUE, point_ref="1.1", coverage="ranked"),
        lambda: task(skill="corpus_point_search", point_ref="110", coverage="ranked"),
        lambda: task(skill="find_all", coverage="ranked"),
        lambda: task(skill="corpus_point_search", scope="unresolved"),
    ],
)
def test_skill_scope_coverage_contract_is_enforced(broken, catalog, knowledge, config):
    query = "Найди требования к заземлению"
    invalid = broken()
    invalid["evidence"] = evidence(query)
    adapter = QueueAdapter([classifier(), parser([invalid]), parser([invalid])])

    plan = run_plan(query, adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert plan.trace["repair_count"] == 1


def test_unsupported_task_gets_explicit_issue_and_raw_replies_are_traced(catalog, knowledge, config):
    query = "Дай определение заземления"
    adapter = QueueAdapter([classifier("complex", ["unsupported_intent"]), parser([task(skill=None, query=query, ev=evidence(query))])])

    plan = run_plan(query, adapter, catalog, knowledge, config)

    assert plan.status == "deferred"
    assert [issue["code"] for issue in plan.issues] == ["unsupported_intent"]
    assert [call["raw_reply"] for call in plan.trace["calls"]] == [adapter.calls[0]["messages"] and classifier("complex", ["unsupported_intent"]), adapter.calls[1]["messages"] and parser([task(skill=None, query=query, ev=evidence(query))])]


def test_invalid_confirmed_condition_fails_before_llm(catalog, knowledge, config):
    context = {"turns": [{"turn_id": "turn_1", "user_text": "электростанции", "confirmed_conditions": [{"id": "c1", "text": "электростанции", "quote": "подстанции", "confirmed": True}]}]}
    adapter = QueueAdapter([])

    plan = run_plan("Не электростанции, а подстанции", adapter, catalog, knowledge, config, dialog_context=context)

    assert plan.status == "error"
    assert plan.issues[0]["code"] == "preparation_error"
    assert adapter.calls == []


def test_clarification_obeys_priority_and_uses_supplied_term_options(catalog, config):
    ambiguous_knowledge = DocumentKnowledgeIndex(abbreviations=[{"abbreviation": "АВР", "expansion": "аварийно-восстановительные работы"}, {"abbreviation": "АВР", "expansion": "автоматический ввод резерва"}])
    clarification = {"question": "Что означает АВР?", "target_task_ids": [], "field": "term", "options": [{"id": "автоматический ввод резерва", "label": "автоматический ввод резерва"}]}
    issues = [{"code": "ambiguous_term", "task_ids": [], "message": "АВР has multiple meanings"}]
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([], clarification=clarification, issues=issues)])

    plan = run_plan("Найди требования к АВР", adapter, catalog, ambiguous_knowledge, config)

    assert plan.status == "needs_clarification"
    wrong_priority = {**clarification, "field": "term"}
    priority_issues = [{"code": "ambiguous_document", "task_ids": [], "message": "document ambiguous"}, *issues]
    broken = QueueAdapter([classifier("complex", ["ambiguity"]), parser([], clarification=wrong_priority, issues=priority_issues), parser([], clarification=wrong_priority, issues=priority_issues)])
    rejected = run_plan("Найди требования к АВР", broken, catalog, ambiguous_knowledge, config)
    assert rejected.status == "error"
    assert "highest-priority" in rejected.issues[0]["message"]


def test_equally_ranked_documents_cannot_be_silently_selected(knowledge, config):
    ambiguous_catalog = DocumentCatalog(entries=[
        DocumentCatalogEntry(catalog_id="pte1", doc_id=DOC_PUE, doc_name="ПТЭ первый", aliases=("птэ",)),
        DocumentCatalogEntry(catalog_id="pte2", doc_id=DOC_POT, doc_name="ПТЭ второй", aliases=("птэ",)),
    ])
    selected = task(skill="corpus_point_search", query="требования ПТЭ", ev=evidence("ПТЭ"), scope="document", doc_id=DOC_PUE, document_mention="ПТЭ")
    adapter = QueueAdapter([classifier(), parser([selected]), parser([selected])])

    plan = run_plan("Найди требования ПТЭ", adapter, ambiguous_catalog, knowledge, config)

    assert plan.status == "error"
    assert "equally ranked document candidates" in plan.issues[0]["message"]


def test_validation_catalog_does_not_leak_into_runtime_candidates(knowledge, config):
    runtime_catalog = DocumentCatalog(entries=[])
    validation_catalog = DocumentCatalog(entries=[
        DocumentCatalogEntry(catalog_id="pue", doc_id=DOC_PUE, doc_name="ПУЭ", aliases=("пуэ",)),
    ])
    context = {"turns": [{"turn_id": "turn_1", "user_text": "Покажи пункт 1.1 ПУЭ", "resolved_doc_id": DOC_PUE}]}
    selected_from_validation_only = task(
        skill="exact_point",
        query="пункт 2.1",
        ev=[*evidence("пункт 2.1"), *evidence("ПУЭ", "turn_1")],
        scope="document",
        doc_id=DOC_PUE,
        document_mention="ПУЭ",
        point_ref="2.1",
        coverage="exact",
    )
    adapter = QueueAdapter([
        classifier("simple"),
        parser([selected_from_validation_only], action="continue"),
        parser([selected_from_validation_only], action="continue"),
    ])

    plan = plan_skill_query(
        "пункт 2.1",
        dialog_context=context,
        catalog=runtime_catalog,
        validation_catalog=validation_catalog,
        knowledge=knowledge,
        llm_adapter=adapter,
        config=config,
    )

    classifier_payload = json.loads(adapter.calls[0]["messages"][1]["content"])
    assert classifier_payload["document_candidates"] == []
    assert plan.status == "error"
    assert "document scope requires catalog doc_id" in plan.issues[0]["message"]


def test_voltage_is_not_accepted_as_an_exact_point(catalog, knowledge, config):
    query = "Покажи требования ПУЭ для 110 кВ"
    broken = task(skill="exact_point", query=query, ev=evidence("110 кВ"), scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="110", coverage="exact")
    adapter = QueueAdapter([classifier(), parser([broken]), parser([broken])])

    plan = run_plan(query, adapter, catalog, knowledge, config)

    assert plan.status == "error"
    assert "may be a quantity" in plan.issues[0]["message"]


def test_eval_comparison_checks_scope_and_forbidden_substitutions():
    expected = {"complexity": "simple", "status": "ready", "tasks": [{"skill": "find_all", "scope": "corpus", "doc_id": None, "point_ref": None, "coverage": "comprehensive", "constraints": [], "evidence": ["ЛЭП"], "depends_on": []}], "forbidden_substitutions": ["ВЛ"]}
    actual_task = task(skill="find_all", query="ЛЭП", ev=evidence("ЛЭП"), scope="unresolved", coverage="comprehensive", concepts=[{"surface": "ЛЭП", "variants": ["ВЛ"], "evidence": evidence("ЛЭП")}])
    actual = {"complexity": "simple", "status": "ready", "dialog_action": "new", "tasks": [actual_task], "clarification": None}

    comparison = compare_case(expected, actual)

    assert comparison["tasks_match"] is False
    assert comparison["forbidden_substitutions_match"] is False
    assert comparison["plan_match"] is False


@pytest.mark.parametrize(
    "actual_task, expected_comparison",
    [
        (task(skill="find_all", query="Найди пункты о воздушных линиях", intent_text="Найди пункты", ev=evidence("ЛЭП"), scope="corpus", coverage="comprehensive"), "query_semantics_match"),
        (task(skill="find_all", query="Найди требования к заземлению ЛЭП", intent_text="Сравни требования", ev=evidence("ЛЭП"), scope="corpus", coverage="comprehensive"), "intent_semantics_match"),
        (task(skill="find_all", query="Найди требования к воздушной линии электропередачи", intent_text="Найди требования", ev=evidence("ЛЭП"), scope="corpus", coverage="comprehensive", concepts=[]), "concept_semantics_match"),
    ],
)
def test_eval_semantics_reject_missing_topic_wrong_action_and_missing_concept(actual_task, expected_comparison):
    expected_task = {
        "skill": "find_all", "scope": "corpus", "doc_id": None,
        "point_ref": None, "coverage": "comprehensive", "constraints": [],
        "evidence": ["ЛЭП"], "depends_on": [],
        "required_semantic_elements": [
            {"kind": "topic", "label": "работа под наведённым напряжением", "variants": ["работа под наведённым напряжением", "работы под наведённым напряжением"]},
            {"kind": "intent", "label": "найти все упоминания", "variants": ["найди все", "полный перечень"]},
            {"kind": "concept", "label": "ЛЭП", "variants": ["ЛЭП", "линия электропередачи"], "require_concept": True},
        ],
    }
    expected = {"complexity": "simple", "status": "ready", "tasks": [expected_task]}
    actual = {"complexity": "simple", "status": "ready", "dialog_action": "new", "tasks": [actual_task], "clarification": None}

    comparison = compare_case(expected, actual)

    assert comparison[expected_comparison] is False
    assert comparison["plan_match"] is False


def test_eval_clarification_must_name_the_ambiguous_subject():
    expected = {"complexity": "complex", "status": "needs_clarification", "tasks": [], "clarification_field": "document", "clarification_subject": {"label": "постановление 86", "variants": ["постановление 86", "номер 86"]}}
    actual = {"complexity": "complex", "status": "needs_clarification", "tasks": [], "clarification": {"field": "document", "question": "Уточните вопрос", "options": []}}

    comparison = compare_case(expected, actual)

    assert comparison["clarification_subject_match"] is False
    assert comparison["plan_match"] is False


def test_forbidden_substitution_ignores_trace_but_checks_semantic_fields():
    expected = {"complexity": "complex", "status": "needs_clarification", "tasks": [], "clarification_field": "document", "forbidden_substitutions": ["86 → 861"]}
    harmless_trace = {"complexity": "complex", "status": "needs_clarification", "tasks": [], "clarification": {"field": "document", "question": "Какой номер постановления вы имели в виду?", "options": []}, "trace": {"raw_reply": "Не заменять 86 на популярное постановление 861"}}
    harmful_plan = {**harmless_trace, "clarification": {"field": "document", "question": "Это постановление 861?", "options": []}}

    assert compare_case(expected, harmless_trace)["forbidden_substitutions_match"] is True
    assert compare_case(expected, harmful_plan)["forbidden_substitutions_match"] is False


def test_forbidden_substitution_checks_constraints_as_semantic_fields():
    expected = {
        "complexity": "simple",
        "status": "ready",
        "tasks": [
            {
                "skill": "find_all", "scope": "corpus", "doc_id": None,
                "point_ref": None, "coverage": "comprehensive",
                "constraints": [{"kind": "object", "text": "ЛЭП", "polarity": "include"}],
                "evidence": ["ЛЭП"], "depends_on": [],
            }
        ],
        "forbidden_substitutions": ["ЛЭП → ВЛ"],
    }
    harmful = task(
        skill="find_all",
        query="Найди все пункты про ЛЭП",
        intent_text="Найди все пункты",
        ev=evidence("ЛЭП"),
        scope="corpus",
        coverage="comprehensive",
        constraints=[{"id": "c1", "kind": "object", "text": "ВЛ", "polarity": "include", "evidence": evidence("ЛЭП")}],
    )

    comparison = compare_case(expected, {"complexity": "simple", "status": "ready", "tasks": [harmful], "clarification": None})

    assert comparison["forbidden_substitutions_match"] is False
    assert comparison["plan_match"] is False


def _pending_point_case() -> dict:
    return {
        "question": "Какой именно пункт вы имеете в виду?",
        "field": "point",
        "options": [{"id": "1.1", "label": "пункт 1.1"}],
        "original_query": "Покажи пункт о заземлении в ПУЭ",
        "original_task": {
            "intent_text": "покажи текст пункта",
            "skill": "exact_point",
            "coverage": "exact",
            "scope": "document",
            "doc_id": DOC_PUE,
            "semantic_elements": [{"label": "заземление", "variants": ["заземление", "заземления", "заземлением", "заземлении"]}],
            "constraints": [{"kind": "condition", "text": "заземление", "polarity": "include"}],
        },
    }


def test_pending_clarification_answer_preserves_original_topic_and_is_immutable(catalog, knowledge, config):
    pending = _pending_point_case()
    before = copy.deepcopy(pending)
    quoted = "Покажи пункт о заземлении в ПУЭ"
    resolved = task(
        skill="exact_point", query="пункт 1.1 ПУЭ о заземлении",
        ev=[*evidence("1.1"), *evidence("ПУЭ", "pending_original"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="1.1", coverage="exact",
        constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
    )
    adapter = QueueAdapter([classifier("complex", ["context_correction"]), parser([resolved], action="clarify")])

    plan = run_plan("1.1", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "ready"
    assert pending == before
    parser_payload = json.loads(adapter.calls[0]["messages"][1]["content"])
    assert parser_payload["pending_original_query"] == quoted
    assert any(turn["turn_id"] == "pending_original" and turn["user_text"] == quoted for turn in parser_payload["context"]["turns"])


def test_unmatched_pending_answer_keeps_original_clarification_open(catalog, knowledge, config):
    pending = _pending_point_case()
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([], clarification=None)])

    plan = run_plan("не знаю", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "needs_clarification"
    assert plan.tasks == []
    assert plan.clarification["question"] == pending["question"]
    assert plan.clarification["field"] == "point"
    assert plan.clarification["options"] == pending["options"]


def test_negated_pending_option_does_not_resolve(catalog, knowledge, config):
    pending = _pending_point_case()
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([], clarification=None)])

    plan = run_plan("не пункт 1.1", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "needs_clarification"
    assert plan.tasks == []
    assert plan.clarification["options"] == pending["options"]


def test_open_point_pending_resolves_from_grounded_free_value(catalog, knowledge, config):
    pending = _pending_point_case()
    pending["options"] = []
    resolved = task(
        skill="exact_point", query="текст пункта 1.1 о заземлении", ev=[*evidence("1.1"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="1.1", coverage="exact",
        constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
    )
    adapter = QueueAdapter([classifier("complex", ["context_correction"]), parser([resolved], action="continue")])

    plan = run_plan("1.1", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "ready"
    assert plan.tasks[0]["point_ref"] == "1.1"


def test_open_point_pending_resolves_unique_integer_point_ref(catalog, knowledge, config):
    pending = _pending_point_case()
    pending["options"] = []
    resolved = task(
        skill="exact_point", query="текст пункта 33 о заземлении", ev=[*evidence("33"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="33", coverage="exact",
        constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
    )
    adapter = QueueAdapter([classifier("complex", ["context_correction"]), parser([resolved], action="continue")])

    plan = run_plan("33", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "ready"
    assert plan.tasks[0]["point_ref"] == "33"


def test_open_point_pending_with_two_unique_refs_stays_open(catalog, knowledge, config):
    pending = _pending_point_case()
    pending["options"] = []
    guessed = task(
        skill="exact_point", query="пункт 1.1 о заземлении", ev=[*evidence("1.1"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="1.1", coverage="exact",
        constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
    )
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([guessed], action="continue")])

    plan = run_plan("1.1 и 2.2", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "needs_clarification"
    assert plan.tasks == []
    assert plan.clarification["options"] == []


@pytest.mark.parametrize("answer", ["1.1+33", "1.1/33"])
def test_open_point_pending_mixed_dotted_and_integer_refs_stays_open(answer, catalog, knowledge, config):
    pending = _pending_point_case()
    pending["options"] = []
    guessed = task(
        skill="exact_point", query="пункт 1.1 о заземлении", ev=[*evidence("1.1"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="1.1", coverage="exact",
        constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
    )
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([guessed], action="continue")])

    plan = run_plan(answer, adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "needs_clarification"
    assert plan.tasks == []
    assert plan.clarification["options"] == []


@pytest.mark.parametrize(
    ("answer", "expected_refs"),
    [
        ("1.1", {"1.1"}),
        ("33", {"33"}),
        ("1.1+33", {"1.1", "33"}),
        ("1.1/33", {"1.1", "33"}),
        ("1.1, 33", {"1.1", "33"}),
        ("1.1; 33", {"1.1", "33"}),
        ("1.1 33", {"1.1", "33"}),
        ("110 кВ", set()),
        ("220 В", set()),
        ("50 МВт", set()),
        ("10 А", set()),
        ("60 Гц", set()),
        ("15 мин", set()),
        ("10 с", set()),
        ("3 ч", set()),
        ("33 список", {"33"}),
        ("1.1 и 110 кВ", {"1.1"}),
        ("1.5 кВ", set()),
        ("1.5кВ", set()),
        ("0.5 МВт", set()),
        ("0.5МВт", set()),
        ("50.0 Гц", set()),
        ("50.0Гц", set()),
        ("1.5", {"1.5"}),
    ],
)
def test_open_point_numeric_extractor_handles_mixed_separators_and_units(answer, expected_refs):
    assert _extract_open_point_refs(answer) == expected_refs


def test_open_document_pending_resolves_only_to_frozen_catalog_document(catalog, knowledge, config):
    pending = {
        "question": "Какой документ?", "field": "document", "options": [],
        "original_query": "Покажи пункт 2.1 о заземлении",
        "original_task": {
            "intent_text": "покажи пункт", "skill": "exact_point", "coverage": "exact", "scope": "document",
            "point_ref": "2.1", "semantic_elements": [{"label": "заземление", "variants": ["заземление", "заземлении"]}],
        },
    }
    resolved = task(
        skill="exact_point", query="пункт 2.1 о заземлении", ev=[*evidence("ПУЭ"), *evidence("2.1", "pending_original"), *evidence("заземлении", "pending_original")],
        scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="2.1", coverage="exact",
    )
    adapter = QueueAdapter([classifier("complex", ["context_correction"]), parser([resolved], action="continue")])

    plan = run_plan("ПУЭ", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "ready", plan.issues
    assert plan.tasks[0]["doc_id"] == DOC_PUE


def test_open_document_ambiguous_alias_does_not_resolve_from_one_guessed_task():
    pending = {"field": "document", "options": []}
    rows = [
        {"doc_id": DOC_PUE, "doc_name": "Правила технической эксплуатации", "aliases": ["ПТЭ"]},
        {"doc_id": DOC_POT, "doc_name": "Правила по охране труда", "aliases": ["ПТЭ"]},
    ]
    guessed = task(skill="corpus_point_search", query="требования ПТЭ", ev=evidence("ПТЭ"), scope="document", doc_id=DOC_PUE)

    assert not _open_pending_value_is_grounded("ПТЭ", pending, [guessed], rows, {DOC_PUE, DOC_POT}, None)


@pytest.mark.parametrize("answer", ["не знаю", "не 1.1"])
def test_open_pending_does_not_resolve_uncertainty_or_negated_value(answer, catalog, knowledge, config):
    pending = _pending_point_case()
    pending["options"] = []
    if answer == "не 1.1":
        guessed = task(
            skill="exact_point", query="пункт 1.1 о заземлении", ev=[*evidence("1.1"), *evidence("заземлении", "pending_original")],
            scope="document", doc_id=DOC_PUE, document_mention="ПУЭ", point_ref="1.1", coverage="exact",
            constraints=[{"id": "p1", "kind": "condition", "text": "заземление", "polarity": "include", "evidence": evidence("заземлении", "pending_original")}],
        )
    else:
        guessed = None
    adapter = QueueAdapter([classifier("complex", ["ambiguity"]), parser([guessed] if guessed else [], action="clarify")])

    plan = run_plan(answer, adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "needs_clarification"
    assert plan.tasks == []
    assert plan.clarification["options"] == []


def test_compare_case_does_not_reuse_one_actual_task_for_duplicate_keys():
    expected_tasks = [
        {**task(task_id="t1", query="альфа", intent_text="альфа", ev=evidence("альфа")), "required_semantic_elements": [{"label": "альфа", "variants": ["альфа"]}]},
        {**task(task_id="t2", query="бета", intent_text="бета", ev=evidence("бета")), "required_semantic_elements": [{"label": "бета", "variants": ["бета"]}]},
    ]
    actual_tasks = [
        task(task_id="a1", query="альфа бета", intent_text="альфа бета", ev=[*evidence("альфа"), *evidence("бета")]),
        task(task_id="a2", query="мусор", intent_text="мусор", ev=[]),
    ]
    expected = {"complexity": "complex", "status": "ready", "tasks": expected_tasks}
    actual = {"complexity": "complex", "status": "ready", "tasks": actual_tasks, "clarification": None}

    comparison = compare_case(expected, actual)

    assert comparison["tasks_match"] is True
    assert comparison["task_alignment_match"] is False
    assert comparison["plan_match"] is False


def test_eval_does_not_require_action_words_in_search_query():
    expected_task = {
        **task(query="заземление", intent_text="найти все", ev=evidence("заземление"), skill="find_all", coverage="comprehensive"),
        "evidence": ["заземление"],
        "required_semantic_elements": [
            {"kind": "topic", "label": "заземление", "variants": ["заземление"]},
            {"kind": "intent", "label": "найти все", "variants": ["найти все"]},
        ],
    }
    expected = {"complexity": "simple", "status": "ready", "tasks": [expected_task]}
    actual_task = task(query="заземление", intent_text="найти все", ev=evidence("заземление"), skill="find_all", coverage="comprehensive")
    actual = {"complexity": "simple", "status": "ready", "tasks": [actual_task], "clarification": None}

    comparison = compare_case(expected, actual)

    assert comparison["query_semantics_match"] is True
    assert comparison["intent_semantics_match"] is True
    assert comparison["plan_match"] is True


@pytest.mark.parametrize(
    "pending",
    [
        {"question": "Какой пункт?", "field": "point", "options": []},
        {**_pending_point_case(), "options": ["1.1"]},
        {**_pending_point_case(), "original_task": {"intent_text": "покажи", "skill": "made_up", "coverage": "exact", "semantic_elements": [{"label": "x", "variants": ["x"]}]}},
    ],
)
def test_malformed_pending_clarification_fails_before_llm(pending, catalog, knowledge, config):
    adapter = QueueAdapter([])

    plan = run_plan("1.1", adapter, catalog, knowledge, config, pending_clarification=pending)

    assert plan.status == "error"
    assert plan.issues[0]["code"] == "preparation_error"
    assert adapter.calls == []


def test_frozen_router_plan_fixture_shape():
    payload = json.loads((HERE / "router_plan_cases_v1.json").read_text(encoding="utf-8"))

    validate_cases(payload)
    cases = {row["id"]: row for row in payload["cases"]}
    for case_id in ("S01", "C02", "D01"):
        for expected_task in cases[case_id]["expected"].get("tasks", []):
            assert all(len(element["variants"]) <= 5 for element in expected_task["required_semantic_elements"])
            assert all(not element.get("require_concept") for element in expected_task["required_semantic_elements"] if expected_task.get("doc_id"))
    s08_terms = {element["label"] for element in cases["S08"]["expected"]["tasks"][0]["required_semantic_elements"]}
    assert {"договор", "передача электрической энергии"} <= s08_terms


def test_frozen_fixture_hash_and_holdout_independence_guards():
    case_path = HERE / "router_plan_cases_v1.json"
    stored_hash = (HERE / "router_plan_cases_v1.sha256").read_text(encoding="ascii").strip()
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    assert __import__("hashlib").sha256(case_path.read_bytes()).hexdigest() == stored_hash

    leaked = copy.deepcopy(payload)
    holdout_case = next(row for row in leaked["cases"] if row["split"] == "holdout")
    holdout_case["metamorphic_pair"] = "leaked_holdout_pair"
    with pytest.raises(ValueError, match="metamorphic pairs"):
        validate_cases(leaked)

    missing_source = copy.deepcopy(payload)
    existing = next(row for row in missing_source["cases"] if row["provenance"] == "existing_fixture")
    existing["question"] = existing["question"] + " изменено"
    with pytest.raises(ValueError, match="existing fixture provenance"):
        validate_cases(missing_source)

    missing_semantics = copy.deepcopy(payload)
    first_task = missing_semantics["cases"][0]["expected"]["tasks"][0]
    first_task["required_semantic_elements"] = []
    with pytest.raises(ValueError, match="every expected task"):
        validate_cases(missing_semantics)

    sentence_variant = copy.deepcopy(payload)
    sentence_task = sentence_variant["cases"][0]["expected"]["tasks"][0]
    sentence_task["required_semantic_elements"][0]["variants"] = ["Как звучит пункт из правил по охране труда", "пункт 6.25"]
    with pytest.raises(ValueError, match="atomic labels"):
        validate_cases(sentence_variant)

    incompatible_pair = copy.deepcopy(payload)
    pair_member = next(row for row in incompatible_pair["cases"] if row["id"] == "V06")
    pair_member["expected"]["tasks"][0]["required_semantic_elements"][1]["variants"] = ["трансформатор"]
    with pytest.raises(ValueError, match="incompatible concepts"):
        validate_cases(incompatible_pair)

    missing_case_candidate = copy.deepcopy(payload)
    s01 = next(row for row in missing_case_candidate["cases"] if row["id"] == "S01")
    s01["candidates"]["documents"] = []
    with pytest.raises(ValueError, match="absent from frozen per-case candidates"):
        validate_cases(missing_case_candidate)

    context_only_document = copy.deepcopy(payload)
    d05 = next(row for row in context_only_document["cases"] if row["id"] == "D05")
    d05["candidates"]["documents"] = []
    validate_cases(context_only_document)


def test_pending_semantics_must_be_grounded_and_selected_option_must_match_field():
    pending = _pending_point_case()
    bad_grounding = copy.deepcopy(pending)
    bad_grounding["original_task"]["semantic_elements"] = [{"label": "трансформатор", "variants": ["трансформатор"]}]
    with pytest.raises(ValueError, match="not grounded"):
        _validate_pending_clarification(bad_grounding)

    bad_constraint = copy.deepcopy(pending)
    bad_constraint["original_task"]["constraints"][0]["text"] = "заземляющий трансформатор"
    with pytest.raises(ValueError, match="constraint is not grounded"):
        _validate_pending_clarification(bad_constraint)

    resolved_wrong_point = task(
        skill="exact_point", query="пункт 2.2 ПУЭ о заземлении", ev=evidence("2.2"),
        scope="document", doc_id=DOC_PUE, point_ref="2.2", coverage="exact",
    )
    with pytest.raises(ValueError, match="selected point option"):
        _validate_pending_result(pending, "1.1", [resolved_wrong_point], None)

    document_pending = {
        "question": "Какой документ?", "field": "document",
        "options": [{"id": DOC_PUE, "label": "ПУЭ"}],
        "original_query": "Найди требования постановления",
        "original_task": {
            "intent_text": "Найди требования", "skill": "corpus_point_search", "coverage": "ranked",
            "semantic_elements": [{"label": "требования постановления", "variants": ["требования постановления"]}],
        },
    }
    wrong_document = task(
        skill="corpus_point_search", query="требования постановления", scope="document",
        doc_id=DOC_POT, document_mention="ПУЭ", coverage="ranked",
    )
    with pytest.raises(ValueError, match="selected document option"):
        _validate_pending_result(document_pending, DOC_PUE, [wrong_document], None)

    malformed_document_option = copy.deepcopy(document_pending)
    malformed_document_option["options"][0]["id"] = "ПУЭ"
    with pytest.raises(ValueError, match="canonical doc_id"):
        _validate_pending_clarification(malformed_document_option)


def test_metamorphic_actual_outputs_are_checked_as_a_pair():
    payload = json.loads((HERE / "router_plan_cases_v1.json").read_text(encoding="utf-8"))
    left = next(row for row in payload["cases"] if row["id"] == "V03")["expected"]
    right = next(row for row in payload["cases"] if row["id"] == "V06")["expected"]
    plan_a = {"tasks": [{"skill": "find_all", "scope": "corpus", "concepts": [{"surface": "воздушные линии", "variants": ["ВЛ"]}]}]}
    plan_b = {"tasks": [{"skill": "find_all", "scope": "corpus", "concepts": [{"surface": "ВЛ", "variants": ["воздушные линии"]}]}]}
    assert compare_metamorphic_pair(left, plan_a, right, plan_b)["passed"] is True
    plan_b["tasks"][0]["skill"] = "corpus_point_search"
    assert compare_metamorphic_pair(left, plan_a, right, plan_b)["passed"] is False
