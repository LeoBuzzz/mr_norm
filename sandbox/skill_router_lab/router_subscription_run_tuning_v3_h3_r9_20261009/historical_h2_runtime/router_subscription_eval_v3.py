"""Reconciled, deterministic scorer for the v3 question-parser experiment.

This module never calls a model or changes the frozen v2 receipts or gold.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from router_draft import extract_source_ref
from router_subscription_eval import (
    _contains_allowed,
    _normalize_semantic,
    canonical_json_bytes,
    compare_router_v2_case,
    compare_router_v2_metamorphic,
)
from mr_norm.retrieval.text_normalize import morphology_phrase_matches_query, normalize_catalog_text


HERE = Path(__file__).resolve().parent
V2_RUN = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
V2_GOLD = HERE / "router_plan_cases_tuning_v2.json"
V2_REPORT = HERE / "router_subscription_eval_tuning_v2_20261008.json"

# The old gold used "unknown" for three explicitly unsupported operations.
# These mappings are determined from the frozen user query, before any new run.
OPERATION_AUDIT = {
    "C01": {"task_index": 0, "operation": "define", "reason": "question explicitly asks what the abbreviation means; frozen gold marks this unsupported operation unknown"},
    "U01": {"task_index": 1, "operation": "compare", "reason": "question explicitly says compare requirements"},
    "U02": {"task_index": 1, "operation": "conclude", "reason": "question explicitly asks for a conclusion about conformity"},
    "U03": {"task_index": 0, "operation": "define", "reason": "question explicitly asks for a definition"},
}

# Negation, quantifiers, numbers, units, task verbs, and content words are
# deliberately retained. Prepositions are omitted only between semantic atoms.
_JOINERS = {"а", "и", "но", "или", "либо", "затем", "потом"}
_PREPOSITIONS = {"в", "во", "на", "к", "ко", "по", "из", "о", "об", "обо",
                 "от", "до", "для", "с", "со", "у", "за", "при", "про"}
_WORD_RE = re.compile(r"\d+(?:[._]\d+)*|[^\W\d_]+(?:[-’'][^\W\d_]+)*|[^\s]", re.UNICODE)
_NUMERIC_RE = re.compile(r"\d+(?:[._]\d+)*")


def _token_key(token: str) -> str:
    return normalize_catalog_text(token)


def _token_is_required(token: str) -> tuple[bool, str | None]:
    if not any(char.isalnum() for char in token):
        return False, "punctuation carries no semantic atom"
    return True, None


def _semantic_tokens(text: str) -> list[tuple[str, bool, str | None]]:
    tokens = _WORD_RE.findall(text)
    result = []
    for index, token in enumerate(tokens):
        include, reason = _token_is_required(token)
        normalized = _token_key(token)
        if normalized in _JOINERS and include:
            include, reason = False, "coordinating/task connector; operation or dependency is checked separately"
        elif normalized in _PREPOSITIONS and include:
            left = index > 0 and any(char.isalnum() for char in tokens[index - 1]) and _token_key(tokens[index - 1]) not in _JOINERS | _PREPOSITIONS
            right = index + 1 < len(tokens) and any(char.isalnum() for char in tokens[index + 1]) and _token_key(tokens[index + 1]) not in _JOINERS | _PREPOSITIONS
            if left and right:
                include, reason = False, "preposition between grounded semantic atoms"
        result.append((token, include, reason))
    return result


def build_source_audit_map(cases: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain every token in every legacy required span without using outputs."""
    input_by_id = {row["case_id"]: row for row in inputs}
    rows = []
    for case in cases:
        manifest = input_by_id[case["id"]]["source_manifest"]
        for task_index, task in enumerate(case["expected"].get("tasks", [])):
            for ref in task.get("source_refs", []):
                parsed = re.fullmatch(r"([^@]+)@(\d+):(\d+)", ref)
                if not parsed:
                    raise ValueError(f"malformed frozen source ref: {case['id']} {ref}")
                turn, first, last = parsed.group(1), int(parsed.group(2)), int(parsed.group(3))
                tokens = manifest["tokens"].get(turn, [])
                if first < 1 or last > len(tokens) or last < first:
                    raise ValueError(f"source ref is outside frozen source: {case['id']} {ref}")
                audited = []
                source_slice = tokens[first - 1:last]
                classifications = _semantic_tokens(" ".join(item["text"] for item in source_slice))
                for item, (_, required, reason) in zip(source_slice, classifications, strict=True):
                    audited.append({"ordinal": item["ordinal"], "text": item["text"],
                                    "required_semantic_atom": required, "exclusion_reason": reason})
                rows.append({"case_id": case["id"], "task_index": task_index, "source_ref": ref,
                             "turn_id": turn, "tokens": audited})
    return {"schema_version": "source_audit_map.v1", "rules": {
        "required": "content words, task/negation markers, numeric atoms, and units",
        "excluded": "punctuation, coordinating/task connectors checked separately, and prepositions only between semantic atoms",
        "morphology": "existing mr_norm.retrieval.text_normalize normalization for ordinary Russian forms",
        "numbers_units_negation": "exact semantic atoms; never synonym-normalized",
        "derived_from": "frozen source spans and general token rules only; actual outputs not read",
    }, "spans": rows}


def _token_matches(required: str, actual: str) -> bool:
    left, right = _token_key(required), _token_key(actual)
    if not left or not right:
        return False
    if _NUMERIC_RE.fullmatch(left) or _NUMERIC_RE.fullmatch(right):
        return left == right
    if len(left) <= 3 or len(right) <= 3:
        return left == right
    return morphology_phrase_matches_query(left, right, min_stems=1)


def _phrase_matches_morphologically(text: str, variants: list[str]) -> bool:
    return any(_contains_allowed(text, [variant]) or morphology_phrase_matches_query(
        normalize_catalog_text(variant), normalize_catalog_text(text), min_stems=1
    ) for variant in variants if variant)


def _source_evidence_match(wanted: dict[str, Any], task: dict[str, Any], manifest: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    required_by_turn: dict[str, list[str]] = {}
    audit = []
    for ref in wanted.get("source_refs", []):
        required = extract_source_ref(ref, manifest)
        turn = required["turn_id"]
        for word, include, reason in _semantic_tokens(required["quote"]):
            audit.append({"source_ref": ref, "token": word, "required": include, "reason": reason})
            if include:
                required_by_turn.setdefault(turn, []).append(word)
    actual_by_turn: dict[str, list[str]] = {}
    for evidence in task.get("evidence", []):
        turn = str(evidence.get("turn_id", ""))
        actual_by_turn.setdefault(turn, []).extend(_WORD_RE.findall(str(evidence.get("quote", ""))))
    for turn, required_words in required_by_turn.items():
        available = actual_by_turn.get(turn, [])
        if any(not any(_token_matches(word, token) for token in available) for word in required_words):
            return False, audit
    return True, audit


def _reconciled_expected(case: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(json.dumps(case["expected"], ensure_ascii=False))
    override = OPERATION_AUDIT.get(case["id"])
    if override:
        expected["tasks"][override["task_index"]]["operation"] = override["operation"]
    return expected


def _task_index(value: Any) -> int | None:
    match = re.fullmatch(r"t?(\d+)", str(value))
    return int(match.group(1)) - 1 if match else None


def _pair_checks(wanted: dict[str, Any], task: dict[str, Any], actual_operation: str | None,
                 source_manifest: dict[str, Any]) -> dict[str, bool]:
    skill_by_operation = {"text": "exact_point", "search": "corpus_point_search", "all": "find_all"}
    query = str(task.get("query") or "")
    concepts = " ".join(str(value) for row in task.get("concepts", [])
                        for value in [row.get("surface", ""), *row.get("variants", [])])
    constraints = task.get("constraints", [])
    query_ok = concepts_ok = conditions_ok = semantic_ok = True
    for element in wanted.get("required_semantic_elements", []):
        kind = element.get("kind")
        variants = element.get("variants") or [element.get("label", "")]
        if kind == "topic":
            found = _phrase_matches_morphologically(query, variants)
            query_ok = query_ok and found
            semantic_ok = semantic_ok and found
        elif kind in {"concept", "object"} or element.get("require_concept"):
            found = _contains_allowed(concepts, variants)
            concepts_ok = concepts_ok and found
            semantic_ok = semantic_ok and found
        elif kind == "point":
            semantic_ok = semantic_ok and any(_normalize_semantic(str(task.get("point_ref") or "")) == _normalize_semantic(value) for value in variants)
        elif kind == "intent":
            semantic_ok = semantic_ok and actual_operation == wanted.get("operation")
        elif kind in {"condition", "quantity", "exclusion"}:
            found = any(row.get("kind") == kind and _contains_allowed(str(row.get("text", "")), variants) for row in constraints)
            conditions_ok = conditions_ok and found
            semantic_ok = semantic_ok and found
    for expected_constraint in wanted.get("constraints", []):
        variants = expected_constraint.get("variants") or [expected_constraint.get("text", "")]
        found = any(row.get("kind") == expected_constraint.get("kind")
                    and row.get("polarity") == expected_constraint.get("polarity")
                    and _contains_allowed(str(row.get("text", "")), variants)
                    for row in constraints)
        conditions_ok = conditions_ok and found
        semantic_ok = semantic_ok and found
    return {
        "skill": task.get("skill") == skill_by_operation.get(wanted.get("operation")),
        "intent": actual_operation == wanted.get("operation"),
        "doc_id": task.get("doc_id") == wanted.get("doc_id"),
        "point_ref": wanted.get("point_ref") is None or task.get("point_ref") == wanted.get("point_ref"),
        "coverage": wanted.get("coverage") is None or task.get("coverage") == wanted.get("coverage"),
        "query_semantics": query_ok, "semantic_elements": semantic_ok,
        "concepts": concepts_ok, "conditions": conditions_ok,
        "evidence": _source_evidence_match(wanted, task, source_manifest)[0],
    }


def _dependencies_match(wanted_tasks: list[dict[str, Any]], actual_tasks: list[dict[str, Any]],
                        assignment: dict[int, int]) -> bool:
    if len(assignment) != len(wanted_tasks) or len(assignment) != len(actual_tasks):
        return False
    inverse = {actual_index: expected_index for expected_index, actual_index in assignment.items()}
    expected_edges = set()
    actual_edges = set()
    for expected_index, wanted in enumerate(wanted_tasks):
        for dependency in wanted.get("depends_on", []):
            dependency_index = _task_index(dependency)
            if dependency_index is None:
                return False
            expected_edges.add((expected_index, dependency_index))
    for actual_index, task in enumerate(actual_tasks):
        dependent_expected_index = inverse.get(actual_index)
        for dependency in task.get("depends_on", []):
            dependency_actual_index = _task_index(dependency)
            dependency_expected_index = inverse.get(dependency_actual_index) if dependency_actual_index is not None else None
            if dependent_expected_index is None or dependency_expected_index is None:
                return False
            actual_edges.add((dependent_expected_index, dependency_expected_index))
    return expected_edges == actual_edges


def compare_case(case: dict[str, Any], actual: dict[str, Any], input_row: dict[str, Any]) -> dict[str, Any]:
    expected = _reconciled_expected(case)
    base = compare_router_v2_case(expected, actual)
    actual_tasks = actual.get("tasks", [])
    source_manifest = (actual.get("trace") or {}).get("source_manifest") or input_row["source_manifest"]
    wanted_tasks = expected.get("tasks", [])
    raw_ops = ((actual.get("trace") or {}).get("semantic_draft") or {}).get("tasks", [])
    pair_map: dict[int, dict[int, dict[str, bool]]] = {}
    candidate_map: dict[int, list[int]] = {}
    for wi, wanted in enumerate(wanted_tasks):
        pair_map[wi] = {}
        candidate_map[wi] = []
        for ai, task in enumerate(actual_tasks):
            operation = raw_ops[ai].get("operation") if ai < len(raw_ops) else None
            checks = _pair_checks(wanted, task, operation, source_manifest)
            pair_map[wi][ai] = checks
            if all(checks.values()):
                candidate_map[wi].append(ai)
    order = sorted(range(len(wanted_tasks)), key=lambda index: len(candidate_map[index]))
    assignment: dict[int, int] = {}
    semantic_assignment: dict[int, int] | None = None
    dag_assignment: dict[int, int] | None = None
    def assign(index: int, used: set[int]) -> bool:
        nonlocal semantic_assignment, dag_assignment
        if index == len(order):
            if semantic_assignment is None:
                semantic_assignment = dict(assignment)
            if _dependencies_match(wanted_tasks, actual_tasks, assignment):
                dag_assignment = dict(assignment)
                return True
            return False
        wanted_index = order[index]
        for ai in candidate_map[wanted_index]:
            if ai not in used:
                assignment[wanted_index] = ai
                if assign(index + 1, used | {ai}):
                    return True
        assignment.pop(wanted_index, None)
        return False
    assign(0, set())
    assignment_found = semantic_assignment is not None
    assignment = dag_assignment or semantic_assignment or {}
    dependency_edges_match = dag_assignment is not None

    evidence_checks = []
    for wi, wanted in enumerate(wanted_tasks):
        ai = assignment.get(wi)
        if ai is None:
            evidence_checks.append(False)
        else:
            evidence_checks.append(_source_evidence_match(wanted, actual_tasks[ai], source_manifest)[0])
    evidence_match = all(evidence_checks) and len(wanted_tasks) == len(actual_tasks)

    operation_checks = []
    for wi, wanted in enumerate(wanted_tasks):
        ai = assignment.get(wi)
        if ai is None:
            operation_checks.append(False)
        else:
            expected_op = wanted.get("operation")
            raw_ops = ((actual.get("trace") or {}).get("semantic_draft") or {}).get("tasks", [])
            actual_op = raw_ops[ai].get("operation") if ai < len(raw_ops) else None
            operation_checks.append(actual_op == expected_op)
    operation_match = all(operation_checks)

    # A retained, grounded task is legitimate while a document clarification is
    # open only when it remains non-executable and has no guessed document/scope.
    unresolved_clarification_ok = False
    if not wanted_tasks and expected.get("status") == "needs_clarification" and actual.get("status") == "needs_clarification":
        clarification = actual.get("clarification") or {}
        linked = set(clarification.get("target_task_ids", []))
        current_tokens = input_row["source_manifest"]["tokens"].get("current", [])
        original_ref = f"current@1:{len(current_tokens)}" if current_tokens else None
        unresolved_grounding = {"source_refs": [original_ref]} if original_ref else {"source_refs": []}
        unresolved_clarification_ok = not actual_tasks or all(
            task.get("task_id") in linked and task.get("doc_id") is None and task.get("scope") == "unresolved"
            and _source_evidence_match(unresolved_grounding, task, source_manifest)[0]
            for task in actual_tasks
        )
        if unresolved_clarification_ok:
            evidence_match = all(
                any(row.get("turn_id") == "current" for row in task.get("evidence", []))
                for task in actual_tasks
            )
    if not wanted_tasks and actual_tasks and not unresolved_clarification_ok:
        evidence_match = False

    # V3 allows ordinary inflectional variants only for query and clarification
    # subject. IDs, points, quantities, units, negation, and polarity stay strict.
    semantic_pair_checks = []
    selected_task_checks = []
    for wi, wanted in enumerate(wanted_tasks):
        ai = assignment.get(wi)
        if ai is None:
            semantic_pair_checks.append(False)
            selected_task_checks.append(min(pair_map[wi].values(), key=lambda row: sum(row.values()), default={}))
            continue
        checks = pair_map[wi][ai]
        semantic_pair_checks.append(all(checks.values()))
        selected_task_checks.append(checks)
    strict_evidence = base.get("evidence_coverage", False)
    # Legacy one-span strict metric is retained for diagnostics, not used as the
    # reconciled score. V3 semantic evidence uses union coverage above.
    semantic_tasks_match = (len(wanted_tasks) == len(actual_tasks) and all(semantic_pair_checks))
    clarification_subject_match = bool(base.get("clarification_subject_match"))
    subject_spec = expected.get("clarification_subject") or {}
    if subject_spec and actual.get("clarification"):
        clarification_subject_match = _phrase_matches_morphologically(
            str((actual.get("clarification") or {}).get("question") or ""),
            subject_spec.get("variants") or [subject_spec.get("label", "")],
        )
    plan_match = bool(base.get("plan_match"))
    if expected.get("tasks"):
        plan_match = (semantic_tasks_match and evidence_match and operation_match
                      and dependency_edges_match
                      and base.get("classification_match", False) and base.get("status_match", False)
                      and base.get("clarification_match", False) and clarification_subject_match
                      and base.get("clarification_options_match", False)
                      and base.get("dialog_action_match", False)
                      and base.get("forbidden_substitutions_match", False))
    elif expected.get("status") == "needs_clarification":
        plan_match = (base.get("status_match", False) and base.get("clarification_match", False)
                      and clarification_subject_match
                      and base.get("clarification_options_match", False)
                      and unresolved_clarification_ok)
    critical_omissions = []
    if wanted_tasks and not assignment_found:
        for index in range(len(wanted_tasks)):
            closest_index, closest_checks = max(
                pair_map[index].items(), key=lambda item: sum(item[1].values()), default=(None, {})
            )
            critical_omissions.append({"code": "unmatched_expected_task", "task_index": index,
                                       "closest_actual_task_index": closest_index,
                                       "failed_fields": [key for key, value in closest_checks.items() if not value]})
    if wanted_tasks and assignment_found and not dependency_edges_match:
        critical_omissions.append({"code": "dependency_edges_mismatch"})
    if expected.get("status") == "deferred" and actual.get("status") == "ready":
        critical_omissions.append({"code": "ready_for_unsupported_or_dependent_intent"})
    return {
        **base,
        "plan_match": plan_match,
        "task_semantics_match": semantic_tasks_match if wanted_tasks else base.get("task_semantics_match", False),
        "dependency_edges_match": dependency_edges_match,
        "clarification_subject_match": clarification_subject_match,
        "evidence_coverage": evidence_match,
        "strict_single_span_evidence_coverage": strict_evidence,
        "operation_match": operation_match,
        "task_assignment": assignment,
        "task_checks": selected_task_checks,
        "critical_omissions": critical_omissions,
        "unresolved_clarification_task_accepted": unresolved_clarification_ok,
        "operation_audit": OPERATION_AUDIT.get(case["id"]),
    }


def reconcile_baseline() -> dict[str, Any]:
    """Replay the frozen v2 receipts twice and rescore only their actual plans."""
    from evaluate_router_v2_tuning import _substantive_replay
    from run_router_v2_offline_gate import validate_run
    gate = validate_run(V2_RUN)
    if gate["receipt_state"] != "receipts_validated":
        raise ValueError("old subscription receipts are incomplete")
    gold = json.loads(V2_GOLD.read_text(encoding="utf-8"))
    cases = [case for case in gold["cases"] if case.get("split") == "tuning"]
    inputs = [json.loads(line) for line in (V2_RUN / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(cases) != 20 or len(inputs) != 20 or [case["id"] for case in cases] != [row["case_id"] for row in inputs]:
        raise ValueError("frozen tuning inputs/gold are not aligned")
    audit_map = build_source_audit_map(cases, inputs)
    first_raw = _substantive_replay(V2_RUN, V2_GOLD)
    second_raw = _substantive_replay(V2_RUN, V2_GOLD)
    if canonical_json_bytes(first_raw) != canonical_json_bytes(second_raw):
        raise RuntimeError("legacy receipt replay is not deterministic")
    actual_by_id = {row["case_id"]: row["actual_plan"] for row in first_raw["case_results"] if row.get("actual_plan")}
    input_by_id = {row["case_id"]: row for row in inputs}
    scores = []
    for case in cases:
        actual = actual_by_id.get(case["id"], {})
        scores.append({"case_id": case["id"], **compare_case(case, actual, input_by_id[case["id"]])})
    by_id = {case["id"]: case for case in cases}
    paired = compare_router_v2_metamorphic(
        by_id["V03"]["expected"], actual_by_id.get("V03", {}),
        by_id["V06"]["expected"], actual_by_id.get("V06", {}),
    )
    legacy_report = json.loads(V2_REPORT.read_text(encoding="utf-8"))
    legacy_score = legacy_report["metrics"]["semantic_plan"]["matched"]
    matched = sum(bool(row["plan_match"]) for row in scores)
    parser_cases = first_raw["case_results"]
    missing_attempt = [row["case_id"] for row in parser_cases
                       if row["parser_attempts"] and not row["parser_attempts"][0]["technical_valid"]
                       and len(row["parser_attempts"]) == 1]
    repair_failed = [row["case_id"] for row in parser_cases
                     if row["parser_attempts"] and not row["parser_attempts"][0]["technical_valid"]
                     and len(row["parser_attempts"]) > 1
                     and not row["parser_attempts"][1]["technical_valid"]]
    repair_succeeded = [row["case_id"] for row in parser_cases
                        if row["parser_attempts"] and not row["parser_attempts"][0]["technical_valid"]
                        and len(row["parser_attempts"]) > 1
                        and row["parser_attempts"][1]["technical_valid"]]
    report = {
        "schema_version": "router_subscription_reconciled_baseline.v1",
        "source_run_id": first_raw["run_id"],
        "source_run_manifest_sha256": first_raw["run_manifest_sha256"],
        "source_gold_sha256": first_raw["gold_sha256"],
        "scorer_code_sha256": __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest(),
        "legacy_score": {"matched": legacy_score, "total": 20, "display": f"{legacy_score}/20"},
        "baseline_reconciled": {"matched": matched, "total": 20, "display": f"{matched}/20"},
        "metrics": {
            "first_wire_valid": legacy_report["metrics"]["first_draft_wire_validity"]["valid"],
            "first_technical_valid": legacy_report["metrics"]["first_draft_technical_validity"]["valid"],
            "first_technical_threshold_pass": legacy_report["metrics"]["first_draft_technical_validity"]["valid"] >= 19,
            "repair": {"needed": legacy_report["metrics"]["repair_success"]["needed"],
                       "succeeded_case_ids": repair_succeeded,
                       "missing_attempt_case_ids": missing_attempt,
                       "model_failure_case_ids": repair_failed},
            "strict_single_span_evidence_cases": sum(bool(row["strict_single_span_evidence_coverage"]) for row in scores),
            "union_evidence_cases": sum(bool(row["evidence_coverage"]) for row in scores),
            "critical_omissions": {"count": sum(len(row["critical_omissions"]) for row in scores),
                                   "by_case": {row["case_id"]: row["critical_omissions"] for row in scores if row["critical_omissions"]}},
            "paired_V03_V06": paired,
        },
        "case_scores": scores,
        "source_audit_map": audit_map,
        "operation_audit": OPERATION_AUDIT,
        "replay": {"runs": 2, "identical_actual_plans": True},
        "provenance": {"no_live_or_network_calls": True, "holdout_loaded": False,
                       "gold_or_receipts_modified": False, "actual_model_metadata": "requested_by_host_only"},
    }
    return report


def main() -> int:
    report = reconcile_baseline()
    out = HERE / "baseline_reconciled_v3_20261008.json"
    out.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    print(json.dumps({"path": str(out), "legacy": report["legacy_score"],
                      "baseline_reconciled": report["baseline_reconciled"], "metrics": report["metrics"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
