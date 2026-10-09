"""Deterministic H3 plan assembly and reconciled semantic scoring on tuning only."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
import re
from typing import Any

from evaluate_router_v3_h3_replay_r10 import replay_h3
from freeze_router_v3_h3_r10 import HERE, RUN_DIR, RUN_ID, validate_frozen
from replay_router_h2_snapshot_r10 import replay_h2_snapshot
from router_subscription_eval import canonical_json_bytes, compare_router_v2_metamorphic
from router_subscription_eval_v3 import compare_case

GOLD_PATH = HERE / "router_plan_cases_tuning_v2.json"
BASELINE_PATH = HERE / "baseline_reconciled_v3_20261008.json"
TOPIC_LOCATOR_RE = re.compile(r"(?<![\w@])[^\s@]+@\d+:\d+(?!\d)")


def _critical_ready_losses(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Find specifically prohibited ready-plan losses without changing scoring."""
    losses: list[dict[str, Any]] = []
    categories = ("lost_intent", "lost_negation", "lost_number_or_unit", "wrong_doc_id",
                  "unsupported_or_dependent_chain")
    for row in case_results:
        expected = row.get("expected") or {}
        actual = row.get("actual_plan") or {}
        if actual.get("status") != "ready":
            continue
        tasks = expected.get("tasks") or []
        actual_tasks = actual.get("tasks") or []
        score = row.get("score") or {}
        checks = score.get("task_checks") or []
        assignment = score.get("task_assignment") or {}
        found = []
        if expected.get("status") == "deferred" or any(
                task.get("operation") in {"unknown", "unsupported"} or task.get("depends_on")
                for task in tasks):
            found.append("unsupported_or_dependent_chain")
        if (any(not check.get("intent", True) for check in checks)
                or not score.get("operation_match", True)
                or not score.get("task_semantics_match", True)
                or len(actual_tasks) != len(tasks)):
            found.append("lost_intent")
        for index, task in enumerate(tasks):
            check = checks[index] if index < len(checks) else {}
            mapped_index = assignment.get(index, assignment.get(str(index)))
            assigned = mapped_index is not None
            if task.get("doc_id") and (not assigned or not check.get("doc_id", False)):
                found.append("wrong_doc_id")
            elements = task.get("required_semantic_elements") or []
            constraints = task.get("constraints") or []
            constraint_text = json.dumps(constraints, ensure_ascii=False).casefold()
            negated = any(token in constraint_text for token in ("не", "без", "исключ", "запрет", "отсутств"))
            quantified = (any((element.get("kind") or "").casefold() in {"point", "quantity", "number", "unit"}
                              for element in elements) or bool(re.search(r"\d", constraint_text))
                          or bool(task.get("point_ref")))
            if negated and (not assigned or not check.get("conditions", False)):
                found.append("lost_negation")
            if quantified and (not assigned or not check.get("point_ref", False) or not check.get("conditions", False)):
                found.append("lost_number_or_unit")
        if found:
            losses.append({"case_id": row.get("case_id"), "categories": sorted(set(found))})
    counts = {category: sum(category in row["categories"] for row in losses) for category in categories}
    return {"zero": not losses, "counts": counts, "case_results": losses}


def compute_h3_gates(metrics: dict[str, Any]) -> dict[str, Any]:
    """Pure gate calculation, kept separate so false-pass boundaries are testable."""
    coverage = bool(metrics.get("coverage_complete")) and metrics.get("coverage_denominator") == 20
    first_technical = metrics.get("first_technical_valid", 0) >= 19 and metrics.get("first_technical_denominator") == 20
    final_technical = metrics.get("final_technical_valid", 0) == 20 and metrics.get("final_technical_denominator") == 20
    locator_free = bool(metrics.get("locator_free_accepted_plans"))
    semantic_useful_threshold = metrics.get("semantic_matched", 0) >= 15
    no_new_baseline = bool(metrics.get("no_new_critical_regressions_vs_baseline",
                                       metrics.get("no_new_critical_regressions", False)))
    no_new_h2 = bool(metrics.get("no_new_critical_regressions_vs_h2",
                                 metrics.get("no_new_critical_regressions", False)))
    no_new_critical = no_new_baseline and no_new_h2
    unnecessary_clarifications = metrics.get("unnecessary_clarifications", 20)
    clarification_budget = (type(unnecessary_clarifications) is int
                            and 0 <= unnecessary_clarifications <= 1)
    useful = all((coverage, first_technical, final_technical, locator_free,
                  semantic_useful_threshold, no_new_critical))
    strict_ready_zero = bool(metrics.get("zero_critical_ready_losses"))
    strict_full = all((coverage, first_technical, final_technical, locator_free,
                       metrics.get("classification_correct", 0) >= 18,
                       metrics.get("all_expected_complex_correct") is True,
                       metrics.get("semantic_matched", 0) >= 18,
                       metrics.get("status_matched", 0) >= 18,
                       metrics.get("all_ambiguities_correct") is True,
                       metrics.get("paired_v03_v06_passed") is True,
                       no_new_baseline, no_new_h2, clarification_budget, strict_ready_zero))
    return {"complete_coverage": coverage, "first_technical_at_least_19_of_20": first_technical,
            "final_technical_20_of_20": final_technical, "locator_free_accepted_plans": locator_free,
            "semantic_useful_at_least_15_of_20": semantic_useful_threshold,
            "no_new_critical_regressions_vs_baseline": no_new_baseline,
            "no_new_critical_regressions_vs_h2": no_new_h2,
            "no_new_critical_regressions": no_new_critical,
            "unnecessary_clarifications_at_most_1": clarification_budget,
            "zero_critical_ready_losses": strict_ready_zero,
            "useful": useful, "full_tuning_gate_pass": strict_full}


def _normalize_critical_record(record: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize stable omission identity; omit only transient nearest-match hints."""
    normalized = {key: value for key, value in record.items() if key != "closest_actual_task_index"}
    if "failed_fields" in normalized:
        normalized["failed_fields"] = sorted({str(value) for value in (normalized["failed_fields"] or [])})

    def canonical(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value
    return canonical(normalized)


def _critical_record_key(record: dict[str, Any]) -> str:
    return canonical_json_bytes(_normalize_critical_record(record)).decode("utf-8")


def _regressions_against(results: list[dict[str, Any]], reference_rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual_by_id = {row["case_id"]: row.get("score", {}) for row in results}
    reference_by_id = {row["case_id"]: row for row in reference_rows}
    plan_regressions: list[str] = []
    added_critical_records: list[dict[str, Any]] = []
    if set(actual_by_id) != set(reference_by_id):
        raise ValueError("regression reference case coverage is not exactly aligned")
    for case_id, old in reference_by_id.items():
        old_score = old.get("score", old)
        new_score = actual_by_id[case_id]
        if old_score.get("plan_match") and not new_score.get("plan_match"):
            plan_regressions.append(case_id)
        old_records = Counter(_critical_record_key(item) for item in old_score.get("critical_omissions", []))
        new_record_rows = {_critical_record_key(item): item for item in new_score.get("critical_omissions", [])}
        new_records = Counter(_critical_record_key(item) for item in new_score.get("critical_omissions", []))
        for key in sorted(new_records):
            for _ in range(max(0, new_records[key] - old_records[key])):
                added_critical_records.append({"case_id": case_id,
                                               "record": _normalize_critical_record(new_record_rows[key])})
    added_critical_records.sort(key=lambda item: (item["case_id"], _critical_record_key(item["record"])))
    return {"plan_regression_case_ids": plan_regressions,
            "added_critical_records": added_critical_records,
            "no_new_critical_regressions": not plan_regressions and not added_critical_records}


def semantic_replay_h3(run_dir: Path = RUN_DIR, gold_path: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    frozen_validation = validate_frozen(run_dir)
    evaluation_dir = run_dir / "evaluation"
    frozen_gold = evaluation_dir / "router_plan_cases_tuning_v2.json"
    frozen_baseline = evaluation_dir / "baseline_reconciled_v3_20261008.json"
    if gold_path is not None and Path(gold_path).resolve() != frozen_gold.resolve():
        raise ValueError("unchecked gold_path substitution rejected; use the r9-frozen evaluation gold")
    expected_resources = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["evaluation_data_sha256"]
    gold_sha = hashlib.sha256(frozen_gold.read_bytes()).hexdigest()
    baseline_sha = hashlib.sha256(frozen_baseline.read_bytes()).hexdigest()
    if (gold_sha != expected_resources["evaluation/router_plan_cases_tuning_v2.json"]
            or baseline_sha != expected_resources["evaluation/baseline_reconciled_v3_20261008.json"]):
        raise ValueError("H3 r9 scorer resources failed frozen hash validation")
    technical = replay_h3(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold = json.loads(frozen_gold.read_text(encoding="utf-8"))
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
            "expected": case["expected"],
            "classification": {"expected": expected_complexity, "predicted": predicted_complexity,
                               "correct": classifier_ok},
            "parser_role": technical_case.get("parser_role"),
            "technical": {key: technical_case.get(key) for key in
                          ("first_wire_valid", "first_technical_valid", "final_technical_valid", "repair_state", "final_state")},
            "actual_plan": technical_case.get("actual_plan"),
            "score": score,
        })

    baseline = json.loads(frozen_baseline.read_text(encoding="utf-8"))
    baseline_regressions = _regressions_against(results, baseline["case_scores"])
    h2_reference = replay_h2_snapshot(run_dir=run_dir)
    h2_regressions = _regressions_against(results, h2_reference["semantic"]["case_results"])
    no_new_critical_regressions = (baseline_regressions["no_new_critical_regressions"]
                                   and h2_regressions["no_new_critical_regressions"])

    by_case = {case["id"]: case for case in cases}
    paired = compare_router_v2_metamorphic(by_case["V03"]["expected"], actual_by_id["V03"],
                                           by_case["V06"]["expected"], actual_by_id["V06"])
    clarification_results = [row for row in results
                              if by_case[row["case_id"]]["expected"].get("status") == "needs_clarification"]
    ambiguity_correct = sum(bool(row["score"].get("plan_match")) for row in clarification_results)
    expected_ambiguities = len(clarification_results)
    unnecessary_clarification_case_ids = [row["case_id"] for row in results
                                          if by_case[row["case_id"]]["expected"].get("status") != "needs_clarification"
                                          and (row.get("actual_plan") or {}).get("status") == "needs_clarification"]
    unnecessary_clarifications = len(unnecessary_clarification_case_ids)
    tech = technical
    ready_critical = _critical_ready_losses(results)
    accepted_rows = [row for row in technical["case_results"] if row.get("final_technical_valid")]
    locator_free_accepted = bool(accepted_rows) and len(accepted_rows) == technical["final_technical_valid"] and all(
        row.get("actual_plan") is not None and
        not any(isinstance(task.get("topic"), str) and TOPIC_LOCATOR_RE.search(task["topic"])
                for task in (row.get("final_draft") or {}).get("tasks", []) if isinstance(task, dict))
        for row in accepted_rows
    )
    metrics = {
        "classification": {"correct": classifier_correct, "denominator": 20, "pass_threshold": 18,
                           "pass": classifier_correct >= 18, "expected_complex_correct": expected_complex_correct,
                           "expected_complex_denominator": sum(case["expected"]["complexity"] == "complex" for case in cases),
                           "all_expected_complex_correct": expected_complex_correct == 11},
        "semantic_plan": {"matched": plan_match_count, "denominator": 20, "pass_threshold": 18,
                          "pass": plan_match_count >= 18, "useful_threshold": 15},
        "semantic_tasks": {"matched": semantic_match_count, "denominator": 20},
        "status": {"matched": status_match_count, "denominator": 20, "pass_threshold": 18,
                   "pass": status_match_count >= 18},
        "ambiguities": {"correct": ambiguity_correct, "denominator": expected_ambiguities,
                        "case_ids": [row["case_id"] for row in clarification_results],
                        "all_correct": ambiguity_correct == expected_ambiguities},
        "paired_V03_V06": paired,
        "critical_regressions": {"versus_baseline": baseline_regressions,
                                 "versus_h2": h2_regressions,
                                 "no_new_critical_regressions": no_new_critical_regressions},
        "unnecessary_clarifications": {"count": unnecessary_clarifications,
                                       "case_ids": unnecessary_clarification_case_ids,
                                       "budget": 1, "pass": unnecessary_clarifications <= 1},
        "critical_ready_losses": ready_critical,
        "technical": {"first_wire_valid": tech["first_wire_valid"], "first_wire_denominator": 20,
                      "first_technical_valid": tech["first_technical_valid"], "first_technical_denominator": 20,
                      "first_technical_pass": tech["first_technical_valid"] >= 19,
                      "final_technical_valid": tech["final_technical_valid"], "final_technical_denominator": 20,
                      "final_technical_pass": tech["final_technical_valid"] == 20,
                      "repair_succeeded": tech["repair_succeeded"]},
    }
    gate_inputs = {"coverage_complete": technical["complete"], "coverage_denominator": technical["expected_case_denominator"],
                   "first_technical_valid": technical["first_technical_valid"], "first_technical_denominator": technical["first_technical_denominator"],
                   "final_technical_valid": technical["final_technical_valid"], "final_technical_denominator": technical["final_technical_denominator"],
                   "locator_free_accepted_plans": locator_free_accepted, "semantic_matched": plan_match_count,
                   "no_new_critical_regressions_vs_baseline": baseline_regressions["no_new_critical_regressions"],
                   "no_new_critical_regressions_vs_h2": h2_regressions["no_new_critical_regressions"],
                   "unnecessary_clarifications": unnecessary_clarifications,
                   "zero_critical_ready_losses": ready_critical["zero"],
                   "classification_correct": classifier_correct,
                   "all_expected_complex_correct": expected_complex_correct == 11,
                   "status_matched": status_match_count,
                   "all_ambiguities_correct": ambiguity_correct == expected_ambiguities == 3,
                   "paired_v03_v06_passed": paired["passed"]}
    gates = compute_h3_gates(gate_inputs)
    metrics["gates"] = gates
    return {
        "schema_version": "router_v3_h3_semantic_evaluation.v1", "run_id": RUN_ID,
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "gold_source": "r9-frozen evaluation/router_plan_cases_tuning_v2.json split=tuning only",
        "baseline_reconciled": baseline["baseline_reconciled"], "metrics": metrics,
        "useful": gates["useful"], "full_tuning_gate_pass": gates["full_tuning_gate_pass"],
        "case_results": results,
        "provenance": {"receipt_count": technical["receipt_count"],
                       "technical_replay_complete": technical["complete"],
                       "r10_run_id": frozen_validation["run_id"],
                       "r10_manifest_sha256": frozen_validation["manifest_sha256"],
                       "consumed_resources": {"gold_sha256": gold_sha, "baseline_sha256": baseline_sha,
                                               "gold_path": str(frozen_gold), "baseline_path": str(frozen_baseline)},
                       "h2_reference_run_id": manifest["external_resources"]["h2_run"]["run_id"],
                       "h2_reference_manifest_sha256": manifest["external_resources"]["h2_run"]["manifest_sha256"],
                       "r10_snapshot_inventory_sha256": h2_reference["bindings"]["r10_snapshot_inventory_sha256"],
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
