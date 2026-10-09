from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from evaluate_router_v2_tuning import RUN_DIR, _validate_raw, build_report


def test_frozen_subscription_replay_is_repeatable_and_reports_failed_gate_honestly():
    report = build_report()
    metrics = report["metrics"]
    assert report["replay_determinism"]["replays"] == 2
    assert report["replay_determinism"]["identical_excluding_generated_at"] is True
    assert len(report["case_order"]) == 20
    assert metrics["receipt_coverage"]["total"] == 44
    assert metrics["receipt_coverage"]["complete"] is True
    assert metrics["unknown_metadata"]["actual_model_unknown"] == 44
    assert metrics["unknown_metadata"]["physical_request_count_unknown"] == 44
    assert metrics["classification"] == {"correct": 17, "total": 20, "accuracy": 0.85}
    assert metrics["first_draft_wire_validity"]["valid"] == 20
    assert metrics["first_draft_technical_validity"]["valid"] == 16
    assert metrics["repair_success"]["needed"] == 4
    assert metrics["repair_success"]["succeeded"] == 3
    assert metrics["final_technical_validity"]["valid"] == 19
    assert metrics["semantic_plan"]["matched"] == 7
    assert 0 <= metrics["semantic_plan"]["source_evidence_coverage_cases"] <= 20
    assert metrics["paired_V03_V06"]["passed"] is True
    assert metrics["tuning_gate"]["status"] == "tuning_failed"


def test_wire_schema_validity_is_separate_from_candidate_and_source_validation():
    import json

    rows = [json.loads(line) for line in (RUN_DIR / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    case = next(row for row in rows if row["case_id"] == "S01")
    wire_valid, error, is_wire_valid = _validate_raw(
        '{"dialog":"new","tasks":[{"operation":"text","source":["current@1:4"],"document":"unknown","point":"current@4:4"}]}',
        case,
    )
    assert wire_valid is None
    assert error and "document ID is not in candidates" in error
    assert is_wire_valid is True
    _, schema_error, is_wire_valid = _validate_raw('{"dialog":"new","tasks":[],"extra":1}', case)
    assert schema_error and is_wire_valid is False
