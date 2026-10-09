from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from evaluate_router_v3_h3_replay_r6 import replay_h3
from evaluate_router_v3_h3_semantic_replay_r6 import _critical_ready_losses, compute_h3_gates, replay_twice_and_write
from freeze_router_v3_h3_r6 import HERE, RUN_DIR
from router_subscription_lifecycle_h3_r6 import FileSubscriptionRunnerH3


def _passing_gate_inputs() -> dict:
    return {"coverage_complete": True, "coverage_denominator": 20,
            "first_technical_valid": 19, "first_technical_denominator": 20,
            "final_technical_valid": 20, "final_technical_denominator": 20,
            "locator_free_accepted_plans": True, "semantic_matched": 15,
            "no_new_critical_regressions": True, "zero_critical_ready_losses": True,
            "classification_correct": 18, "all_expected_complex_correct": True,
            "status_matched": 18, "all_ambiguities_correct": True,
            "paired_v03_v06_passed": True}


def test_h3_useful_rejects_15_of_20_when_first_technical_threshold_is_missed():
    metrics = _passing_gate_inputs()
    metrics["first_technical_valid"] = 18
    gates = compute_h3_gates(metrics)
    assert gates["semantic_useful_at_least_15_of_20"] is True
    assert gates["first_technical_at_least_19_of_20"] is False
    assert gates["useful"] is False


def test_h3_full_gate_rejects_18_of_20_when_complex_and_critical_requirements_fail():
    metrics = _passing_gate_inputs()
    metrics.update({"semantic_matched": 18, "all_expected_complex_correct": False,
                    "zero_critical_ready_losses": False})
    gates = compute_h3_gates(metrics)
    assert gates["useful"] is True
    assert gates["zero_critical_ready_losses"] is False
    assert gates["full_tuning_gate_pass"] is False


def test_h3_critical_ready_loss_categories_fail_closed_on_unmatched_expected_task():
    case = {"case_id": "critical", "expected": {"status": "ready", "tasks": [{
        "doc_id": "doc-x", "point_ref": "3.2", "constraints": [{"kind": "exclusion", "text": "без напряжения"}],
        "required_semantic_elements": [{"kind": "intent", "variants": ["покажи"]}, {"kind": "point", "variants": ["3.2"]}],
    }]}, "actual_plan": {"status": "ready", "tasks": []},
        "score": {"task_count_match": False, "task_semantics_match": False, "operation_match": False,
                  "task_assignment": {}, "task_checks": []}}
    report = _critical_ready_losses([case])
    assert report["zero"] is False
    assert report["counts"] == {"lost_intent": 1, "lost_negation": 1, "lost_number_or_unit": 1,
                                "wrong_doc_id": 1, "unsupported_or_dependent_chain": 0}


@pytest.mark.parametrize("field,value", [
    ("coverage_complete", False), ("locator_free_accepted_plans", False),
    ("final_technical_valid", 19), ("no_new_critical_regressions", False),
])
def test_h3_useful_requires_each_named_criterion(field, value):
    metrics = _passing_gate_inputs()
    metrics[field] = value
    assert compute_h3_gates(metrics)["useful"] is False


def test_r5_snapshot_launcher_reproduces_original_h2_metrics():
    from replay_router_h2_snapshot import replay_h2_snapshot

    result = replay_h2_snapshot()
    assert result["bindings"]["h2_manifest_sha256"] == "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
    assert result["technical"]["first_technical_valid"] == 15
    assert result["technical"]["final_technical_valid"] == 20
    assert result["semantic"]["baseline_reconciled"]["matched"] == 11
    assert result["semantic"]["metrics"]["semantic_plan"]["matched"] == 5


def test_r6_actual_lifecycle_assembly_and_receipt_replay_are_the_same(tmp_path):
    run_dir = tmp_path / "r6-run"
    shutil.copytree(RUN_DIR, run_dir)
    row = next(json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if json.loads(line)["case_id"] == "S02")
    parser = json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@1:4"],
                           "topic": "заземление"}]}, ensure_ascii=False)
    outputs = iter(['{"complexity":"simple"}', parser])
    result = FileSubscriptionRunnerH3(run_dir, lambda _envelope: next(outputs)).run_case(row)
    assert result["status"] == "valid"
    replay = replay_h3(run_dir)
    case = next(item for item in replay["case_results"] if item["case_id"] == "S02")
    assert case["actual_plan"] == result["plan"]
    semantic = replay_twice_and_write(run_dir, tmp_path / "semantic.json")
    assert semantic["determinism"]["byte_identical_substantive_results"] is True


@pytest.mark.parametrize("message_index,replacement", [(0, None), (1, "coherently altered request")])
def test_snapshot_h2_replay_rejects_coherently_rehashed_tampering(message_index, replacement):
    from router_request_envelope_v3 import capture_envelope
    from router_subscription_eval import canonical_json_bytes
    from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3
    from replay_router_h2_snapshot import SnapshotReplayError, replay_h2_snapshot

    h2_dir = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
    source_row = next(json.loads(line) for line in (h2_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
                      if json.loads(line)["case_id"] == "S01")
    temp_root = Path(tempfile.mkdtemp(prefix="router-h2-snapshot-tamper-", dir=HERE))
    run_dir = temp_root / "run"
    shutil.copytree(h2_dir, run_dir)
    for filename in ("request_envelopes.jsonl", "receipt_envelope_bindings.jsonl", "receipts.jsonl"):
        (run_dir / filename).unlink()
    draft = json.dumps({"dialog": "new", "tasks": [{"operation": "text", "source": ["current@1:9"],
                       "document": "doc_ad116a1f6b41adb1", "point": "current@4:4"}]}, ensure_ascii=False)
    outputs = ['{"complexity":"complex"}', '{}', draft]
    try:
        runner = FileSubscriptionRunnerV3(run_dir, lambda _: outputs.pop(0))
        assert runner.run_case(source_row)["status"] == "valid"
        envelopes_path = run_dir / "request_envelopes.jsonl"
        envelope_rows = [json.loads(line) for line in envelopes_path.read_text(encoding="utf-8").splitlines()]
        envelope_row = envelope_rows[0]
        envelope = json.loads(envelope_row["envelope_utf8"])
        envelope["messages"][message_index]["content"] += " altered system" if replacement is None else ""
        if replacement is not None:
            envelope["messages"][message_index]["content"] = replacement
        captured = capture_envelope(envelope)
        envelope_row["envelope_utf8"] = captured["envelope_utf8"]
        envelope_row["envelope_sha256"] = captured["envelope_sha256"]
        envelope_rows[0] = envelope_row
        envelopes_path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in envelope_rows))
        bindings_path = run_dir / "receipt_envelope_bindings.jsonl"
        bindings = [json.loads(line) for line in bindings_path.read_text(encoding="utf-8").splitlines()]
        bindings[0]["envelope_sha256"] = captured["envelope_sha256"]
        if replacement is None:
            prompt_hash = hashlib.sha256(envelope["messages"][0]["content"].encode("utf-8")).hexdigest()
            bindings[0]["prompt_sha256"] = prompt_hash
            receipts_path = run_dir / "receipts.jsonl"
            receipts = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()]
            receipts[0]["prompt_sha256"] = prompt_hash
            receipts_path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in receipts))
        bindings_path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in bindings))
        with pytest.raises(SnapshotReplayError, match="exact frozen lifecycle payload"):
            replay_h2_snapshot(run_dir=run_dir)
    finally:
        shutil.rmtree(temp_root)
