from __future__ import annotations

import json
from pathlib import Path

from router_draft import build_source_manifest, extract_source_ref
from freeze_router_v3_run import validate_frozen
from router_prompts_v3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_request_envelope_v3 import capture_envelope
from router_subscription_eval_v3 import (
    OPERATION_AUDIT,
    _source_evidence_match,
    _pair_checks,
    _token_is_required,
    _token_matches,
    compare_case,
    reconcile_baseline,
)


HERE = Path(__file__).resolve().parent


def test_union_evidence_and_punctuation_are_semantically_equivalent():
    query = "Покажи пункт 1.1 из ПУЭ?"
    manifest = build_source_manifest(query)
    wanted = {"source_refs": ["current@1:6"]}
    split = {"evidence": [
        {"turn_id": "current", "quote": "Покажи пункт"},
        {"turn_id": "current", "quote": "1.1"},
        {"turn_id": "current", "quote": "ПУЭ"},
    ]}
    continuous = {"evidence": [{"turn_id": "current", "quote": "Покажи пункт 1.1 ПУЭ"}]}
    assert _source_evidence_match(wanted, split, manifest)[0]
    assert _source_evidence_match(wanted, continuous, manifest)[0]
    assert not _source_evidence_match(wanted, {"evidence": [
        {"turn_id": "turn_1", "quote": query}
    ]}, manifest)[0]


def test_morphology_is_limited_to_ordinary_wordforms_and_preserves_numbers_units_negation():
    assert _token_matches("подстанции", "подстанциям")
    assert not _token_matches("110", "10")
    assert _token_is_required("не") == (True, None)
    assert _token_is_required("кВ") == (True, None)


def test_unknown_legacy_operations_have_reasoned_query_mapping():
    assert OPERATION_AUDIT["U01"]["operation"] == "compare"
    assert OPERATION_AUDIT["U02"]["operation"] == "conclude"
    assert OPERATION_AUDIT["U03"]["operation"] == "define"
    assert all(row["reason"] for row in OPERATION_AUDIT.values())


def _same_skill_pair_case():
    query = "Найди альфа и затем бета"
    source = build_source_manifest(query)
    input_row = {"source_manifest": source}
    expected = {
        "complexity": "complex", "status": "ready", "dialog_action": None,
        "clarification_field": None, "clarification_options": [], "clarification_subject": None,
        "forbidden_substitutions": [], "tasks": [
            {"operation": "search", "doc_id": None, "point_ref": None, "coverage": "ranked",
             "depends_on": [], "constraints": [], "required_semantic_elements": [
                 {"kind": "topic", "label": "альфа", "variants": ["альфа"]}], "source_refs": ["current@2:2"]},
            {"operation": "search", "doc_id": None, "point_ref": None, "coverage": "ranked",
             "depends_on": ["t1"], "constraints": [], "required_semantic_elements": [
                 {"kind": "topic", "label": "бета", "variants": ["бета"]}], "source_refs": ["current@5:5"]},
        ],
    }
    actual = {
        "complexity": "complex", "status": "ready", "dialog_action": "new", "tasks": [
            {"task_id": "t1", "skill": "corpus_point_search", "query": "бета", "doc_id": None,
             "point_ref": None, "coverage": "ranked", "concepts": [], "constraints": [],
             "depends_on": ["t2"], "evidence": [{"turn_id": "current", "quote": "бета"}]},
            {"task_id": "t2", "skill": "corpus_point_search", "query": "альфа", "doc_id": None,
             "point_ref": None, "coverage": "ranked", "concepts": [], "constraints": [],
             "depends_on": [], "evidence": [{"turn_id": "current", "quote": "альфа"}]},
        ],
        "trace": {"source_manifest": source, "semantic_draft": {"tasks": [
            {"operation": "search"}, {"operation": "search"}
        ]}},
    }
    case = {"id": "PAIR", "expected": expected}
    return case, actual, input_row


def test_same_skill_tasks_match_one_to_one_after_reordering_with_exact_dependency_edges():
    case, actual, input_row = _same_skill_pair_case()
    scored = compare_case(case, actual, input_row)
    assert scored["plan_match"]
    assert scored["task_assignment"] == {0: 1, 1: 0}
    assert scored["dependency_edges_match"]


def test_semantic_assignment_survives_wrong_dependency_edges():
    case, actual, input_row = _same_skill_pair_case()
    actual["tasks"][0]["depends_on"] = []
    scored = compare_case(case, actual, input_row)
    assert scored["task_semantics_match"]
    assert not scored["dependency_edges_match"]
    assert any(row["code"] == "dependency_edges_mismatch" for row in scored["critical_omissions"])
    assert all(row["code"] != "unmatched_expected_task" for row in scored["critical_omissions"])


def test_pair_scoring_keeps_object_quantity_negation_document_and_point_strict():
    source = build_source_manifest("Найди все требования к заземлению ВЛ 110 кВ, не для кабелей")
    wanted = {
        "operation": "all", "doc_id": "doc_fixture", "point_ref": "1.1", "coverage": "comprehensive",
        "constraints": [
            {"kind": "object", "polarity": "include", "text": "ВЛ"},
            {"kind": "quantity", "polarity": "include", "text": "110 кВ"},
            {"kind": "exclusion", "polarity": "exclude", "text": "кабелей"},
        ],
        "required_semantic_elements": [
            {"kind": "topic", "variants": ["заземление"]},
            {"kind": "object", "require_concept": True, "variants": ["ВЛ"]},
        ], "source_refs": [],
    }
    actual = {
        "skill": "find_all", "doc_id": "wrong_doc", "point_ref": "1.2", "coverage": "comprehensive",
        "query": "заземление", "concepts": [{"surface": "ВЛ", "variants": []}], "constraints": [],
        "evidence": [],
    }
    checks = _pair_checks(wanted, actual, "all", source)
    assert checks["query_semantics"] and checks["concepts"]
    assert not checks["doc_id"] and not checks["point_ref"] and not checks["conditions"]


def test_reconciled_baseline_uses_technical_gate_and_missing_attempt_semantics():
    report = reconcile_baseline()
    assert report["legacy_score"]["display"] == "7/20"
    assert report["baseline_reconciled"]["display"] == "11/20"
    assert report["metrics"]["first_wire_valid"] == 20
    assert report["metrics"]["first_technical_valid"] == 16
    assert report["metrics"]["first_technical_threshold_pass"] is False
    assert report["metrics"]["repair"]["missing_attempt_case_ids"] == ["C02"]
    assert report["metrics"]["repair"]["model_failure_case_ids"] == []
    assert report["replay"]["identical_actual_plans"] is True


def test_synthetic_prompt_source_refs_and_fixture_ids_are_valid():
    fixtures = [
        ("“Найди требования к вентиляции”", "current@5:5", "вентиляции"),
        ("“Перечисли все требования к освещению”", "current@6:6", "освещению"),
        ("“Покажи пункты 2.1 и 2.2 вымышленной инструкции Q”", "current@4:4", "2.1"),
        ("“Найди понятие марш для наружных лестниц”", "current@6:7", "наружных лестниц"),
        ("“Найди все требования только по вымышленному документу R кроме временных лестниц”", "current@11:12", "временных лестниц"),
        ("“Раскрой значение придуманного слова «лестничный шлейф»”", "current@2:9", "Раскрой значение придуманного слова «лестничный шлейф»"),
        ("“Уточни значение слова «шляпка» для вентиляционного канала”", "current@6:6", "шляпка"),
    ]
    assert PARSER_V3_SYSTEM.count("Synthetic contrasts") == 1
    assert PARSER_V3_SYSTEM.count("\n1.") == 1
    assert sum(line[:3] in {f"{number}. " for number in range(1, 7)} for line in PARSER_V3_SYSTEM.splitlines()) == 6
    assert "doc_fixture_q" in PARSER_V3_SYSTEM and "doc_fixture_r" in PARSER_V3_SYSTEM
    assert "term_fixture_m" in PARSER_V3_SYSTEM and "term_fixture_hood_cap" in PARSER_V3_SYSTEM
    assert "term_fixture_vent_hat" in PARSER_V3_SYSTEM
    for query, ref, quote in fixtures:
        assert query in PARSER_V3_SYSTEM
        source = build_source_manifest(query)
        assert extract_source_ref(ref, source)["quote"] == quote
    correction_query = "“не внутренних, а наружных”"
    prior = "“для внутренних лестниц”"
    assert correction_query in PARSER_V3_SYSTEM and prior in PARSER_V3_SYSTEM
    correction = build_source_manifest(correction_query, {"turns": [
        {"turn_id": "turn_1", "user_text": prior}
    ]})
    assert extract_source_ref("turn_1@2:4", correction)["quote"] == "для внутренних лестниц"
    assert extract_source_ref("current@2:6", correction)["quote"] == "не внутренних, а наружных"
    assert '"complexity":"simple"' in CLASSIFIER_V3_SYSTEM
    assert "Preserve original user intent" in REPAIR_V3_SYSTEM


def test_all_tuning_questions_case_ids_and_electrical_literals_are_absent_from_prompts():
    gold = json.loads((HERE / "router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))
    prompts = "\n".join([CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM]).casefold()
    normalized_prompts = " ".join(prompts.replace("ё", "е").split())
    for case in gold["cases"]:
        assert case["id"].casefold() not in prompts
        normalized_question = " ".join(case["question"].casefold().replace("ё", "е").split())
        assert normalized_question not in normalized_prompts
    tokens = {token.casefold().replace("ё", "е") for token in __import__("re").findall(r"[А-Яа-яЁёA-Za-z0-9]+", prompts)}
    assert not tokens.intersection({"авр", "птэ", "пуэ", "лэп", "вл", "заземление", "электростанции"})


def test_request_envelope_capture_hashes_the_exact_canonical_payload():
    captured = capture_envelope({
        "role": "complex_parser", "attempt": 2, "model": "gpt-6-sol", "effort": "medium",
        "messages": [{"role": "system", "content": REPAIR_V3_SYSTEM}, {"role": "user", "content": "raw/errors"}],
    })
    assert captured["capture_stage"] == "pre_dispatch"
    assert len(captured["envelope_sha256"]) == 64
    assert json.loads(captured["envelope_utf8"]) == captured["envelope"]
    try:
        capture_envelope({"role": "complex_parser", "attempt": 0, "messages": []})
    except ValueError:
        pass
    else:
        raise AssertionError("malformed request envelope was accepted")


def test_zero_task_unresolved_clarification_is_allowed_when_subject_is_grounded():
    from router_subscription_eval import canonical_json_bytes
    gold = json.loads((HERE / "router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))
    case = next(row for row in gold["cases"] if row["id"] == "A01")
    from freeze_router_v3_run import RUN_DIR
    run_dir = RUN_DIR
    input_row = next(json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(line)["case_id"] == "A01")
    actual = {"complexity": "complex", "status": "needs_clarification", "dialog_action": "clarify", "tasks": [],
              "clarification": {"field": "document", "question": "Какой документ для пункта 6.25?", "options": [], "target_task_ids": []},
              "trace": {"source_manifest": input_row["source_manifest"], "semantic_draft": {"tasks": []}}}
    scored = compare_case(case, actual, input_row)
    assert scored["unresolved_clarification_task_accepted"]


def test_v3_frozen_run_has_v2_input_bytes_order_and_schema_without_gold():
    assert validate_frozen()["status"] == "v3_frozen_inputs_valid"
    source_manifest = json.loads((HERE / "router_subscription_run_tuning_v2_criticfix3_20261008/manifest.json").read_text(encoding="utf-8"))
    from freeze_router_v3_run import RUN_DIR
    frozen_dir = RUN_DIR
    manifest = json.loads((frozen_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_order"] == source_manifest["case_order"]
    assert manifest["files_sha256"]["inputs/input_manifest.jsonl"] == source_manifest["files_sha256"]["inputs/input_manifest.jsonl"]
    assert manifest["files_sha256"]["schema.json"] == source_manifest["files_sha256"]["schema.json"]
    assert manifest["gold_included"] is False
    assert manifest["receipts_included"] is False
    assert not (frozen_dir / "gold.json").exists()


def test_reconciled_baseline_is_b_11_twice_with_expected_evaluator_only_recoveries():
    from router_subscription_eval import canonical_json_bytes

    first = reconcile_baseline()
    second = reconcile_baseline()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["baseline_reconciled"]["matched"] == 11
    assert first["baseline_reconciled"]["display"] == "11/20"
    passed = [row["case_id"] for row in first["case_scores"] if row["plan_match"]]
    assert passed == ["S01", "S03", "S04", "C01", "C03", "D01", "D02", "D04", "A01", "U01", "U02"]
    reasoned = (HERE / "baseline_reconciled_v3_reasoned_diff_20261008.md").read_text(encoding="utf-8")
    for case_id, reason in {
        "S01": "separate grounded evidence spans",
        "D01": "question mark",
        "D02": "Query/subject morphology uses",
        "A01": "parser retained the point request",
    }.items():
        assert f"**{case_id}**" in reasoned
        assert reason in reasoned


def test_source_audit_excludes_only_grammatical_connectors_and_keeps_semantic_atoms():
    from router_subscription_eval_v3 import _semantic_tokens

    tokens = _semantic_tokens("пункт 6.25 и заземление для ВЛ не ниже 110 кВ")
    by_text = {token: (required, reason) for token, required, reason in tokens}
    assert by_text["и"] == (False, "coordinating/task connector; operation or dependency is checked separately")
    assert by_text["для"] == (False, "preposition between grounded semantic atoms")
    for token in ("пункт", "6.25", "заземление", "не", "110", "кВ", "ВЛ"):
        assert by_text[token][0] is True, token


def test_unsupported_operation_audit_includes_all_legacy_unknown_rows():
    assert OPERATION_AUDIT["C01"]["operation"] == "define"
    assert OPERATION_AUDIT["U01"]["operation"] == "compare"
    assert OPERATION_AUDIT["U02"]["operation"] == "conclude"
    assert OPERATION_AUDIT["U03"]["operation"] == "define"
    assert set(OPERATION_AUDIT) == {"C01", "U01", "U02", "U03"}


def test_v3_prompts_do_not_embed_tuning_ids_gold_or_electrical_literals():
    prompts = "\n".join([CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM])
    cases = json.loads((HERE / "router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        assert case["id"] not in prompts
        assert case["question"] not in prompts
    forbidden_literals = ["АВР", "ВЛ", "ПУЭ", "ПТЭ", "110 кВ", "6.25", "6.26"]
    assert [value for value in forbidden_literals if value in prompts] == []
    assert "expected" not in prompts.lower()
    assert "gold" not in prompts.lower()


def test_file_lifecycle_persists_pre_dispatch_envelopes_and_performs_one_repair():
    import shutil
    import tempfile
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
    from freeze_router_v3_run import RUN_DIR
    frozen = RUN_DIR
    temp_root = Path(tempfile.mkdtemp(prefix="router-lifecycle-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(frozen, run_dir)
    case = {"case_id": "FAKE", "question": "вентиляция", "context": {}, "pending_clarification": None,
            "source_manifest": build_source_manifest("вентиляция"), "candidates": {"documents": [], "terms": []}}
    valid = json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:1"], "topic": "вентиляция"}]}, ensure_ascii=False)
    answers = ['{"complexity":"simple"}', '{}', valid]
    calls = []
    def dispatch(envelope):
        persisted = [json.loads(row) for row in (run_dir / "request_envelopes.jsonl").read_text(encoding="utf-8").splitlines()]
        assert persisted[-1]["envelope_sha256"]
        calls.append(envelope)
        return answers.pop(0)
    runner = FileSubscriptionRunnerV3(run_dir, dispatch,
        {"classifier": "gpt-6-luna", "simple_parser": "gpt-6-luna", "complex_parser": "gpt-6-sol"})
    try:
        result = runner.run_case(case)
        assert result["status"] == "valid"
        assert [(row["role"], row["attempt"]) for row in calls] == [("classifier", 1), ("simple_parser", 1), ("simple_parser", 2)]
        receipts = [json.loads(row) for row in (run_dir / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
        envelopes = [json.loads(row) for row in (run_dir / "request_envelopes.jsonl").read_text(encoding="utf-8").splitlines()]
        bindings = [json.loads(row) for row in (run_dir / "receipt_envelope_bindings.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row["envelope_sha256"] for row in bindings] == [row["envelope_sha256"] for row in envelopes]
        assert [(row["role"], row["attempt"]) for row in receipts] == [("classifier", 1), ("simple_parser", 1), ("simple_parser", 2)]
        assert runner.attempt_state("FAKE", "simple_parser", 2) == "completed"
        assert all("envelope_sha256" not in row and set(row) == __import__("router_subscription_eval").RECEIPT_FIELDS for row in receipts)
        try:
            runner.run_case(case)
        except Exception as exc:
            assert "refusing to resample" in str(exc)
        else:
            raise AssertionError("existing role/attempt was resampled")
    finally:
        shutil.rmtree(temp_root)


def test_file_lifecycle_records_transport_failure_without_retry():
    import shutil
    import tempfile
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3, TransportFailure
    from freeze_router_v3_run import RUN_DIR
    frozen = RUN_DIR
    temp_root = Path(tempfile.mkdtemp(prefix="router-lifecycle-fail-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(frozen, run_dir)
    case = {"case_id": "FAIL", "question": "вентиляция", "context": {}, "pending_clarification": None,
            "source_manifest": build_source_manifest("вентиляция"), "candidates": {"documents": [], "terms": []}}
    def dispatch(_):
        raise TransportFailure("offline fake")
    runner = FileSubscriptionRunnerV3(run_dir, dispatch,
        {"classifier": "gpt-6-luna", "simple_parser": "gpt-6-luna", "complex_parser": "gpt-6-sol"})
    try:
        result = runner.run_case(case)
        assert result["status"] == "transport_failure"
        assert len((run_dir / "receipts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    finally:
        shutil.rmtree(temp_root)


def test_file_lifecycle_records_model_failure_and_interrupted_attempt():
    import shutil
    import tempfile
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
    from freeze_router_v3_run import RUN_DIR
    frozen = RUN_DIR
    temp_root = Path(tempfile.mkdtemp(prefix="router-lifecycle-model-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(frozen, run_dir)
    case = {"case_id": "MODEL", "question": "вентиляция", "context": {}, "pending_clarification": None,
            "source_manifest": build_source_manifest("вентиляция"), "candidates": {"documents": [], "terms": []}}
    runner = FileSubscriptionRunnerV3(run_dir, lambda _: {"failure": "model_failure", "error": "fake model error"},
        {"classifier": "gpt-6-luna", "simple_parser": "gpt-6-luna", "complex_parser": "gpt-6-sol"})
    try:
        result = runner.run_case(case)
        assert result["status"] == "model_failure"
        assert runner.attempt_state("MODEL", "classifier", 1) == "model_failure"
        # Simulate process interruption after durable pre-dispatch capture.
        from router_subscription_lifecycle_v3 import _append_jsonl
        _append_jsonl(runner.envelope_path, {"case_id": "INTERRUPTED", "role": "classifier", "attempt": 1,
                                             "envelope_sha256": "frozen-before-dispatch", "envelope_utf8": "{}",
                                             "capture_stage": "pre_dispatch"})
        assert runner.attempt_state("INTERRUPTED", "classifier", 1) == "missing_attempt"
        rows = [json.loads(row) for row in (run_dir / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows[0]["error"].startswith("model_failure:")
    finally:
        shutil.rmtree(temp_root)


def test_v3_offline_gate_replays_inherited_receipts_and_reports_technical_states():
    import shutil
    import tempfile
    from freeze_router_v3_run import RUN_DIR
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
    from evaluate_router_v3_file_replay import replay_v3
    temp_root = Path(tempfile.mkdtemp(prefix="router-v3-gate-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(RUN_DIR, run_dir)
    row = next(json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if json.loads(line)["case_id"] == "S01")
    answers = [
        '{"complexity":"complex"}', '{}',
        json.dumps({"dialog": "new", "tasks": [{"operation": "text", "source": ["current@1:9"],
                    "document": "doc_ad116a1f6b41adb1", "point": "current@4:4"}]}, ensure_ascii=False),
    ]
    runner = FileSubscriptionRunnerV3(run_dir, lambda _: answers.pop(0))
    try:
        assert runner.run_case(row)["status"] == "valid"
        report = replay_v3(run_dir)
        assert report["receipt_count"] == 3
        assert report["first_wire_valid"] == 0
        assert report["first_technical_valid"] == 0
        assert report["first_technical_denominator"] == 20
        assert not report["first_technical_pass"]
        assert report["final_technical_valid"] == 1
        assert report["final_technical_denominator"] == 20
        assert not report["final_technical_pass"]
        assert report["repair_succeeded"] == 1
        assert report["missing_attempt_case_ids"] == sorted(case_id for case_id in json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["case_order"] if case_id != "S01")
        assert report["provenance"]["gold_loaded"] is False
    finally:
        shutil.rmtree(temp_root)


def test_v3_gate_rejects_coherently_rehashed_prompt_or_user_tampering():
    import hashlib
    import shutil
    import tempfile
    from freeze_router_v3_run import RUN_DIR
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
    from router_request_envelope_v3 import capture_envelope
    from router_subscription_eval import canonical_json_bytes
    from evaluate_router_v3_file_replay import replay_v3
    row = next(json.loads(line) for line in (RUN_DIR / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if json.loads(line)["case_id"] == "S01")
    draft = json.dumps({"dialog": "new", "tasks": [{"operation": "text", "source": ["current@1:9"],
                       "document": "doc_ad116a1f6b41adb1", "point": "current@4:4"}]}, ensure_ascii=False)
    for message_index, replacement in ((0, None), (1, "coherently altered request")):
        temp_root = Path(tempfile.mkdtemp(prefix="router-v3-tamper-", dir=HERE))
        run_dir = temp_root / "run"
        shutil.copytree(RUN_DIR, run_dir)
        outputs = ['{"complexity":"complex"}', '{}', draft]
        runner = FileSubscriptionRunnerV3(run_dir, lambda _: outputs.pop(0))
        try:
            assert runner.run_case(row)["status"] == "valid"
            envelopes_path = run_dir / "request_envelopes.jsonl"
            envelope_rows = [json.loads(line) for line in envelopes_path.read_text(encoding="utf-8").splitlines()]
            envelope_row = envelope_rows[0]
            envelope = json.loads(envelope_row["envelope_utf8"])
            if replacement is None:
                envelope["messages"][message_index]["content"] += " altered system"
            else:
                envelope["messages"][message_index]["content"] = replacement
            captured = capture_envelope(envelope)
            envelope_row["envelope_utf8"] = captured["envelope_utf8"]
            envelope_row["envelope_sha256"] = captured["envelope_sha256"]
            envelope_rows[0] = envelope_row
            envelopes_path.write_bytes(b"".join(canonical_json_bytes(value) + b"\n" for value in envelope_rows))
            bindings_path = run_dir / "receipt_envelope_bindings.jsonl"
            binding_rows = [json.loads(line) for line in bindings_path.read_text(encoding="utf-8").splitlines()]
            binding_rows[0]["envelope_sha256"] = captured["envelope_sha256"]
            if replacement is None:
                prompt_hash = hashlib.sha256(envelope["messages"][0]["content"].encode("utf-8")).hexdigest()
                binding_rows[0]["prompt_sha256"] = prompt_hash
                receipt_path = run_dir / "receipts.jsonl"
                receipt_rows = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
                receipt_rows[0]["prompt_sha256"] = prompt_hash
                receipt_path.write_bytes(b"".join(canonical_json_bytes(value) + b"\n" for value in receipt_rows))
            bindings_path.write_bytes(b"".join(canonical_json_bytes(value) + b"\n" for value in binding_rows))
            try:
                replay_v3(run_dir)
            except Exception as exc:
                assert "exact frozen lifecycle payload" in str(exc)
            else:
                raise AssertionError("coherently rehashed mutated envelope was accepted")
        finally:
            shutil.rmtree(temp_root)


def test_v3_freeze_fails_closed_on_inherited_dependency_drift():
    from freeze_router_v3_run import _check_inherited_runtime
    try:
        _check_inherited_runtime({"code_files_sha256": {"router_draft.py": "0" * 64}})
    except ValueError as exc:
        assert "inherited v2 dependency changed" in str(exc)
    else:
        raise AssertionError("inherited dependency drift was accepted")



def test_unresolved_clarification_rejects_unsafe_executable_or_unlinked_tasks():
    gold = json.loads((HERE / "router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))
    case = next(row for row in gold["cases"] if row["id"] == "A01")
    from freeze_router_v3_run import RUN_DIR
    run_dir = RUN_DIR
    input_row = next(json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(line)["case_id"] == "A01")
    base = {"complexity": "complex", "status": "needs_clarification", "dialog_action": "clarify",
            "clarification": {"field": "document", "question": "Какой документ для пункта 6.25?", "options": [], "target_task_ids": ["t1"]},
            "trace": {"source_manifest": input_row["source_manifest"], "semantic_draft": {"tasks": [{"operation": "text"}]}}}
    unsafe_ready_scope = {**base, "tasks": [{"task_id": "t1", "skill": "exact_point", "query": "пункт", "scope": "document",
                                             "doc_id": "doc_guessed", "point_ref": "33", "coverage": "exact",
                                             "concepts": [], "constraints": [], "depends_on": [],
                                             "evidence": [{"turn_id": "current", "quote": input_row["question"]}]}]}
    unlinked = {**base, "tasks": [{**unsafe_ready_scope["tasks"][0], "task_id": "t2", "scope": "unresolved", "doc_id": None}]}
    for actual in (unsafe_ready_scope, unlinked):
        scored = compare_case(case, actual, input_row)
        assert not scored["plan_match"]
        assert not scored["unresolved_clarification_task_accepted"]
        assert not scored["evidence_coverage"]


def test_semantic_assignment_reports_missing_and_bad_dag_without_unmatched_task():
    case, actual, input_row = _same_skill_pair_case()
    for bad_dependencies in ([], ["t99"]):
        mutated = json.loads(json.dumps(actual, ensure_ascii=False))
        mutated["tasks"][0]["depends_on"] = bad_dependencies
        scored = compare_case(case, mutated, input_row)
        assert scored["task_semantics_match"]
        assert not scored["dependency_edges_match"]
        assert any(row["code"] == "dependency_edges_mismatch" for row in scored["critical_omissions"])
        assert all(row["code"] != "unmatched_expected_task" for row in scored["critical_omissions"])


def test_v3_manifest_validates_inherited_dependency_hashes_and_current_code_hashes():
    import hashlib
    from freeze_router_v3_run import RUN_DIR, SOURCE_RUN, validate_frozen

    assert validate_frozen()["status"] == "v3_frozen_inputs_valid"
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inherited_v2_code_files_sha256"] == source_manifest["code_files_sha256"]
    assert set(source_manifest["code_files_sha256"]) <= set(manifest["code_files_sha256"])
    for relative, expected in manifest["code_files_sha256"].items():
        assert hashlib.sha256((HERE / relative).read_bytes()).hexdigest() == expected


def test_v3_prompt_preserves_flat_legacy_term_ids_and_ambiguity_rule():
    assert "flat strings in `candidates.terms` are valid legacy meaning IDs" in PARSER_V3_SYSTEM
    assert "may be emitted exactly as `terms[].meaning`" in PARSER_V3_SYSTEM
    assert "do not treat separate strings as synonyms" in PARSER_V3_SYSTEM
    assert "preserve uncertainty and list only those exact supplied IDs" in PARSER_V3_SYSTEM
    assert "term_fixture_hood_cap" in PARSER_V3_SYSTEM and "term_fixture_vent_hat" in PARSER_V3_SYSTEM


def test_lifecycle_envelopes_reconstruct_exact_frozen_system_and_canonical_users():
    import shutil
    import tempfile
    from evaluate_router_v2_tuning import _validate_raw
    from freeze_router_v3_run import RUN_DIR
    from router_subscription_eval import canonical_json_bytes, load_receipts, ReplayAdapter
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3

    temp_root = Path(tempfile.mkdtemp(prefix="router-v3-exact-envelope-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(RUN_DIR, run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    case = {"case_id": "EXACT", "question": "вентиляция", "context": {}, "pending_clarification": None,
            "source_manifest": build_source_manifest("вентиляция"), "candidates": {"documents": [], "terms": []}}
    invalid_raw = '{}'
    _, validation_error, _ = _validate_raw(invalid_raw, case)
    valid_raw = json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:1"], "topic": "вентиляция"}]}, ensure_ascii=False)
    outputs = ['{"complexity":"simple"}', invalid_raw, valid_raw]
    runner = FileSubscriptionRunnerV3(run_dir, lambda envelope: outputs.pop(0))
    try:
        assert runner.run_case(case)["status"] == "valid"
        envelopes = [json.loads(row) for row in (run_dir / "request_envelopes.jsonl").read_text(encoding="utf-8").splitlines()]
        decoded = [json.loads(row["envelope_utf8"]) for row in envelopes]
        classifier_user = canonical_json_bytes({"input": case}).decode("utf-8")
        parser_user = canonical_json_bytes({"input": case, "classifier": {"complexity": "simple"}}).decode("utf-8")
        repair_user = canonical_json_bytes({"original_request": parser_user, "raw_response": invalid_raw,
                                            "validation_errors": [validation_error]}).decode("utf-8")
        expected = [
            ("classifier", 1, CLASSIFIER_V3_SYSTEM, classifier_user),
            ("simple_parser", 1, PARSER_V3_SYSTEM, parser_user),
            ("simple_parser", 2, REPAIR_V3_SYSTEM, repair_user),
        ]
        for row, envelope, (role, attempt, system, user) in zip(envelopes, decoded, expected, strict=True):
            assert row["run_id"] == manifest["run_id"]
            assert envelope == {"role": role, "attempt": attempt, "model": manifest["requested_models"][role],
                                "effort": manifest["requested_effort"],
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        receipts = load_receipts(run_dir / "receipts.jsonl")
        assert all(row["run_id"] == manifest["run_id"] for row in receipts)
        bindings = [json.loads(row) for row in (run_dir / "receipt_envelope_bindings.jsonl").read_text(encoding="utf-8").splitlines()]
        assert all(row["run_id"] == manifest["run_id"] for row in bindings)
        for receipt in receipts:
            ReplayAdapter([receipt], run_id=manifest["run_id"], input_hashes={receipt["case_id"]: receipt["input_sha256"]},
                          prompt_hashes={receipt["role"]: receipt["prompt_sha256"]},
                          schema_hash=manifest["files_sha256"]["schema.json"], expected_models=manifest["requested_models"],
                          expected_effort=manifest["requested_effort"])
    finally:
        shutil.rmtree(temp_root)


def test_v3_replay_empty_snapshot_has_explicit_20_case_denominators_and_fail_states():
    from evaluate_router_v3_file_replay import replay_v3
    from freeze_router_v3_run import RUN_DIR

    report = replay_v3(RUN_DIR)
    assert report["receipt_count"] == 0
    assert report["expected_case_denominator"] == 20
    assert report["classifier_coverage"] == {"received": 0, "expected": 20}
    assert report["predicted_parser_coverage"] == {"received": 0, "expected": 20}
    assert report["coverage_complete"] is False
    assert report["complete"] is False
    assert report["first_wire_valid"] == 0 and report["first_wire_denominator"] == 20
    assert report["first_technical_valid"] == 0 and report["first_technical_denominator"] == 20
    assert report["final_technical_valid"] == 0 and report["final_technical_denominator"] == 20
    assert report["first_technical_pass"] is False
    assert report["final_technical_pass"] is False
    assert report["overall_gate_pass"] is False
    assert len(report["missing_attempt_case_ids"]) == 20
    assert report["transport_failure_case_ids"] == [] and report["model_failure_case_ids"] == []


def test_v3_r2_lineage_and_prompt_input_schema_parity_are_immutable():
    import hashlib
    from freeze_router_v3_run import RUN_DIR, PARENT_RUN_DIR, SOURCE_RUN, validate_frozen

    assert validate_frozen()["status"] == "v3_frozen_inputs_valid"
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    parent = json.loads((PARENT_RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    source = json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"].endswith("-r2")
    assert manifest["lineage"]["parent_run_id"] == parent["run_id"]
    assert manifest["lineage"]["parent_manifest_sha256"] == hashlib.sha256((PARENT_RUN_DIR / "manifest.json").read_bytes()).hexdigest()
    for rel in ["inputs/input_manifest.jsonl", "source_manifests.json", "schema.json", "prompts/classifier.txt", "prompts/parser.txt", "prompts/repair.txt"]:
        assert manifest["files_sha256"][rel] == parent["files_sha256"][rel]
    assert manifest["files_sha256"]["inputs/input_manifest.jsonl"] == source["files_sha256"]["inputs/input_manifest.jsonl"]
    assert manifest["files_sha256"]["schema.json"] == source["files_sha256"]["schema.json"]
    assert manifest["case_order"] == source["case_order"]
    assert manifest["inherited_v2_code_files_sha256"] == source["code_files_sha256"]


def test_v3_replay_rejects_raw_envelope_hash_tampering():
    import shutil
    import tempfile
    from evaluate_router_v3_file_replay import replay_v3
    from freeze_router_v3_run import RUN_DIR
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3

    temp_root = Path(tempfile.mkdtemp(prefix="router-v3-hash-tamper-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(RUN_DIR, run_dir)
    row = next(json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if json.loads(line)["case_id"] == "S01")
    outputs = ['{"complexity":"simple"}', json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:2"], "topic": "тема"}]}, ensure_ascii=False)]
    runner = FileSubscriptionRunnerV3(run_dir, lambda _: outputs.pop(0))
    try:
        runner.run_case(row)
        path = run_dir / "request_envelopes.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["envelope_sha256"] = "0" * 64
        path.write_text("\n".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in rows) + "\n", encoding="utf-8")
        try:
            replay_v3(run_dir)
        except Exception as exc:
            assert "invalid envelope hash" in str(exc)
        else:
            raise AssertionError("tampered envelope hash was accepted")
    finally:
        shutil.rmtree(temp_root)
