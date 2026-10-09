"""Deterministic H3 plan assembly and reconciled semantic scoring on tuning only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_router_v3_h3_replay import replay_h3
from freeze_router_v3_h3_run import HERE, RUN_DIR, RUN_ID
from router_subscription_eval import canonical_json_bytes, compare_router_v2_metamorphic
from router_subscription_eval_v3 import compare_case

GOLD_PATH = HERE / "router_plan_cases_tuning_v2.json"
BASELINE_PATH = HERE / "baseline_reconciled_v3_20261008.json"
def semantic_replay_h3(run_dir: Path = RUN_DIR, gold_path: Path = GOLD_PATH) -> dict[str, Any]:
    run_dir = Path(run_dir)
    technical = replay_h3(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    cases = [case for case in gold["cases"] if case.get("split") == "tuning"]
    if len(rows) != 20 or len(cases) != 20 or [row["case_id"] for row in rows] != [case["id"] for case in cases]:
        raise ValueError("H3 frozen inputs and tuning-only gold are not exactly aligned")
    technical_by_id = {row["case_id"]: row for row in technical["case_results"]}
    actual_by_id: dict[str, dict[str, Any]] = {}
    results = []
    classifier_correct = expected_complex_correct = plan_match_count = semantic_match_count = status_match_count = 0
    for case, input_row in zip(cases, rows, strict=True):
        case_id = case["id"]
        technical_case = technical_by_id[case_id]
        expected_complexity = case["expected"]["complexity"]
        predicted_complexity = technical_case.get("predicted_complexity")
        classifier_ok = predicted_complexity == expected_complexity
        classifier_correct += int(classifier_ok)
        if expected_complexity == "complex" and predicted_complexity == "complex":
            expected_complex_correct += 1

        # replay_h3 exposes a plan only after the H3 policy validated the selected
        # final draft and the unchanged SkillQueryPlan assembler completed.
        actual = technical_case.get("actual_plan") or {}
        actual_by_id[case_id] = actual
        score = compare_case(case, actual, input_row)
        plan_match_count += int(bool(score.get("plan_match")))
        semantic_match_count += int(bool(score.get("task_semantics_match")))
        status_match_count += int(bool(score.get("status_match")))
        results.append({
            "case_id": case_id,
            "classification": {"expected": expected_complexity, "predicted": predicted_complexity,
                               "correct": classifier_ok},
            "parser_role": technical_case.get("parser_role"),
            "technical": {key: technical_case.get(key) for key in
                          ("first_wire_valid", "first_technical_valid", "final_technical_valid", "repair_state", "final_state")},
            "actual_plan": technical_case.get("actual_plan"),
            "score": score,
        })

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_by_id = {row["case_id"]: row for row in baseline["case_scores"]}
    new_by_id = {row["case_id"]: row for row in results}
    baseline_pass_regressions = []
    added_critical_codes = []
    for case_id, old in baseline_by_id.items():
        new_score = new_by_id[case_id]["score"]
        if old.get("plan_match") and not new_score.get("plan_match"):
            baseline_pass_regressions.append(case_id)
        old_codes = {item.get("code") for item in old.get("critical_omissions", [])}
        new_codes = {item.get("code") for item in new_score.get("critical_omissions", [])}
        if new_codes - old_codes:
            added_critical_codes.append({"case_id": case_id, "codes": sorted(new_codes - old_codes)})
    no_new_critical_regressions = not baseline_pass_regressions and not added_critical_codes

    by_case = {case["id"]: case for case in cases}
    paired = compare_router_v2_metamorphic(by_case["V03"]["expected"], actual_by_id["V03"],
                                           by_case["V06"]["expected"], actual_by_id["V06"])
    clarification_results = [row for row in results
                              if by_case[row["case_id"]]["expected"].get("status") == "needs_clarification"]
    ambiguity_correct = sum(bool(row["score"].get("plan_match")) for row in clarification_results)
    expected_ambiguities = len(clarification_results)
    tech = technical
    metrics = {
        "classification": {"correct": classifier_correct, "denominator": 20, "pass_threshold": 18,
                           "pass": classifier_correct >= 18, "expected_complex_correct": expected_complex_correct,
                           "expected_complex_denominator": sum(case["expected"]["complexity"] == "complex" for case in cases),
                           "all_expected_complex_correct": expected_complex_correct == 11},
        "semantic_plan": {"matched": plan_match_count, "denominator": 20, "pass_threshold": 18,
                          "pass": plan_match_count >= 18, "useful_threshold": 15,
                          "h3_useful": plan_match_count >= 15 and no_new_critical_regressions},
        "semantic_tasks": {"matched": semantic_match_count, "denominator": 20},
        "status": {"matched": status_match_count, "denominator": 20, "pass_threshold": 18,
                   "pass": status_match_count >= 18},
        "ambiguities": {"correct": ambiguity_correct, "denominator": expected_ambiguities,
                        "case_ids": [row["case_id"] for row in clarification_results],
                        "all_correct": ambiguity_correct == expected_ambiguities},
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
                 and no_new_critical_regressions)
    return {
        "schema_version": "router_v3_h3_semantic_evaluation.v1", "run_id": RUN_ID,
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "gold_source": "router_plan_cases_tuning_v2.json split=tuning only",
        "baseline_reconciled": baseline["baseline_reconciled"], "metrics": metrics,
        "useful": metrics["semantic_plan"]["h3_useful"], "full_tuning_gate_pass": full_gate,
        "case_results": results,
        "provenance": {"receipt_count": technical["receipt_count"],
                       "technical_replay_complete": technical["complete"],
                       "network_used": False, "holdout_loaded": False,
                       "gold_input_separation": "gold read only by evaluator; absent from frozen model inputs"},
    }


def replay_twice_and_write(run_dir: Path = RUN_DIR, output_path: Path | None = None) -> dict[str, Any]:
    first = semantic_replay_h3(run_dir)
    second = semantic_replay_h3(run_dir)
    identical = canonical_json_bytes(first) == canonical_json_bytes(second)
    if not identical:
        raise RuntimeError("H3 semantic plans/scores differ across deterministic replays")
    report = {**first, "determinism": {"runs": 2, "byte_identical_substantive_results": True}}
    target = Path(output_path) if output_path else Path(run_dir) / "semantic_evaluation_report.json"
    target.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    return report


if __name__ == "__main__":
    report = replay_twice_and_write()
    print(json.dumps({"run_id": RUN_ID, "metrics": report["metrics"],
                      "full_tuning_gate_pass": report["full_tuning_gate_pass"],
                      "determinism": report["determinism"]}, ensure_ascii=False, indent=2))
