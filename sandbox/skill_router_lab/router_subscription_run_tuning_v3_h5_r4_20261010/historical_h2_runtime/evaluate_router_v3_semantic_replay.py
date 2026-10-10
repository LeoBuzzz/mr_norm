"""Deterministic semantic evaluation for the frozen v3 H2 tuning receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_router_v2_tuning import _assemble, _parse_classifier, _validate_raw
from evaluate_router_v3_file_replay import replay_v3
from freeze_router_v3_run import RUN_DIR
from router_subscription_eval import canonical_json_bytes, compare_router_v2_metamorphic, load_receipts
from router_subscription_eval_v3 import compare_case


HERE = Path(__file__).resolve().parent
GOLD_PATH = HERE / "router_plan_cases_tuning_v2.json"


def semantic_replay(run_dir: Path = RUN_DIR) -> dict[str, Any]:
    run_dir = Path(run_dir)
    technical = replay_v3(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    cases_doc = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    cases = [case for case in cases_doc["cases"] if case.get("split") == "tuning"]
    if len(rows) != 20 or len(cases) != 20 or [row["case_id"] for row in rows] != [case["id"] for case in cases]:
        raise ValueError("frozen r2 inputs and tuning cases are not exactly aligned")
    keyed = {}
    for receipt in load_receipts(run_dir / "receipts.jsonl"):
        key = (receipt["case_id"], receipt["role"], receipt["attempt"])
        if key in keyed:
            raise ValueError(f"duplicate immutable receipt: {key}")
        keyed[key] = receipt
    offline_by_id = {row["case_id"]: row for row in technical["case_results"]}
    scores = []
    actual_by_id = {}
    classifier_correct = 0
    complex_expected_correct = 0
    class_rows = []
    for case, input_row in zip(cases, rows, strict=True):
        case_id = case["id"]
        classifier = keyed.get((case_id, "classifier", 1))
        expected_complexity = case["expected"]["complexity"]
        predicted_complexity = None
        actual = {}
        if classifier and not classifier.get("error"):
            predicted_complexity, classifier_error = _parse_classifier(classifier["raw_final_text"])
            if classifier_error:
                predicted_complexity = None
        classifier_ok = predicted_complexity == expected_complexity
        classifier_correct += int(classifier_ok)
        if expected_complexity == "complex" and predicted_complexity == "complex":
            complex_expected_correct += 1
        parser_role = f"{predicted_complexity}_parser" if predicted_complexity else None
        selected_raw = None
        selected_draft = None
        parser_attempts = []
        final_validation_error = None
        if parser_role:
            first = keyed.get((case_id, parser_role, 1))
            if first and not first.get("error"):
                selected_raw = first["raw_final_text"]
                selected_draft, final_validation_error, _ = _validate_raw(selected_raw, input_row)
                parser_attempts.append({"attempt": 1, "technical_valid": final_validation_error is None})
                if final_validation_error is not None:
                    repair = keyed.get((case_id, parser_role, 2))
                    if repair and not repair.get("error"):
                        selected_raw = repair["raw_final_text"]
                        selected_draft, final_validation_error, _ = _validate_raw(selected_raw, input_row)
                        parser_attempts.append({"attempt": 2, "technical_valid": final_validation_error is None})
                    else:
                        selected_draft = None
            elif first:
                parser_attempts.append({"attempt": 1, "technical_valid": False, "failure": first.get("error")})
        if selected_draft is not None and final_validation_error is None:
            actual = _assemble(input_row, predicted_complexity, selected_draft)
            actual.setdefault("trace", {})["source_manifest"] = input_row["source_manifest"]
            actual["trace"]["semantic_draft"] = selected_draft
        else:
            actual = {"complexity": predicted_complexity, "status": "needs_clarification",
                      "dialog_action": None, "tasks": [], "trace": {"source_manifest": input_row["source_manifest"],
                      "semantic_draft": {"tasks": []}}}
        actual_by_id[case_id] = actual
        scored = compare_case(case, actual, input_row)
        scores.append({"case_id": case_id, "classification": {
            "expected": expected_complexity, "predicted": predicted_complexity, "correct": classifier_ok},
            "parser_role": parser_role, "parser_attempts": parser_attempts,
            "technical": offline_by_id[case_id], "actual_plan": actual, "score": scored})
        class_rows.append({"case_id": case_id, "expected": expected_complexity,
                           "predicted": predicted_complexity, "correct": classifier_ok})

    baseline = json.loads((HERE / "baseline_reconciled_v3_20261008.json").read_text(encoding="utf-8"))
    baseline_by_id = {row["case_id"]: row for row in baseline["case_scores"]}
    h2_by_id = {row["case_id"]: row for row in scores}
    baseline_pass_regressions = []
    added_critical_codes = []
    for case_id, old in baseline_by_id.items():
        new = h2_by_id[case_id]["score"]
        if old.get("plan_match") and not new.get("plan_match"):
            baseline_pass_regressions.append(case_id)
        old_codes = {row.get("code") for row in old.get("critical_omissions", [])}
        new_codes = {row.get("code") for row in new.get("critical_omissions", [])}
        if new_codes - old_codes:
            added_critical_codes.append({"case_id": case_id, "codes": sorted(new_codes - old_codes)})
    no_new_critical_regressions = not baseline_pass_regressions and not added_critical_codes

    v03 = next(case for case in cases if case["id"] == "V03")
    v06 = next(case for case in cases if case["id"] == "V06")
    paired = compare_router_v2_metamorphic(v03["expected"], actual_by_id["V03"],
                                           v06["expected"], actual_by_id["V06"])
    plan_matched = sum(bool(row["score"].get("plan_match")) for row in scores)
    status_matched = sum(bool(row["score"].get("status_match")) for row in scores)
    semantic_matched = sum(bool(row["score"].get("task_semantics_match")) for row in scores)
    clarification_cases = [row for row in scores if cases[[c["id"] for c in cases].index(row["case_id"])]
                           ["expected"].get("status") == "needs_clarification"]
    ambiguities_correct = sum(bool(row["score"].get("plan_match")) for row in clarification_cases)
    tech = technical
    metrics = {
        "classification": {"correct": classifier_correct, "denominator": 20,
                           "pass_threshold": 18, "pass": classifier_correct >= 18,
                           "expected_complex_correct": complex_expected_correct,
                           "expected_complex_denominator": sum(c["expected"]["complexity"] == "complex" for c in cases),
                           "all_expected_complex_correct": complex_expected_correct == 11},
        "semantic_plan": {"matched": plan_matched, "denominator": 20, "pass_threshold": 18,
                          "pass": plan_matched >= 18, "useful_threshold": 15,
                          "h2_useful": plan_matched >= 15 and no_new_critical_regressions},
        "semantic_tasks": {"matched": semantic_matched, "denominator": 20},
        "status": {"matched": status_matched, "denominator": 20, "pass_threshold": 18,
                   "pass": status_matched >= 18},
        "ambiguities": {"correct": ambiguities_correct, "denominator": len(clarification_cases),
                        "case_ids": [row["case_id"] for row in clarification_cases],
                        "all_correct": ambiguities_correct == len(clarification_cases)},
        "paired_V03_V06": paired,
        "critical_regressions": {"baseline_pass_regression_case_ids": baseline_pass_regressions,
                                 "added_critical_codes": added_critical_codes,
                                 "no_new_critical_regressions": no_new_critical_regressions},
        "technical": {"first_wire_valid": tech["first_wire_valid"], "first_wire_denominator": 20,
                       "first_technical_valid": tech["first_technical_valid"], "first_technical_denominator": 20,
                       "first_technical_pass": tech["first_technical_valid"] >= 19,
                       "final_technical_valid": tech["final_technical_valid"], "final_technical_denominator": 20,
                       "final_technical_pass": tech["final_technical_valid"] == 20,
                       "repair_succeeded": tech["repair_succeeded"]},
    }
    full_gate = (metrics["classification"]["pass"] and metrics["classification"]["all_expected_complex_correct"]
                 and metrics["semantic_plan"]["pass"] and metrics["status"]["pass"]
                 and metrics["ambiguities"]["all_correct"] and paired["passed"]
                 and metrics["technical"]["first_technical_pass"] and metrics["technical"]["final_technical_pass"]
                 and metrics["critical_regressions"]["no_new_critical_regressions"])
    return {
        "schema_version": "router_v3_h2_semantic_evaluation.v1", "run_id": manifest["run_id"],
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "gold_source": "router_plan_cases_tuning_v2.json split=tuning only",
        "baseline_reconciled": baseline["baseline_reconciled"], "metrics": metrics,
        "h2_useful": metrics["semantic_plan"]["h2_useful"], "full_tuning_gate_pass": full_gate,
        "case_results": scores,
        "provenance": {"receipt_count": technical["receipt_count"], "technical_replay_complete": technical["complete"],
                       "frozen_file_hashes": manifest["files_sha256"],
                       "receipts_sha256": hashlib.sha256((run_dir / "receipts.jsonl").read_bytes()).hexdigest(),
                       "holdout_loaded": False, "network_used": False, "inputs_prompts_schema_receipts_modified": False},
    }


def replay_twice_and_write(run_dir: Path = RUN_DIR, output_path: Path | None = None) -> dict[str, Any]:
    first = semantic_replay(run_dir)
    second = semantic_replay(run_dir)
    identical = canonical_json_bytes(first) == canonical_json_bytes(second)
    if not identical:
        raise RuntimeError("semantic replay actual plans/results changed between deterministic runs")
    report = {**first, "determinism": {"runs": 2, "byte_identical_substantive_results": identical}}
    target = output_path or (Path(run_dir) / "semantic_evaluation_report.json")
    target.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    return report


def main() -> int:
    report = replay_twice_and_write()
    print(json.dumps({"run_id": report["run_id"], "baseline": report["baseline_reconciled"],
                      "metrics": report["metrics"], "h2_useful": report["h2_useful"],
                      "full_tuning_gate_pass": report["full_tuning_gate_pass"],
                      "determinism": report["determinism"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
