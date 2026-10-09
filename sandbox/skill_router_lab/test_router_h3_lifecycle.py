from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evaluate_router_v3_h3_replay import replay_h3
from evaluate_router_v3_h3_semantic_replay import replay_twice_and_write, semantic_replay_h3
from freeze_router_v3_h3_run import RUN_DIR, SOURCE_RUN
from evaluate_router_v2_tuning import _assemble
from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
from router_subscription_eval import ReplayInputError, canonical_json_bytes
from router_subscription_lifecycle_h3 import FileSubscriptionRunnerH3


def _copy_run(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "h3_run"
    shutil.copytree(RUN_DIR, target)
    return target


def _valid_parser_response():
    return json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@3:9"],
                                                       "topic": "пункт 6.25 правила охраны труда"}]}, ensure_ascii=False)


def test_h3_lifecycle_dispatches_frozen_prompts_and_repairs_topic_locator(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = json.loads((run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    outputs = iter(['{"complexity":"simple"}',
                    '{"dialog":"new","tasks":[{"operation":"search","source":["current@3:9"],"topic":"current@2:2"}]}',
                    _valid_parser_response()])
    dispatched = []

    def dispatch(envelope):
        dispatched.append(envelope)
        return next(outputs)

    runner = FileSubscriptionRunnerH3(run_dir, dispatch)
    result = runner.run_case(row)
    assert result["status"] == "valid"
    assert result["semantic_draft"]["tasks"][0]["topic"] == "пункт 6.25 правила охраны труда"
    assert [envelope["role"] for envelope in dispatched] == ["classifier", "simple_parser", "simple_parser"]
    assert [envelope["attempt"] for envelope in dispatched] == [1, 1, 2]
    frozen_prompt = (run_dir / "prompts/parser.txt").read_text(encoding="utf-8")
    frozen_repair = (run_dir / "prompts/repair.txt").read_text(encoding="utf-8")
    assert dispatched[1]["messages"][0]["content"] == frozen_prompt
    assert dispatched[2]["messages"][0]["content"] == frozen_repair
    assert "TOPIC_MUST_BE_LITERAL_TEXT" in dispatched[2]["messages"][1]["content"]
    assert dispatched[2]["role"] == "simple_parser" and dispatched[2]["attempt"] == 2
    replay = replay_h3(run_dir)
    assert replay["final_technical_valid"] == 1
    assert result["plan"] == replay["case_results"][0]["actual_plan"]


def test_h3_second_locator_fails_after_exactly_one_repair_and_literal_topic_is_unchanged(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = json.loads((run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    locator = '{"dialog":"new","tasks":[{"operation":"search","source":["current@3:9"],"topic":"current@2:2"}]}'
    outputs = iter(['{"complexity":"simple"}', locator, locator])
    result = FileSubscriptionRunnerH3(run_dir, lambda _envelope: next(outputs)).run_case(row)
    assert result["status"] == "model_failure"
    assert "TOPIC_MUST_BE_LITERAL_TEXT" in result["final_error"]
    assert len(result["parser_attempts"]) == 2

    fresh = _copy_run(tmp_path / "literal")
    row = json.loads((fresh / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    outputs = iter(['{"complexity":"simple"}', _valid_parser_response()])
    literal_result = FileSubscriptionRunnerH3(fresh, lambda _envelope: next(outputs)).run_case(row)
    assert literal_result["status"] == "valid"
    assert literal_result["semantic_draft"]["tasks"][0]["topic"] == "пункт 6.25 правила охраны труда"
    assert len(literal_result["parser_attempts"]) == 1


def test_h3_empty_replay_has_fixed_denominators_and_fails_closed():
    report = replay_h3(RUN_DIR)
    assert canonical_json_bytes(report) == canonical_json_bytes(replay_h3(RUN_DIR))
    assert report["receipt_count"] == 0
    assert report["expected_case_denominator"] == 20
    assert report["classifier_coverage"] == {"received": 0, "expected": 20}
    assert report["predicted_parser_coverage"] == {"received": 0, "expected": 20}
    assert report["complete"] is False and report["overall_gate_pass"] is False
    assert report["first_technical_denominator"] == report["final_technical_denominator"] == 20
    assert report["missing_attempt_case_ids"] == sorted(row["case_id"] for row in report["case_results"])
    semantic = semantic_replay_h3(RUN_DIR)
    assert semantic["metrics"]["semantic_plan"]["denominator"] == 20
    assert semantic["metrics"]["semantic_plan"]["pass"] is False
    assert semantic["full_tuning_gate_pass"] is False


def test_h3_replay_rejects_coherently_rehashed_but_noncanonical_envelope(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = json.loads((run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    outputs = iter(['{"complexity":"simple"}', _valid_parser_response()])
    FileSubscriptionRunnerH3(run_dir, lambda _envelope: next(outputs)).run_case(row)
    assert replay_h3(run_dir)["receipt_count"] == 2

    envelope_path = run_dir / "request_envelopes.jsonl"
    captures = [json.loads(line) for line in envelope_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    classifier = next(item for item in captures if item["role"] == "classifier")
    envelope = json.loads(classifier["envelope_utf8"])
    envelope["messages"][1]["content"] = '{"input":{"case_id":"tampered"}}'
    encoded = canonical_json_bytes(envelope).decode("utf-8")
    envelope_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    classifier["envelope_utf8"] = encoded
    classifier["envelope_sha256"] = envelope_hash
    envelope_path.write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in captures))

    binding_path = run_dir / "receipt_envelope_bindings.jsonl"
    bindings = [json.loads(line) for line in binding_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    binding = next(item for item in bindings if item["role"] == "classifier")
    binding["envelope_sha256"] = envelope_hash
    binding_path.write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in bindings))
    with pytest.raises(ReplayInputError, match="not reconstructed from frozen prompt/input"):
        replay_h3(run_dir)


def test_h3_replay_binds_repair_errors_to_policy_validation(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = json.loads((run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    locator = '{"dialog":"new","tasks":[{"operation":"search","source":["current@3:9"],"topic":"current@2:2"}]}'
    outputs = iter(['{"complexity":"simple"}', locator, _valid_parser_response()])
    FileSubscriptionRunnerH3(run_dir, lambda _envelope: next(outputs)).run_case(row)
    assert replay_h3(run_dir)["repair_succeeded"] == 1

    envelope_path = run_dir / "request_envelopes.jsonl"
    captures = [json.loads(line) for line in envelope_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    repair = next(item for item in captures if item["role"] == "simple_parser" and item["attempt"] == 2)
    envelope = json.loads(repair["envelope_utf8"])
    payload = json.loads(envelope["messages"][1]["content"])
    payload["validation_errors"] = ["forged validation outcome"]
    envelope["messages"][1]["content"] = canonical_json_bytes(payload).decode("utf-8")
    encoded = canonical_json_bytes(envelope).decode("utf-8")
    envelope_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    repair["envelope_utf8"] = encoded
    repair["envelope_sha256"] = envelope_hash
    envelope_path.write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in captures))
    binding_path = run_dir / "receipt_envelope_bindings.jsonl"
    bindings = [json.loads(line) for line in binding_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    binding = next(item for item in bindings if item["role"] == "simple_parser" and item["attempt"] == 2)
    binding["envelope_sha256"] = envelope_hash
    binding_path.write_bytes(b"".join(canonical_json_bytes(item) + b"\n" for item in bindings))
    with pytest.raises(ReplayInputError, match="not reconstructed from frozen prompt/input"):
        replay_h3(run_dir)


def test_h3_freeze_rejects_coherently_rehashed_prompt_tampering(tmp_path):
    run_dir = _copy_run(tmp_path)
    path = run_dir / "prompts/parser.txt"
    path.write_text(path.read_text(encoding="utf-8") + "\nextra", encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files_sha256"]["prompts/parser.txt"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from freeze_router_v3_h3_run import validate_frozen
    with pytest.raises(ValueError, match="H3 parser prompt bytes do not match"):
        validate_frozen(run_dir)


@pytest.mark.parametrize("field,value,error", [
    ("run_id", "other-run", "H3 run identity mismatch"),
    ("parent_manifest_sha256", "0" * 64, "H3 direct/ultimate parent identity/hash mismatch"),
])
def test_h3_freeze_rejects_identity_or_parent_lineage_drift(tmp_path, field, value, error):
    run_dir = _copy_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if field == "run_id":
        manifest[field] = value
    else:
        manifest["lineage"][field] = value
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from freeze_router_v3_h3_run import validate_frozen
    with pytest.raises(ValueError, match=error):
        validate_frozen(run_dir)


def test_legacy_runner_rejects_h3_before_any_dispatch_or_write_and_still_runs_h2(tmp_path):
    h3_dir = _copy_run(tmp_path / "h3")
    h3_row = json.loads((h3_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    dispatched = []
    with pytest.raises(ReplayInputError, match="H3 run identity/policy"):
        FileSubscriptionRunnerV3(h3_dir, lambda envelope: dispatched.append(envelope) or "{}")
    assert dispatched == []
    assert not (h3_dir / "request_envelopes.jsonl").exists()
    assert not (h3_dir / "receipts.jsonl").exists()

    h2_dir = tmp_path / "h2" / "h2_run"
    shutil.copytree(SOURCE_RUN, h2_dir, ignore=shutil.ignore_patterns(
        "receipts.jsonl", "request_envelopes.jsonl", "receipt_envelope_bindings.jsonl"))
    h2_row = json.loads((h2_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[1])
    responses = iter(['{"complexity":"simple"}',
                      '{"dialog":"new","tasks":[{"operation":"search","source":["current@1:4"],"topic":"заземления"}]}'])
    h2_result = FileSubscriptionRunnerV3(h2_dir, lambda _envelope: next(responses)).run_case(h2_row)
    assert h2_result["status"] == "valid"
    assert len(h2_result["parser_attempts"]) == 1
    assert (h2_dir / "receipts.jsonl").exists()


def test_h3_end_to_end_assembly_and_reconciled_semantic_scoring(tmp_path, monkeypatch):
    run_dir = _copy_run(tmp_path)
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["case_id"]: row for row in rows}
    document = {case_id: (row.get("candidates") or {}).get("documents", []) for case_id, row in by_id.items()}
    expected_complexity = {"S01": "simple", "S02": "simple", "S03": "simple", "C02": "complex", "A01": "complex"}

    def task(operation, source, *, topic=None, doc=None, point=None):
        value = {"operation": operation, "source": [source]}
        if topic is not None:
            value["topic"] = topic
        if doc is not None:
            value["document"] = doc
        if point is not None:
            value["point"] = point
        return value

    docs = {case_id: next(iter(document[case_id]), {}).get("doc_id") for case_id in ("S01", "S03", "C02", "A01")}
    responses = {
        "S02": [json.dumps({"dialog": "new", "tasks": [task("search", "current@1:4", topic="заземлении")]}, ensure_ascii=False)],
        "S01": [json.dumps({"dialog": "new", "tasks": [task("text", "current@1:9", doc=docs["S01"], point="current@4:4")]}, ensure_ascii=False)],
        "C02": [json.dumps({"dialog": "new", "tasks": [
            task("text", "current@1:9", doc=docs["C02"], point="current@3:3"),
            task("text", "current@1:9", doc=docs["C02"], point="current@5:5"),
        ]}, ensure_ascii=False)],
        "A01": [json.dumps({"dialog": "new", "tasks": [], "uncertain": [
            {"field": "document", "source": "current@3:4", "candidates": [docs["A01"]]}
        ]}, ensure_ascii=False)],
        "S03": [
            json.dumps({"dialog": "new", "tasks": [task("all", "current@1:9", topic="current@2:2", doc=docs["S03"])]}, ensure_ascii=False),
            json.dumps({"dialog": "new", "tasks": [task("all", "current@1:9", topic="допускающего", doc=docs["S03"])]}, ensure_ascii=False),
        ],
    }
    from router_subscription_lifecycle_h3 import assemble_plan_h3 as original_assemble
    assembly_calls = []
    def tracked_assemble(*args, **kwargs):
        assembly_calls.append(args[0]["case_id"])
        return original_assemble(*args, **kwargs)
    monkeypatch.setattr("router_subscription_lifecycle_h3.assemble_plan_h3", tracked_assemble)

    lifecycle_plans = {}
    for case_id in ("S02", "S01", "C02", "A01", "S03"):
        output = iter([json.dumps({"complexity": expected_complexity[case_id]}), *responses[case_id]])
        result = FileSubscriptionRunnerH3(run_dir, lambda _envelope, output=output: next(output)).run_case(by_id[case_id])
        assert result["status"] == "valid"
        lifecycle_plans[case_id] = result["plan"]
    # The first invalid S03 draft was not sent to the assembler; only the repaired one was.
    assert assembly_calls.count("S03") == 1

    semantic = replay_twice_and_write(run_dir, tmp_path / "semantic_evaluation_report.json")
    assert semantic["determinism"]["byte_identical_substantive_results"] is True
    scored = {row["case_id"]: row for row in semantic["case_results"]}
    for case_id, plan in lifecycle_plans.items():
        assert scored[case_id]["actual_plan"] == plan
        assert scored[case_id]["score"]
    assert scored["S02"]["actual_plan"]["tasks"][0]["query"] == "заземлении"
    assert scored["S01"]["actual_plan"]["tasks"][0]["point_ref"] == "6.25"
    assert len(scored["C02"]["actual_plan"]["tasks"]) == 2
    assert [item["point_ref"] for item in scored["C02"]["actual_plan"]["tasks"]] == ["6.25", "6.26"]
    assert scored["A01"]["actual_plan"]["status"] == "needs_clarification"
    assert scored["S03"]["technical"]["repair_state"] == "succeeded"
    assert semantic["metrics"]["semantic_plan"]["denominator"] == 20
    assert semantic["metrics"]["technical"]["first_technical_denominator"] == 20
    assert semantic["metrics"]["technical"]["final_technical_denominator"] == 20
    assert isinstance(semantic["full_tuning_gate_pass"], bool)

    # Literal H3 assembly remains byte-identical to the pre-existing assembler path.
    case = by_id["S02"]
    draft = {"dialog": "new", "tasks": [task("search", "current@1:4", topic="заземлении")]}
    from router_draft_h3 import validate_semantic_draft
    validated = validate_semantic_draft(draft, case["source_manifest"])
    legacy = _assemble(case, "simple", validated)
    legacy.setdefault("trace", {})["source_manifest"] = case["source_manifest"]
    legacy["trace"]["semantic_draft"] = validated
    assert canonical_json_bytes(legacy) == canonical_json_bytes(lifecycle_plans["S02"])
