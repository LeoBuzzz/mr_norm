from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from router_draft import (DraftValidationError, SEMANTIC_DRAFT_SCHEMA, assemble_skill_query_plan,
                          build_source_manifest, extract_source_ref, validate_semantic_draft)
from router_subscription_eval import (ReplayAdapter, ReplayInputError, RECEIPT_FIELDS,
                                      append_receipt, compare_router_v2_case, compare_router_v2_metamorphic,
                                      load_receipts, sha256_bytes)
from router_contracts import RouterPlanConfig
from router_planning import plan_skill_query_v2
from mr_norm.retrieval.document_knowledge import DocumentKnowledgeIndex


def test_source_ref_preserves_exact_offsets_and_decimal_token():
    manifest = build_source_manifest("Найди 6.25 и заземление")
    assert [x["text"] for x in manifest["tokens"]["current"]] == ["Найди", "6.25", "и", "заземление"]
    assert extract_source_ref("current@2:4", manifest) == {"turn_id": "current", "quote": "6.25 и заземление"}


@pytest.mark.parametrize("ref", ["unknown@1:1", "current@0:1", "current@2:1", "current@1:8", "current:1:2"])
def test_source_ref_rejects_bad_ranges(ref):
    with pytest.raises(DraftValidationError):
        extract_source_ref(ref, build_source_manifest("тест 6.25"))


def test_reserved_pending_source_and_input_immutability():
    context = {"turns": [{"turn_id": "prior", "user_text": "Сохрани ограничение 110 кВ"}]}
    original = json.loads(json.dumps(context))
    manifest = build_source_manifest("пункт 1.1", context, {"original_query": "исходный вопрос"})
    assert manifest["sources"]["pending_original"] == "исходный вопрос"
    assert context == original
    with pytest.raises(DraftValidationError):
        build_source_manifest("x", {"turns": [{"turn_id": "pending_original", "user_text": "x"}]}, {"original_query": "y"})


def test_schema_file_matches_authoritative_contract():
    schema = json.loads((HERE / "router_draft_schema.json").read_text(encoding="utf-8"))
    assert schema == SEMANTIC_DRAFT_SCHEMA


def test_draft_rejects_null_unknown_fields_and_forward_dependencies():
    manifest = build_source_manifest("Найди требования к заземлению")
    base = {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:4"], "topic": "требования к заземлению"}]}
    assert validate_semantic_draft(base, manifest)["tasks"][0]["operation"] == "search"
    for draft in (
        {**base, "extra": True},
        {"dialog": "new", "tasks": [{**base["tasks"][0], "topic": None}]},
        {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:4"], "topic": "тема", "after": [1]}]},
        {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@9:10"], "topic": "тема"}]},
    ):
        with pytest.raises(DraftValidationError):
            validate_semantic_draft(draft, manifest)


def test_assembler_generates_stable_ids_grounded_evidence_and_status():
    manifest = build_source_manifest("Найди требования к заземлению")
    draft = validate_semantic_draft({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:4"], "topic": "требования к заземлению"}]}, manifest)
    result = assemble_skill_query_plan("Найди требования к заземлению", "simple", draft, manifest, catalog_rows=[])
    assert result.schema_version == "skill_query_plan.v2"
    assert result.status == "ready"
    assert result.tasks[0]["task_id"] == "t1"
    assert result.tasks[0]["skill"] == "corpus_point_search"
    assert result.tasks[0]["evidence"] == [{"turn_id": "current", "quote": "Найди требования к заземлению"}]


def test_v2_entrypoint_uses_two_logical_adapter_calls_only():
    calls = []
    def reply(content, model):
        return {"content": content, "metadata": {"transport_name": "injected-test", "authentication_mode": "test",
            "supports_json_schema": False, "capability_evidence": "test adapter has no schema enforcement",
            "requested_model": model, "actual_model": None, "effort": "medium", "usage": None,
            "physical_request_count": None}}
    def adapter(*, role, messages, model, timeout, max_tokens):
        calls.append((role, model))
        if role == "classifier":
            return reply('{"complexity":"simple"}', model)
        return reply('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:4"],"topic":"требования к заземлению"}]}', model)
    plan = plan_skill_query_v2("Найди требования к заземлению", catalog=None,
                               knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
                               config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert plan.schema_version == "skill_query_plan.v2"
    assert plan.status == "ready"
    assert calls == [("classifier", "luna"), ("simple_parser", "luna")]
    assert plan.trace["logical_call_count"] == 2
    assert plan.trace["physical_call_count"] is None


def _receipt(**overrides):
    raw = '{"complexity":"simple"}'
    row = {key: None for key in RECEIPT_FIELDS}
    row.update({"run_id": "r", "case_id": "S01", "role": "classifier", "attempt": 1,
                "input_sha256": "a" * 64, "prompt_sha256": "b" * 64, "schema_sha256": "c" * 64,
                "agent_id": "agent", "requested_model": "gpt-6-luna", "requested_effort": "medium",
                "actual_model": None, "model_verification": "requested_by_host_only", "started_at": "t0",
                "finished_at": "t1", "raw_final_text": raw, "raw_sha256": sha256_bytes(raw.encode()),
                "event_id": "event", "finish": "completed", "error": None})
    row.update(overrides)
    return row


def _adapter_response(content, model, **metadata_overrides):
    metadata = {"transport_name": "injected-test", "authentication_mode": "test", "supports_json_schema": False,
                "capability_evidence": "test adapter has no schema enforcement", "requested_model": model,
                "actual_model": None, "effort": "medium", "usage": None, "physical_request_count": None}
    metadata.update(metadata_overrides)
    return {"content": content, "metadata": metadata}


def test_v2_repairs_one_contract_error_but_not_transport_error():
    calls = []
    responses = [
        _adapter_response('{"complexity":"simple"}', "luna"),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:4"],"topic":"тема","bad":1}]}', "luna"),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:4"],"topic":"тема"}]}', "luna"),
    ]
    def adapter(**kwargs):
        calls.append(kwargs["role"])
        return responses.pop(0)
    plan = plan_skill_query_v2("Найди требования к заземлению", catalog=None, knowledge=DocumentKnowledgeIndex(),
                               llm_adapter=adapter, config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert plan.status == "ready"
    assert calls == ["classifier", "simple_parser", "simple_parser"]
    assert plan.trace["repair_count"] == 1
    assert plan.trace["logical_call_count"] == 3

    def broken(**kwargs):
        if kwargs["role"] == "classifier":
            return _adapter_response('{"complexity":"simple"}', "luna")
        raise TimeoutError("transport failed")
    failed = plan_skill_query_v2("Найди требования к заземлению", catalog=None, knowledge=DocumentKnowledgeIndex(),
                                 llm_adapter=broken, config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert failed.status == "error"
    assert failed.issues[0]["code"] == "parser_transport_error"
    assert failed.trace["repair_count"] == 0


def test_v2_unmatched_pending_keeps_original_clarification_and_snapshot_immutable():
    pending = {"question": "Какой пункт?", "field": "point", "options": [],
               "original_query": "Покажи пункт о заземлении в ПУЭ",
               "original_task": {"intent_text": "покажи пункт", "skill": "exact_point", "coverage": "exact",
                                 "semantic_elements": [{"label": "заземление", "variants": ["заземление", "заземлении"]}]}}
    before = json.loads(json.dumps(pending))
    replies = [_adapter_response('{"complexity":"complex"}', "luna"),
               _adapter_response('{"dialog":"clarify","tasks":[],"uncertain":[{"field":"point","source":"current@1:2"}]}', "sol")]
    def adapter(**kwargs):
        return replies.pop(0)
    plan = plan_skill_query_v2("не знаю", pending_clarification=pending, catalog=None,
                               knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
                               config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert plan.status == "needs_clarification", plan.issues
    assert plan.tasks == []
    assert plan.clarification["question"] == "Какой пункт?"
    assert pending == before


def test_replay_requires_exact_hash_and_has_no_fallback():
    adapter = ReplayAdapter([_receipt()], run_id="r", input_hashes={"S01": "a" * 64}, prompt_hashes={"classifier": "b" * 64}, schema_hash="c" * 64)
    assert adapter.get("S01", "classifier") == '{"complexity":"simple"}'
    with pytest.raises(ReplayInputError, match="missing"):
        adapter.get("S02", "classifier")


def test_replay_rejects_duplicate_and_mismatched_receipt():
    with pytest.raises(ReplayInputError, match="duplicate"):
        ReplayAdapter([_receipt(), _receipt()], run_id="r", input_hashes={"S01": "a" * 64}, prompt_hashes={"classifier": "b" * 64}, schema_hash="c" * 64)
    with pytest.raises(ReplayInputError, match="hash mismatch"):
        ReplayAdapter([_receipt()], run_id="r", input_hashes={"S01": "d" * 64}, prompt_hashes={"classifier": "b" * 64}, schema_hash="c" * 64)


def test_receipt_append_is_validated_and_duplicate_safe():
    path = HERE / f".test-receipts-{uuid.uuid4().hex}.jsonl"
    try:
        append_receipt(path, _receipt())
        assert len(load_receipts(path)) == 1
        with pytest.raises(ReplayInputError, match="duplicate"):
            append_receipt(path, _receipt())
        broken = _receipt(raw_sha256="0" * 64)
        with pytest.raises(ReplayInputError, match="raw receipt hash"):
            append_receipt(path, broken)
    finally:
        path.unlink(missing_ok=True)


def test_v2_semantic_evaluator_detects_lost_topic():
    expected = {"complexity": "simple", "status": "ready", "dialog_action": "new", "tasks": [
        {"operation": "search", "doc_id": None, "point_ref": None, "coverage": "ranked", "depends_on": [],
         "required_semantic_elements": [{"kind": "topic", "label": "заземлению", "variants": ["заземлению"]}], "constraints": []}
    ]}
    actual = {"complexity": "simple", "status": "ready", "dialog_action": "new", "clarification": None, "tasks": [
        {"skill": "corpus_point_search", "doc_id": None, "point_ref": None, "coverage": "ranked", "depends_on": [],
         "query": "требования к заземлению", "intent_text": "search: требования к заземлению", "concepts": [], "constraints": []}
    ]}
    assert compare_router_v2_case(expected, actual)["plan_match"], compare_router_v2_case(expected, actual)
    actual["tasks"][0]["query"] = "условия эксплуатации трансформаторов"
    actual["tasks"][0]["intent_text"] = "search: условия эксплуатации трансформаторов"
    assert not compare_router_v2_case(expected, actual)["plan_match"]


def test_v2_semantic_evaluator_checks_source_coverage_exact_dag_and_clarification_options():
    source = build_source_manifest("Найди заземление затем ПУЭ")
    expected = {"complexity": "complex", "status": "needs_clarification", "dialog_action": "new",
                "clarification_field": "document", "clarification_subject": {"variants": ["ПУЭ"]},
                "clarification_options": [{"id": "doc_pue", "labels": ["ПУЭ"]}], "tasks": [
        {"operation": "search", "doc_id": None, "coverage": "ranked", "depends_on": [], "source_refs": ["current@2:2"],
         "required_semantic_elements": [{"kind": "topic", "variants": ["заземление"]}], "constraints": []},
        {"operation": "search", "doc_id": None, "coverage": "ranked", "depends_on": ["t1"], "source_refs": ["current@4:4"],
         "required_semantic_elements": [{"kind": "topic", "variants": ["ПУЭ"]}], "constraints": []}]}
    tasks = [
        {"task_id": "t1", "skill": "corpus_point_search", "doc_id": None, "coverage": "ranked", "depends_on": [],
         "query": "заземление", "intent_text": "заземление", "concepts": [], "constraints": [], "evidence": [{"turn_id": "current", "quote": "заземление"}]},
        {"task_id": "t2", "skill": "corpus_point_search", "doc_id": None, "coverage": "ranked", "depends_on": ["t1"],
         "query": "ПУЭ", "intent_text": "ПУЭ", "concepts": [], "constraints": [], "evidence": [{"turn_id": "current", "quote": "ПУЭ"}]},
    ]
    actual = {"complexity": "complex", "status": "needs_clarification", "dialog_action": "new",
              "clarification": {"field": "document", "question": "Выберите документ по ПУЭ", "options": [{"id": "doc_pue", "label": "ПУЭ"}]},
              "tasks": tasks, "trace": {"source_manifest": source}}
    assert compare_router_v2_case(expected, actual)["plan_match"]
    actual["tasks"][1]["depends_on"] = []
    assert not compare_router_v2_case(expected, actual)["dependency_edges_match"]
    actual["tasks"][1]["depends_on"] = ["t1"]
    actual["tasks"][0]["evidence"] = [{"turn_id": "current", "quote": "Найди"}]
    assert not compare_router_v2_case(expected, actual)["evidence_coverage"]
    actual["tasks"][0]["evidence"] = [{"turn_id": "current", "quote": "заземление"}]
    actual["clarification"]["options"] = [{"id": "doc_other", "label": "ПУЭ"}]
    assert not compare_router_v2_case(expected, actual)["clarification_options_match"]
    for options in ([], [{"id": "doc_pue", "label": "ПУЭ"}, {"id": "doc_pue", "label": "ПУЭ"}],
                    [{"id": "doc_pue", "label": "чужая подпись"}], [{"id": "doc_pue_extra", "label": "ПУЭ"}]):
        actual["clarification"]["options"] = options
        assert not compare_router_v2_case(expected, actual)["clarification_options_match"]


def test_real_v03_semantics_use_query_concept_and_constraints_separately():
    from router_draft import extract_source_ref
    case = next(row for row in json.loads((HERE / "router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))["cases"] if row["id"] == "V03")
    expected = case["expected"]
    manifest = build_source_manifest(case["question"], case.get("context", {}), case.get("pending_clarification"))
    evidence = [extract_source_ref(ref, manifest) for ref in expected["tasks"][0]["source_refs"]]
    actual_task = {"task_id": "t1", "skill": "find_all", "doc_id": None, "point_ref": None,
                   "coverage": "comprehensive", "depends_on": [], "query": "заземление",
                   "intent_text": "all: заземление", "concepts": [{"surface": "ВЛ", "variants": ["ВЛ"]}],
                   "constraints": [{"kind": row["kind"], "polarity": row["polarity"], "text": row["text"]}
                                   for row in expected["tasks"][0]["constraints"]], "evidence": evidence}
    actual = {"complexity": expected["complexity"], "status": expected["status"], "dialog_action": expected["dialog_action"],
              "clarification": None, "tasks": [actual_task], "trace": {"source_manifest": manifest}}
    assert len(actual_task["constraints"]) == 3
    assert compare_router_v2_case(expected, actual)["plan_match"]

    # The same semantic words in the wrong representation do not satisfy gold.
    actual_task["concepts"] = []
    actual_task["query"] = "заземление ВЛ"
    assert not compare_router_v2_case(expected, actual)["plan_match"]
    actual_task["concepts"] = [{"surface": "ВЛ", "variants": ["ВЛ"]}]
    actual_task["constraints"] = actual_task["constraints"][:2]
    actual_task["query"] = "заземление 110 кВ кабельных линий"
    assert not compare_router_v2_case(expected, actual)["plan_match"]
    actual_task["constraints"] = [{"kind": row["kind"], "polarity": row["polarity"], "text": row["text"]}
                                  for row in expected["tasks"][0]["constraints"]]
    actual_task["query"] = "110 кВ"
    assert not compare_router_v2_case(expected, actual)["plan_match"]


def test_clarification_options_require_exact_one_to_one_id_set_and_labels():
    expected = {"complexity": "simple", "status": "needs_clarification", "dialog_action": "new",
                "clarification_field": "document", "clarification_options": [
                    {"id": "doc_a", "labels": ["ПУЭ", "Правила устройства электроустановок"]},
                    {"id": "doc_b", "labels": ["ПТЭ"]}], "tasks": []}
    actual = {"complexity": "simple", "status": "needs_clarification", "dialog_action": "new",
              "clarification": {"field": "document", "question": "Выберите документ", "options": [
                  {"id": "doc_b", "label": "ПТЭ"}, {"id": "doc_a", "label": "Правила устройства электроустановок"}]}, "tasks": []}
    assert compare_router_v2_case(expected, actual)["clarification_options_match"]
    for options in (
        [{"id": "doc_a", "label": "ПУЭ"}],
        [{"id": "doc_a", "label": "ПУЭ"}, {"id": "doc_a", "label": "ПУЭ"}],
        [{"id": "doc_a", "label": "ПУЭ"}, {"id": "doc_b", "label": "ПТЭ"}, {"id": "doc_c", "label": "ГОСТ"}],
        [{"id": "doc_a", "label": "ПУЭ"}, {"id": "doc_b", "label": "чужой документ"}],
    ):
        actual["clarification"]["options"] = options
        assert not compare_router_v2_case(expected, actual)["clarification_options_match"]

    # Tuning term options without canonical IDs use exact, unique normalized labels.
    expected["clarification_options"] = [{"labels": ["АВР"]}, {"labels": ["Автоматический ввод резерва"]}]
    actual["clarification"]["options"] = [{"id": "term-2", "label": "Автоматический ввод резерва"}, {"id": "term-1", "label": "АВР"}]
    assert compare_router_v2_case(expected, actual)["clarification_options_match"]
    actual["clarification"]["options"] = [{"id": "term-1", "label": "АВР"}, {"id": "term-1", "label": "АВР"}]
    assert not compare_router_v2_case(expected, actual)["clarification_options_match"]


def test_v2_metamorphic_pair_checks_cross_case_concept_compatibility():
    expected = {"tasks": [{"required_semantic_elements": [{"kind": "object", "variants": ["заземление"]}]}]}
    left = {"tasks": [{"skill": "corpus_point_search", "scope": "corpus", "concepts": [{"surface": "заземление", "variants": []}]}]}
    right = {"tasks": [{"skill": "corpus_point_search", "scope": "corpus", "concepts": [{"surface": "заземление", "variants": []}]}]}
    assert compare_router_v2_metamorphic(expected, left, expected, right)["passed"]
    right["tasks"][0]["concepts"] = [{"surface": "трансформатор", "variants": []}]
    assert not compare_router_v2_metamorphic(expected, left, expected, right)["passed"]


def test_point_refs_reject_units_but_keep_negated_conditions_grounded():
    manifest = build_source_manifest("Покажи пункт 6.25 и не ниже 110 кВ")
    valid = validate_semantic_draft({"dialog": "new", "tasks": [{
        "operation": "text", "source": ["current@1:8"], "document": "doc_pue", "point": "current@3:3",
        "conditions": [{"source": "current@5:8", "kind": "condition", "polarity": "exclude"}],
    }]}, manifest, candidate_ids={"doc_pue"})
    plan = assemble_skill_query_plan(
        "Покажи пункт 6.25 и не ниже 110 кВ", "simple", valid, manifest,
        catalog_rows=[{"doc_id": "doc_pue", "doc_name": "ПУЭ"}],
    )
    assert plan.status == "ready"
    assert plan.tasks[0]["point_ref"] == "6.25"
    assert plan.tasks[0]["constraints"] == [{
        "id": "t1:c1", "kind": "condition", "text": "не ниже 110 кВ", "polarity": "exclude",
        "evidence": [{"turn_id": "current", "quote": "не ниже 110 кВ"}],
    }]

    for text, ref in (("110 кВ", "current@1:2"), ("1.5кВ", "current@1:2"), ("0.5МВт", "current@1:2"), ("50.0Гц", "current@1:2")):
        with pytest.raises(DraftValidationError, match="measured quantity"):
            validate_semantic_draft({"dialog": "new", "tasks": [{
                "operation": "text", "source": ["current@1:2"], "document": "doc_pue", "point": ref,
            }]}, build_source_manifest(text), candidate_ids={"doc_pue"})


def test_assembler_marks_unsupported_dependencies_without_losing_skill_coverage_and_evidence():
    manifest = build_source_manifest("Найди заземление затем сравни результаты")
    draft = validate_semantic_draft({"dialog": "new", "tasks": [
        {"operation": "search", "source": ["current@1:2"], "topic": "заземление", "terms": [{"source": "current@2:2"}]},
        {"operation": "compare", "source": ["current@4:5"], "topic": "сравни результаты", "document": "corpus", "after": [1]},
    ]}, manifest)
    plan = assemble_skill_query_plan("Найди заземление затем сравни результаты", "complex", draft, manifest, catalog_rows=[])
    assert plan.status == "deferred"
    assert [(task["task_id"], task["skill"], task["coverage"], task["depends_on"]) for task in plan.tasks] == [
        ("t1", "corpus_point_search", "ranked", []),
        ("t2", None, "ranked", ["t1"]),
    ]
    assert plan.tasks[0]["evidence"] == [{"turn_id": "current", "quote": "Найди заземление"}]
    assert plan.tasks[0]["concepts"][0]["surface"] == "заземление"
    assert {issue["code"] for issue in plan.issues} >= {"unsupported_intent", "dependency_execution_not_in_stage1"}
    assert plan.clarification is None


def test_implicit_document_ambiguity_is_only_for_text_and_unknown_intent_clarifies():
    manifest = build_source_manifest("Объясни пункт 1.1")
    unsupported = validate_semantic_draft({"dialog": "new", "tasks": [{"operation": "define", "source": ["current@1:3"], "topic": "пункт 1.1"}]}, manifest)
    deferred = assemble_skill_query_plan("Объясни пункт 1.1", "simple", unsupported, manifest, catalog_rows=[])
    assert deferred.status == "deferred"
    assert deferred.clarification is None
    unknown = validate_semantic_draft({"dialog": "new", "tasks": [{"operation": "unknown", "source": ["current@1:3"]}]}, manifest)
    clarified = assemble_skill_query_plan("Объясни пункт 1.1", "complex", unknown, manifest, catalog_rows=[])
    assert clarified.status == "needs_clarification"
    assert clarified.clarification["field"] == "intent"
    assert not any(issue["code"] == "ambiguous_document" for issue in deferred.issues)


def test_schema_description_is_normative_for_all_optional_shapes():
    from router_draft import semantic_draft_schema_description
    description = semantic_draft_schema_description()
    for token in ('"topic":{"type":"string"}', '"document":{"type":"string"}', '"point":{"type":"string"}',
                  '"meaning":{"type":"string"}', '"conditions":{"type":"array","items"', '"after":{"type":"array","items"',
                  '"replace":{"type":"array","items"', '"candidates":{"type":"array","items"', '"additionalProperties":false'):
        assert token in description


def test_term_meaning_groups_have_stable_canonical_ids_and_readable_labels():
    from router_v2_runtime import _prepare_vocab
    vocab, ids = _prepare_vocab({"abbreviation_groups": [{"abbreviation": "ПТЭ", "expansions": ["Правила технической эксплуатации"]}]})
    group = vocab["meaning_groups"][0]
    assert group["meaning_id"] in ids
    assert group["meaning_id"].startswith("abbr:птэ:")
    assert group["label"] == "ПТЭ — Правила технической эксплуатации"
    assert group["variants"] == ["ПТЭ", "Правила технической эксплуатации"]


def test_v2_catalog_ids_are_validated_before_any_llm_call():
    from mr_norm.retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
    calls = []
    def adapter(**kwargs):
        calls.append(kwargs)
        return _adapter_response('{"complexity":"simple"}', "luna")
    pending = {"question": "Какой документ?", "field": "document", "options": [], "original_query": "Найди пункт 1.1 ПУЭ",
               "original_task": {"intent_text": "Найди пункт", "skill": "exact_point", "coverage": "exact", "doc_id": "missing_doc",
                                 "semantic_elements": [{"label": "пункт", "variants": ["пункт"]}]}}
    catalog = DocumentCatalog(entries=[DocumentCatalogEntry(catalog_id="valid", doc_id="doc_valid", doc_name="Документ")])
    plan = plan_skill_query_v2("doc_valid", pending_clarification=pending, catalog=catalog, knowledge=DocumentKnowledgeIndex(),
                               llm_adapter=adapter, config=RouterPlanConfig("luna", "sol", "injected"))
    assert plan.status == "error" and plan.issues[0]["code"] == "preparation_error"
    assert not calls
    plan = plan_skill_query_v2("Найди пункт 1.1", dialog_context={"turns": [{"turn_id": "old", "user_text": "Документ", "resolved_doc_id": "missing_doc"}]},
                               catalog=catalog, knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
                               config=RouterPlanConfig("luna", "sol", "injected"))
    assert plan.status == "error" and plan.issues[0]["code"] == "invalid_context_doc_id"
    assert not calls


def test_v2_repair_input_retains_classifier_and_primary_query():
    seen = []
    responses = [_adapter_response('{"complexity":"simple"}', "luna"), _adapter_response('{"bad":true}', "luna"),
                 _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:2"],"topic":"заземление"}]}', "luna")]
    def adapter(**kwargs):
        seen.append(json.loads(kwargs["messages"][1]["content"]))
        return responses.pop(0)
    plan = plan_skill_query_v2("Найди заземление", catalog=None, knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
                               config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert plan.status == "ready"
    repair = seen[2]
    assert repair["original_query"] == "Найди заземление"
    assert repair["classifier"] == {"complexity": "simple"}
    assert repair["invalid_response"] == '{"bad":true}'
    assert repair["validation_errors"]


def test_v2_known_physical_count_survives_classifier_parse_error():
    plan = plan_skill_query_v2("Найди заземление", catalog=None, knowledge=DocumentKnowledgeIndex(),
                               llm_adapter=lambda **kwargs: _adapter_response("not-json", "luna", physical_request_count=2),
                               config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000))
    assert plan.status == "error"
    assert plan.trace["physical_call_count"] == 2


def test_strict_schema_defaults_uncertain_and_rejects_nested_unknowns_and_self_cycles():
    manifest = build_source_manifest("Найди заземление")
    normalized = validate_semantic_draft({"dialog": "new", "tasks": [
        {"operation": "search", "source": ["current@1:2"], "topic": "заземление"},
    ]}, manifest)
    assert normalized["uncertain"] == []

    malformed = [
        {"dialog": "new", "tasks": [], "uncertain": None},
        {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:2"], "topic": "заземление", "terms": [{"source": "current@2:2", "extra": "x"}]}]},
        {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:2"], "topic": "заземление", "conditions": [{"source": "current@2:2", "kind": "object", "polarity": "include", "extra": "x"}]}]},
        {"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:2"], "topic": "заземление", "after": [1]}]},
    ]
    for draft in malformed:
        with pytest.raises(DraftValidationError):
            validate_semantic_draft(draft, manifest)


def test_v2_adapter_metadata_is_required_and_physical_counts_are_summed():
    def incomplete(**kwargs):
        return {"content": '{"complexity":"simple"}', "metadata": {"transport_name": "test"}}

    plan = plan_skill_query_v2(
        "Найди заземление", catalog=None, knowledge=DocumentKnowledgeIndex(), llm_adapter=incomplete,
        config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000),
    )
    assert plan.status == "error"
    assert plan.issues[0]["code"] == "classifier_error"

    replies = [
        _adapter_response('{"complexity":"simple"}', "luna", physical_request_count=1),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:2"],"topic":"заземление"}]}', "luna", physical_request_count=2),
    ]

    def counted(**kwargs):
        return replies.pop(0)

    counted_plan = plan_skill_query_v2(
        "Найди заземление", catalog=None, knowledge=DocumentKnowledgeIndex(), llm_adapter=counted,
        config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000),
    )
    assert counted_plan.status == "ready"
    assert counted_plan.trace["physical_call_count"] == 3
    assert [row["transport_name"] for row in counted_plan.trace["adapter_metadata"]] == ["injected-test", "injected-test"]


def test_v2_validation_catalog_cannot_supply_runtime_frozen_document_candidates():
    from mr_norm.retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry

    runtime_catalog = DocumentCatalog(entries=[])
    validation_catalog = DocumentCatalog(entries=[
        DocumentCatalogEntry(catalog_id="pue", doc_id="doc_pue", doc_name="ПУЭ", aliases=("пуэ",)),
    ])
    replies = [
        _adapter_response('{"complexity":"simple"}', "luna"),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:3"],"topic":"заземление","document":"doc_pue"}]}', "luna"),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:3"],"topic":"заземление","document":"doc_pue"}]}', "luna"),
    ]

    def adapter(**kwargs):
        return replies.pop(0)

    plan = plan_skill_query_v2(
        "Найди заземление ПУЭ", catalog=runtime_catalog, validation_catalog=validation_catalog,
        knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
        config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000),
    )
    assert plan.status == "error"
    assert plan.issues[0]["code"] == "parser_error"
    assert "document ID is not in candidates" in plan.issues[0]["message"]
    assert plan.trace["repair_count"] == 1


def test_frozen_tuning_run_has_exactly_20_ids_and_no_gold_in_model_inputs():
    from run_router_v2_offline_gate import RUN_DIR, validate_run

    summary = validate_run(RUN_DIR)
    assert summary["case_count"] == 20
    assert summary["live_workers_started"] is False
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (RUN_DIR / "inputs" / "input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert manifest["case_count"] == len(rows) == 20
    assert manifest["case_order"] == [row["case_id"] for row in rows]
    assert len(set(manifest["case_order"])) == 20
    forbidden = {"expected", "gold", "split", "answer", "label"}
    assert all(not (forbidden & set(row)) for row in rows)


def test_v2_physical_call_count_stays_null_when_any_adapter_count_is_unknown():
    replies = [
        _adapter_response('{"complexity":"simple"}', "luna", physical_request_count=1),
        _adapter_response('{"dialog":"new","tasks":[{"operation":"search","source":["current@1:2"],"topic":"заземление"}]}', "luna"),
    ]

    def adapter(**kwargs):
        return replies.pop(0)

    plan = plan_skill_query_v2(
        "Найди заземление", catalog=None, knowledge=DocumentKnowledgeIndex(), llm_adapter=adapter,
        config=RouterPlanConfig("luna", "sol", "injected", max_context_chars=4000),
    )
    assert plan.status == "ready"
    assert [row["physical_request_count"] for row in plan.trace["adapter_metadata"]] == [1, None]
    assert plan.trace["physical_call_count"] is None


def test_receipts_must_match_prescribed_model_and_effort():
    with pytest.raises(ReplayInputError, match="requested_model"):
        ReplayAdapter([_receipt(requested_model="gpt-6-sol")], run_id="r", input_hashes={"S01": "a" * 64},
                      prompt_hashes={"classifier": "b" * 64}, schema_hash="c" * 64,
                      expected_models={"classifier": "gpt-6-luna"}, expected_effort="medium")


def test_partial_receipt_set_is_not_complete_until_required_parser_attempt_exists():
    from run_router_v2_offline_gate import _receipt_set_complete
    classifier = _receipt(case_id="S01", raw_final_text='{"complexity":"simple"}',
                          raw_sha256=sha256_bytes(b'{"complexity":"simple"}'))
    keyed = {("S01", "classifier", 1): classifier}
    assert not _receipt_set_complete(keyed, ["S01"])
    parser = _receipt(case_id="S01", role="simple_parser", raw_final_text='{"dialog":"new","tasks":[]}',
                      raw_sha256=sha256_bytes(b'{"dialog":"new","tasks":[]}'))
    keyed[("S01", "simple_parser", 1)] = parser
    assert _receipt_set_complete(keyed, ["S01"])
    with pytest.raises(ReplayInputError, match="requested_effort"):
        ReplayAdapter([_receipt(requested_effort="low")], run_id="r", input_hashes={"S01": "a" * 64},
                      prompt_hashes={"classifier": "b" * 64}, schema_hash="c" * 64,
                      expected_models={"classifier": "gpt-6-luna"}, expected_effort="medium")


def test_parser_prompt_embeds_full_semantic_draft_schema_contract():
    from router_draft import semantic_draft_schema_description
    from router_prompts_v2 import PARSER_V2_SYSTEM

    schema_text = semantic_draft_schema_description()
    assert schema_text in PARSER_V2_SYSTEM
    for token in ('"conditions":{"type":"array","items"', '"terms":{"type":"array","items"', '"meaning":{"type":"string"}', '"additionalProperties":false'):
        assert token in PARSER_V2_SYSTEM
