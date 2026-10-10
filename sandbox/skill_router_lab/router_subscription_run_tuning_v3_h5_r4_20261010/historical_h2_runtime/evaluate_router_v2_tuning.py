"""Deterministically replay frozen tuning receipts and score v2 gold offline."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from router_draft import SEMANTIC_DRAFT_SCHEMA, assemble_skill_query_plan, validate_semantic_draft
from router_subscription_eval import (
    ReplayAdapter,
    _contains_allowed,
    _normalize_semantic,
    canonical_json_bytes,
    compare_router_v2_case,
    compare_router_v2_metamorphic,
)
from run_router_v2_offline_gate import RUN_DIR, validate_run

HERE = Path(__file__).resolve().parent
GOLD_PATH = HERE / "router_plan_cases_tuning_v2.json"
REPORT_PATH = HERE / "router_subscription_eval_tuning_v2_20261008.json"


def _parse_classifier(raw: str) -> tuple[str | None, str | None]:
    try:
        value = json.loads(raw)
    except Exception as exc:
        return None, f"invalid classifier JSON: {exc}"
    if not isinstance(value, dict) or set(value) != {"complexity"} or value.get("complexity") not in {"simple", "complex"}:
        return None, "classifier must contain only simple or complex complexity"
    return value["complexity"], None


def _candidate_sets(case: dict[str, Any]) -> tuple[set[str], set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = case.get("candidates") or {}
    documents = [row for row in candidates.get("documents", []) if isinstance(row, dict)]
    terms = [str(value) for value in candidates.get("terms", []) if isinstance(value, str) and value.strip()]
    doc_ids = {str(row["doc_id"]) for row in documents if row.get("doc_id")}
    vocab = {"meaning_groups": [{"meaning_id": term, "label": term, "surface": term, "variants": [term]} for term in terms]}
    return doc_ids, set(terms), documents, vocab["meaning_groups"]


def _replace_ids(context: dict[str, Any], pending: dict[str, Any] | None) -> set[str]:
    ids = set()
    for turn in (context or {}).get("turns", []):
        for condition in turn.get("confirmed_conditions", []) or []:
            if isinstance(condition, dict) and condition.get("id"):
                ids.add(str(condition["id"]))
    if pending:
        for condition in (pending.get("original_task") or {}).get("constraints", []):
            if isinstance(condition, dict) and condition.get("id"):
                ids.add(str(condition["id"]))
    return ids


def _validate_raw(raw: str, case: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, bool]:
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return None, f"invalid JSON: {type(exc).__name__}: {exc}", False
    schema_errors = sorted(
        Draft202012Validator(SEMANTIC_DRAFT_SCHEMA).iter_errors(parsed),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if schema_errors:
        return None, f"wire/schema: {schema_errors[0].message}", False
    try:
        doc_ids, term_ids, _, _ = _candidate_sets(case)
        draft = validate_semantic_draft(
            parsed,
            case["source_manifest"],
            max_tasks=6,
            candidate_ids=doc_ids,
            term_ids=term_ids,
            replace_ids=_replace_ids(case.get("context") or {}, case.get("pending_clarification")),
        )
        return draft, None, True
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", True


def _assemble(case: dict[str, Any], complexity: str, draft: dict[str, Any]) -> dict[str, Any]:
    _, _, documents, meaning_groups = _candidate_sets(case)
    plan = assemble_skill_query_plan(
        case["question"], complexity, draft, case["source_manifest"],
        catalog_rows=documents, vocab={"meaning_groups": meaning_groups},
        trace={"replay_mode": "codex_subscription_file_replay"},
    )
    return plan.to_dict()


def _critical_omissions(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, str]]:
    omissions: list[dict[str, str]] = []
    actual_tasks = actual.get("tasks", [])
    if expected.get("status") == "ready" and actual.get("status") == "ready":
        for wanted in expected.get("tasks", []):
            candidates = [task for task in actual_tasks if task.get("skill") == {
                "text": "exact_point", "search": "corpus_point_search", "all": "find_all",
            }.get(wanted.get("operation"))]
            task = candidates[0] if len(candidates) == 1 else None
            if task is None:
                omissions.append({"code": "lost_or_wrong_operation", "detail": str(wanted.get("operation"))})
                continue
            if wanted.get("doc_id") and task.get("doc_id") != wanted["doc_id"]:
                omissions.append({"code": "wrong_document", "detail": str(wanted["doc_id"])})
            if wanted.get("point_ref") and task.get("point_ref") != wanted["point_ref"]:
                omissions.append({"code": "wrong_point", "detail": str(wanted["point_ref"])})
            query = str(task.get("query") or "")
            concepts = " ".join(str(value) for concept in task.get("concepts", [])
                                for value in [concept.get("surface", ""), *concept.get("variants", [])])
            constraints = task.get("constraints", [])
            for element in wanted.get("required_semantic_elements", []):
                kind = element.get("kind")
                variants = element.get("variants") or [element.get("label", "")]
                if kind == "topic" and not _contains_allowed(query, variants):
                    omissions.append({"code": "lost_topic_or_intent", "detail": str(element.get("label", ""))})
                elif kind in {"concept", "object"} and not _contains_allowed(concepts, variants):
                    omissions.append({"code": "lost_required_concept", "detail": str(element.get("label", ""))})
                elif kind == "point" and _normalize_semantic(task.get("point_ref") or "") not in {_normalize_semantic(v) for v in variants}:
                    omissions.append({"code": "wrong_point", "detail": str(element.get("label", ""))})
                elif kind in {"quantity", "exclusion", "condition"} and not any(
                    row.get("kind") == kind and _contains_allowed(row.get("text", ""), variants) for row in constraints
                ):
                    omissions.append({"code": "lost_required_constraint", "detail": str(element.get("label", ""))})
            for wanted_constraint in wanted.get("constraints", []):
                found = any(row.get("kind") == wanted_constraint.get("kind")
                            and row.get("polarity") == wanted_constraint.get("polarity")
                            and _contains_allowed(row.get("text", ""), wanted_constraint.get("variants") or [wanted_constraint.get("text", "")])
                            for row in constraints)
                if not found:
                    code = "lost_required_negation" if wanted_constraint.get("polarity") == "exclude" else (
                        "lost_quantity_or_unit" if wanted_constraint.get("kind") == "quantity" else "lost_required_constraint")
                    omissions.append({"code": code, "detail": str(wanted_constraint.get("text", ""))})
            if len(task.get("depends_on", [])) != len(wanted.get("depends_on", [])):
                omissions.append({"code": "lost_dependency", "detail": str(wanted.get("depends_on", []))})
    if expected.get("status") == "deferred" and actual.get("status") == "ready":
        omissions.append({"code": "ready_for_unsupported_or_dependent_intent", "detail": "expected deferred"})
    return omissions


def _substantive_replay(run_dir: Path = RUN_DIR, gold_path: Path = GOLD_PATH) -> dict[str, Any]:
    gate = validate_run(run_dir)
    if gate["receipt_state"] != "receipts_validated":
        raise ValueError(f"subscription receipts are not complete: {gate['receipt_state']}")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    inputs = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_data = json.loads(gold_path.read_text(encoding="utf-8"))
    cases = [case for case in gold_data.get("cases", []) if case.get("split") == "tuning"]
    if len(cases) != 20 or any(case.get("split") != "tuning" for case in cases):
        raise ValueError("replay requires exactly 20 tuning cases")
    if [case["id"] for case in cases] != manifest["case_order"]:
        raise ValueError("gold case order does not match frozen receipt manifest")
    if len(inputs) != 20 or [row["case_id"] for row in inputs] != manifest["case_order"]:
        raise ValueError("frozen input order does not match receipt manifest")

    input_hashes = {row["case_id"]: hashlib.sha256(canonical_json_bytes(row)).hexdigest() for row in inputs}
    prompt_hashes = {
        "classifier": manifest["files_sha256"]["prompts/classifier.txt"],
        "simple_parser": manifest["files_sha256"]["prompts/parser.txt"],
        "complex_parser": manifest["files_sha256"]["prompts/parser.txt"],
    }
    receipts_path = run_dir / "receipts.jsonl"
    from router_subscription_eval import load_receipts
    receipts = load_receipts(receipts_path)
    replay = ReplayAdapter(
        receipts, run_id=manifest["run_id"], input_hashes=input_hashes,
        prompt_hashes=prompt_hashes, schema_hash=manifest["files_sha256"]["schema.json"],
        expected_models=manifest["requested_models"], expected_effort=manifest["requested_effort"],
    )

    case_results = []
    actual_by_id: dict[str, dict[str, Any]] = {}
    classification_correct = 0
    first_wire_valid_count = 0
    first_technical_valid_count = 0
    repair_needed_count = 0
    repair_success_count = 0
    final_technical_count = 0
    assembler_error_count = 0
    for case, frozen_input in zip(cases, inputs, strict=True):
        case_id = case["id"]
        classifier_raw = replay.get(
            case_id, "classifier", 1,
            input_sha256=input_hashes[case_id], prompt_sha256=prompt_hashes["classifier"],
            schema_sha256=manifest["files_sha256"]["schema.json"],
        )
        complexity, classifier_error = _parse_classifier(classifier_raw)
        expected_complexity = case["expected"].get("complexity")
        is_class_correct = complexity is not None and complexity == expected_complexity
        classification_correct += int(is_class_correct)
        attempts = []
        final_draft = None
        final_error = classifier_error
        assembled = None
        assembler_error = None
        first_draft_wire_valid = False
        first_draft_technical_valid = False
        repair_succeeded = None
        if complexity is not None:
            role = "simple_parser" if complexity == "simple" else "complex_parser"
            for attempt in (1, 2):
                try:
                    raw = replay.get(
                        case_id, role, attempt,
                        input_sha256=input_hashes[case_id], prompt_sha256=prompt_hashes[role],
                        schema_sha256=manifest["files_sha256"]["schema.json"],
                    )
                except Exception:
                    break
                draft, error, wire_valid = _validate_raw(raw, frozen_input)
                attempts.append({"attempt": attempt, "wire_valid": wire_valid, "technical_valid": draft is not None, "error": error})
                if attempt == 1:
                    first_draft_wire_valid = wire_valid
                    first_draft_technical_valid = draft is not None
                    if not first_draft_technical_valid:
                        repair_needed_count += 1
                        repair_succeeded = False
                if draft is not None:
                    final_draft, final_error = draft, None
                    if attempt == 2:
                        repair_succeeded = True
                    break
                final_error = error
                if attempt == 1 and len([key for key in receipts if key["case_id"] == case_id and key["role"] == role]) > 1:
                    repair_succeeded = False
            if attempts and not attempts[0]["technical_valid"] and len(attempts) > 1:
                repair_succeeded = attempts[1]["technical_valid"]
            if final_draft is not None:
                try:
                    assembled = _assemble(frozen_input, complexity, final_draft)
                    final_technical_count += 1
                except Exception as exc:
                    assembler_error = f"{type(exc).__name__}: {exc}"
                    assembler_error_count += 1
        else:
            assembler_error = None
        if assembled is not None:
            actual_by_id[case_id] = assembled
        case_results.append({
            "case_id": case_id,
            "classifier": {"predicted": complexity, "expected": expected_complexity, "correct": is_class_correct, "error": classifier_error},
            "parser_role": None if complexity is None else ("simple_parser" if complexity == "simple" else "complex_parser"),
            "parser_attempts": attempts,
            "first_draft_wire_valid": first_draft_wire_valid,
            "first_draft_technical_valid": first_draft_technical_valid,
            "repair_succeeded": repair_succeeded,
            "final_technical_valid": final_draft is not None and assembled is not None,
            "final_validation_error": final_error,
            "assembler_error": assembler_error,
            "actual_plan": assembled,
        })
        first_wire_valid_count += int(first_draft_wire_valid)
        first_technical_valid_count += int(first_draft_technical_valid)
        repair_success_count += int(repair_succeeded is True)

    scores = []
    critical_by_case = {}
    for case in cases:
        actual = actual_by_id.get(case["id"])
        if actual is None:
            score = {"plan_match": False, "status_match": False, "clarification_match": False,
                     "clarification_subject_match": False, "clarification_options_match": False,
                     "task_semantics_match": False}
            omissions = [{"code": "no_final_plan", "detail": "classifier/parser/assembler did not produce a technical plan"}]
        else:
            score = compare_router_v2_case(case["expected"], actual)
            omissions = _critical_omissions(case["expected"], actual)
        scores.append({"case_id": case["id"], **{key: score.get(key, False) for key in (
            "plan_match", "status_match", "clarification_match", "clarification_subject_match",
            "clarification_options_match", "task_semantics_match", "evidence_coverage", "dependency_edges_match")}})
        critical_by_case[case["id"]] = omissions

    ambiguous = [case for case in cases if case["expected"].get("clarification_field")]
    expected_complex = [case for case in cases if case["expected"].get("complexity") == "complex"]
    complex_classification_correct = sum(
        next(row for row in case_results if row["case_id"] == case["id"])["classifier"]["predicted"] == "complex"
        for case in expected_complex
    )
    unnecessary_clarifications = [case["id"] for case in cases if not case["expected"].get("clarification_field")
                                  and actual_by_id.get(case["id"], {}).get("clarification")]
    paired = compare_router_v2_metamorphic(
        next(case["expected"] for case in cases if case["id"] == "V03"), actual_by_id.get("V03", {}),
        next(case["expected"] for case in cases if case["id"] == "V06"), actual_by_id.get("V06", {}),
    )
    receipt_rows = receipts
    roles = {role: sum(row["role"] == role for row in receipt_rows) for role in ("classifier", "simple_parser", "complex_parser")}
    verification_counts = {state: sum(row["model_verification"] == state for row in receipt_rows)
                           for state in ("host_verified", "requested_by_host_only", "unknown")}
    unknown_metadata = {
        "receipt_count": len(receipt_rows),
        "actual_model_unknown": sum(row["actual_model"] is None for row in receipt_rows),
        "usage_unknown": len(receipt_rows),
        "physical_request_count_unknown": len(receipt_rows),
        "event_id_unknown": sum(row["event_id"] is None for row in receipt_rows),
        "per_message_timestamps_unavailable": len(receipt_rows),
        "worker_transcript_hashes_unknown": 2,
        "verification_states": verification_counts,
        "note": "Workers used the requested subscription models; host exposed no server actual_model, event IDs, message timestamps, usage, or physical-request counts. Receipt timestamps are explicit host-unavailable markers.",
    }
    critical_rows = [{"case_id": case_id, **item} for case_id, items in critical_by_case.items() for item in items]
    metrics = {
        "classification": {"correct": classification_correct, "total": len(cases), "accuracy": classification_correct / len(cases)},
        "required_complex_classification": {"correct": complex_classification_correct, "total": len(expected_complex),
                                             "case_ids": [case["id"] for case in expected_complex],
                                             "all_marked_complex_classified_complex": complex_classification_correct == len(expected_complex)},
        "first_draft_wire_validity": {"valid": first_wire_valid_count, "total": len(cases), "case_ids_invalid": [r["case_id"] for r in case_results if not r["first_draft_wire_valid"]]},
        "first_draft_technical_validity": {"valid": first_technical_valid_count, "total": len(cases),
                                            "case_ids_invalid": [r["case_id"] for r in case_results if not r["first_draft_technical_valid"]]},
        "repair_success": {"needed": repair_needed_count, "succeeded": repair_success_count,
                           "case_ids_failed": [r["case_id"] for r in case_results if r["repair_succeeded"] is False]},
        "final_technical_validity": {"valid": final_technical_count, "total": len(cases),
                                     "case_ids_invalid": [r["case_id"] for r in case_results if not r["final_technical_valid"]]},
        "assembler_errors": {"count": assembler_error_count, "case_ids": [r["case_id"] for r in case_results if r["assembler_error"]]},
        "semantic_plan": {"matched": sum(row["plan_match"] for row in scores), "total": len(scores),
                          "status_matched": sum(row["status_match"] for row in scores),
                          "source_evidence_coverage_cases": sum(row.get("evidence_coverage", False) for row in scores),
                          "dependency_edges_matched_cases": sum(row.get("dependency_edges_match", False) for row in scores)},
        "critical_omissions": {"count": len(critical_rows), "by_case": critical_by_case, "rows": critical_rows},
        "clarification": {
            "ambiguous_cases": len(ambiguous),
            "field_matched": sum(bool(next(row for row in scores if row["case_id"] == case["id"])["clarification_match"]) for case in ambiguous),
            "subject_matched": sum(bool(next(row for row in scores if row["case_id"] == case["id"])["clarification_subject_match"]) for case in ambiguous),
            "options_matched": sum(bool(next(row for row in scores if row["case_id"] == case["id"])["clarification_options_match"]) for case in ambiguous),
            "unnecessary_case_ids": unnecessary_clarifications,
        },
        "paired_V03_V06": paired,
        "receipt_coverage": {"total": len(receipt_rows), "by_role": roles,
                             "classifier_cases": len({row["case_id"] for row in receipt_rows if row["role"] == "classifier"}),
                             "parser_cases": len({row["case_id"] for row in receipt_rows if row["role"] in {"simple_parser", "complex_parser"}}),
                             "expected_cases": len(cases), "complete": gate["receipt_state"] == "receipts_validated"},
        "unknown_metadata": unknown_metadata,
    }
    critical_zero = not critical_rows
    ambiguous_clarification_ok = all(
        next(row for row in scores if row["case_id"] == case["id"])["clarification_match"]
        and next(row for row in scores if row["case_id"] == case["id"])["clarification_subject_match"]
        and next(row for row in scores if row["case_id"] == case["id"])["clarification_options_match"]
        for case in ambiguous
    )
    metrics["tuning_gate"] = {
        "first_draft_threshold": first_wire_valid_count >= 19,
        "repair_final_technical_threshold": final_technical_count == 20,
        "classification_threshold": classification_correct >= 18,
        "all_expected_complex_classified_complex": complex_classification_correct == len(expected_complex),
        "semantic_plan_threshold": metrics["semantic_plan"]["matched"] >= 18,
        "no_critical_omissions": critical_zero,
        "all_ambiguous_clarified": ambiguous_clarification_ok,
        "unnecessary_clarification_limit": len(unnecessary_clarifications) <= 1,
        "paired_metamorphic_pass": bool(paired.get("passed")),
    }
    metrics["tuning_gate"]["status"] = "tuning_passed" if all(
        value for key, value in metrics["tuning_gate"].items() if key != "status"
    ) else "tuning_failed"
    return {
        "schema_version": "router_subscription_eval_report.v1",
        "run_id": manifest["run_id"],
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "case_order": manifest["case_order"],
        "metrics": metrics,
        "case_scores": scores,
        "case_results": case_results,
    }


def build_report(run_dir: Path = RUN_DIR, gold_path: Path = GOLD_PATH) -> dict[str, Any]:
    first = _substantive_replay(run_dir, gold_path)
    second = _substantive_replay(run_dir, gold_path)
    first_bytes, second_bytes = canonical_json_bytes(first), canonical_json_bytes(second)
    if first_bytes != second_bytes:
        raise RuntimeError("deterministic replay mismatch between repeated runs")
    return {
        **first,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replay_determinism": {
            "replays": 2,
            "identical_excluding_generated_at": True,
            "substantive_sha256": hashlib.sha256(first_bytes).hexdigest(),
        },
        "provenance_limits": {
            "workers": ["/root/luna_v2_tuning_worker", "/root/sol_v2_tuning_worker"],
            "worker_models": "requested gpt-6-luna medium / gpt-6-sol medium; server-side actual_model unavailable",
            "actual_model_verification": "requested_by_host_only",
            "worker_tools_used": False,
            "repair_prompt_wrapper_unavailable": True,
            "repair_success_means_second_captured_response_validated": True,
            "holdout_loaded": False,
            "network_or_live_services_used": False,
        },
    }


def main() -> int:
    report = build_report()
    REPORT_PATH.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    print(json.dumps({"report": str(REPORT_PATH), "status": report["metrics"]["tuning_gate"]["status"],
                      "metrics": report["metrics"], "replay_determinism": report["replay_determinism"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
