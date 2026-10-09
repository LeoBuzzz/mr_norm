"""Immutable file-only receipt/replay helpers for the subscription experiment.

No transport, worker spawn, or network fallback is implemented here.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


RECEIPT_FIELDS = {
    "run_id", "case_id", "role", "attempt", "input_sha256", "prompt_sha256",
    "schema_sha256", "agent_id", "requested_model", "requested_effort",
    "actual_model", "model_verification", "started_at", "finished_at",
    "raw_final_text", "raw_sha256", "event_id", "finish", "error",
}


class ReplayInputError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def freeze_files(run_dir: str | Path, files: dict[str, bytes | str]) -> dict[str, str]:
    """Create frozen files once. Existing identical files are accepted; mutation is rejected."""
    root = Path(run_dir)
    hashes = {}
    for relative, content in files.items():
        path = root / relative
        payload = content.encode("utf-8") if isinstance(content, str) else content
        if path.exists():
            existing = path.read_bytes()
            if existing != payload:
                raise ReplayInputError(f"refusing to mutate frozen file: {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        hashes[relative] = sha256_bytes(payload)
    return hashes


def validate_receipt(receipt: Any, *, expected_model: str | None = None, expected_effort: str | None = None) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise ReplayInputError("receipt fields do not match frozen protocol")
    for key in ("input_sha256", "prompt_sha256", "schema_sha256", "raw_sha256"):
        value = receipt.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ReplayInputError(f"invalid receipt hash: {key}")
    raw = receipt.get("raw_final_text")
    if not isinstance(raw, str) or sha256_bytes(raw.encode("utf-8")) != receipt["raw_sha256"]:
        raise ReplayInputError("raw receipt hash mismatch")
    if receipt.get("model_verification") not in {"host_verified", "requested_by_host_only", "unknown"}:
        raise ReplayInputError("invalid model verification state")
    if receipt["model_verification"] == "host_verified" and not receipt.get("actual_model"):
        raise ReplayInputError("host_verified receipt must name the observed model")
    if receipt["model_verification"] == "requested_by_host_only" and receipt.get("actual_model") is not None:
        raise ReplayInputError("requested_by_host_only receipt must not claim observed model metadata")
    if not isinstance(receipt.get("attempt"), int) or receipt["attempt"] < 1:
        raise ReplayInputError("attempt must be a positive integer")
    if receipt.get("role") not in {"classifier", "simple_parser", "complex_parser"}:
        raise ReplayInputError("unknown receipt role")
    for key in ("run_id", "case_id", "agent_id", "requested_model", "requested_effort", "started_at", "finished_at"):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise ReplayInputError(f"receipt {key} must be nonempty text")
    if expected_model is not None and receipt["requested_model"] != expected_model:
        raise ReplayInputError("receipt requested_model differs from prescribed model")
    if expected_effort is not None and receipt["requested_effort"] != expected_effort:
        raise ReplayInputError("receipt requested_effort differs from prescribed effort")
    return receipt


def load_receipts(path: str | Path) -> list[dict[str, Any]]:
    receipts = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            receipts.append(validate_receipt(json.loads(line)))
        except Exception as exc:
            raise ReplayInputError(f"invalid receipt at line {line_no}: {exc}") from exc
    return receipts


def append_receipt(path: str | Path, receipt: dict[str, Any]) -> None:
    """Append an immutable raw receipt; refuse duplicate role/attempt keys."""
    row = validate_receipt(receipt)
    target = Path(path)
    existing = load_receipts(target) if target.exists() else []
    key = (row["run_id"], row["case_id"], row["role"], row["attempt"])
    if any((item["run_id"], item["case_id"], item["role"], item["attempt"]) == key for item in existing):
        raise ReplayInputError(f"duplicate immutable receipt: {key}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("ab") as stream:
        stream.write(canonical_json_bytes(row) + b"\n")


class ReplayAdapter:
    """Resolve exact pre-recorded role outputs only; never calls a fallback transport."""
    def __init__(self, receipts: Iterable[dict[str, Any]], *, run_id: str, input_hashes: dict[str, str], prompt_hashes: dict[str, str], schema_hash: str, expected_models: dict[str, str] | None = None, expected_effort: str | None = None):
        self._index: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._hashes = (input_hashes, prompt_hashes, schema_hash)
        for value in receipts:
            role = value.get("role") if isinstance(value, dict) else None
            receipt = validate_receipt(value, expected_model=(expected_models or {}).get(role), expected_effort=expected_effort)
            key = (receipt["case_id"], receipt["role"], receipt["attempt"])
            if receipt["run_id"] != run_id:
                continue
            if key in self._index:
                raise ReplayInputError(f"duplicate replay receipt: {key}")
            expected_input = input_hashes.get(receipt["case_id"])
            expected_prompt = prompt_hashes.get(receipt["role"])
            if receipt["input_sha256"] != expected_input or receipt["prompt_sha256"] != expected_prompt or receipt["schema_sha256"] != schema_hash:
                raise ReplayInputError(f"receipt manifest hash mismatch: {key}")
            self._index[key] = receipt

    def get(self, case_id: str, role: str, attempt: int = 1, *, input_sha256: str | None = None, prompt_sha256: str | None = None, schema_sha256: str | None = None) -> str:
        key = (case_id, role, attempt)
        receipt = self._index.get(key)
        if receipt is None:
            raise ReplayInputError(f"missing replay receipt: {key}")
        if input_sha256 is not None and receipt["input_sha256"] != input_sha256:
            raise ReplayInputError(f"input hash mismatch: {key}")
        if prompt_sha256 is not None and receipt["prompt_sha256"] != prompt_sha256:
            raise ReplayInputError(f"prompt hash mismatch: {key}")
        if schema_sha256 is not None and receipt["schema_sha256"] != schema_sha256:
            raise ReplayInputError(f"schema hash mismatch: {key}")
        if receipt.get("error") or receipt.get("finish") not in {"completed", "stop"}:
            raise ReplayInputError(f"receipt did not complete successfully: {key}")
        return receipt["raw_final_text"]


def prepare_tuning_v2(case_rows: Iterable[dict[str, Any]], *, source_name: str) -> dict[str, Any]:
    """Migrate supplied tuning rows only. Caller must pass already-approved tuning data."""
    cases = []
    for row in case_rows:
        if row.get("split") != "tuning":
            continue
        cases.append({"id": row["id"], "source_case_id": row.get("source_case_id"), "group": row["group"],
                      "question": row["question"], "context": row.get("context", {}), "candidates": row.get("candidates", {}),
                      "pending_clarification": row.get("pending_clarification"), "expected": row["expected"]})
    if not cases:
        raise ValueError("no tuning rows supplied")
    if len({row["id"] for row in cases}) != len(cases):
        raise ValueError("duplicate tuning case IDs")
    return {"schema_version": "router_plan_cases.tuning_v2", "source": source_name, "cases": cases}


def build_source_manifest(case: dict[str, Any]) -> dict[str, Any]:
    from router_draft import build_source_manifest as build
    return build(case["question"], case.get("context", {}), case.get("pending_clarification"))


def _normalize_semantic(value: str) -> str:
    return " ".join(str(value).casefold().replace("ё", "е").split())


def _contains_allowed(text: str, variants: list[str]) -> bool:
    haystack = _normalize_semantic(text)
    for value in variants:
        needle = _normalize_semantic(value)
        if needle and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


def compare_router_v2_case(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Score assembled v2 meaning against tuning gold without an LLM judge."""
    op_skill = {"text": "exact_point", "search": "corpus_point_search", "all": "find_all", "define": None, "compare": None, "conclude": None, "summarize": None, "unknown": None}
    expected_tasks = expected.get("tasks", [])
    actual_tasks = actual.get("tasks", [])
    task_count = len(expected_tasks) == len(actual_tasks)
    checks_by_pair: list[dict[int, dict[str, bool]]] = []
    choices: list[list[int]] = []
    for wanted in expected_tasks:
        pair_checks = {}
        for index, task in enumerate(actual_tasks):
            skill_ok = task.get("skill") == op_skill.get(wanted.get("operation"))
            doc_ok = task.get("doc_id") == wanted.get("doc_id")
            point_ok = wanted.get("point_ref") is None or task.get("point_ref") == wanted.get("point_ref")
            coverage_ok = wanted.get("coverage") is None or task.get("coverage") == wanted.get("coverage")
            content = str(task.get("query") or "")
            concept_content = " ".join(str(v) for row in task.get("concepts", []) for v in [row.get("surface", ""), *row.get("variants", [])])
            constraints = task.get("constraints", [])
            semantic_ok = True
            query_ok = True
            concept_ok = True
            intent_ok = True
            condition_ok = True
            for element in wanted.get("required_semantic_elements", []):
                variants = element.get("variants") or [element.get("label", "")]
                kind = element.get("kind")
                if kind == "point":
                    semantic_ok = semantic_ok and any(_normalize_semantic(task.get("point_ref") or "") == _normalize_semantic(v) for v in variants)
                elif kind == "intent":
                    intent_ok = intent_ok and task.get("skill") == op_skill.get(wanted.get("operation"))
                elif kind == "topic":
                    matched = _contains_allowed(content, variants)
                    semantic_ok = semantic_ok and matched
                    query_ok = query_ok and matched
                elif kind in {"concept", "object"} or element.get("require_concept"):
                    concept_ok = concept_ok and _contains_allowed(concept_content, variants)
                elif kind in {"condition", "quantity", "exclusion"}:
                    expected_constraint = next((row for row in wanted.get("constraints", [])
                                                if row.get("kind") == kind and _contains_allowed(row.get("text", ""), variants)), None)
                    matched = any(row.get("kind") == kind
                                  and (expected_constraint is None or row.get("polarity") == expected_constraint.get("polarity"))
                                  and _contains_allowed(row.get("text", ""), variants)
                                  for row in constraints)
                    semantic_ok = semantic_ok and matched
                    condition_ok = condition_ok and matched
                else:
                    matched = _contains_allowed(content, variants)
                    semantic_ok = semantic_ok and matched
                    query_ok = query_ok and matched
            for condition in wanted.get("constraints", []):
                variants = condition.get("variants") or [condition.get("text", "")]
                found = any(row.get("kind") == condition.get("kind") and row.get("polarity") == condition.get("polarity") and _contains_allowed(row.get("text", ""), variants) for row in constraints)
                condition_ok = condition_ok and found
            # Exact DAG edges are checked after a one-to-one semantic assignment.
            dep_ok = True
            pair_checks[index] = {"skill": skill_ok, "intent": intent_ok, "doc_id": doc_ok, "point_ref": point_ok, "coverage": coverage_ok,
                                  "query_semantics": query_ok, "semantic_elements": semantic_ok,
                                  "concepts": concept_ok, "conditions": condition_ok, "dependencies": dep_ok}
        checks_by_pair.append(pair_checks)
        choices.append([index for index, values in pair_checks.items() if all(values.values())])
    order = sorted(range(len(expected_tasks)), key=lambda index: len(choices[index]))
    assignment: dict[int, int] = {}
    def match(position: int, used: set[int]) -> bool:
        if position == len(order):
            return True
        expected_index = order[position]
        for actual_index in choices[expected_index]:
            if actual_index not in used:
                assignment[expected_index] = actual_index
                if match(position + 1, used | {actual_index}):
                    return True
        assignment.pop(expected_index, None)
        return False
    task_match = task_count and match(0, set())
    dependency_edges_match = task_match
    if task_match:
        def task_index(value: Any, *, actual: bool = False) -> int | None:
            text = str(value)
            match_id = re.fullmatch(r"t?(\d+)", text)
            return int(match_id.group(1)) - 1 if match_id else None
        expected_edges = set()
        actual_edges = set()
        inverse = {actual_index: expected_index for expected_index, actual_index in assignment.items()}
        for expected_index, wanted in enumerate(expected_tasks):
            for dependency in wanted.get("depends_on", []):
                dependency_index = task_index(dependency)
                if dependency_index is None:
                    dependency_edges_match = False
                else:
                    expected_edges.add((expected_index, dependency_index))
        for actual_index, task in enumerate(actual_tasks):
            dependent_expected_index = inverse.get(actual_index)
            for dependency in task.get("depends_on", []):
                dependency_actual_index = task_index(dependency, actual=True)
                dependency_expected_index = inverse.get(dependency_actual_index) if dependency_actual_index is not None else None
                if dependent_expected_index is None or dependency_expected_index is None:
                    dependency_edges_match = False
                else:
                    actual_edges.add((dependent_expected_index, dependency_expected_index))
        dependency_edges_match = dependency_edges_match and expected_edges == actual_edges
        task_match = task_match and dependency_edges_match
    task_checks = []
    for expected_index, pair_checks in enumerate(checks_by_pair):
        actual_index = assignment.get(expected_index)
        if actual_index is None:
            actual_index = min(pair_checks, key=lambda idx: sum(pair_checks[idx].values()), default=None)
        task_checks.append(pair_checks.get(actual_index, {"skill": False, "doc_id": False, "point_ref": False, "coverage": False,
                            "query_semantics": False, "concepts": False, "conditions": False, "dependencies": False}))
    field = (actual.get("clarification") or {}).get("field")
    clarification_match = field == expected.get("clarification_field") if expected.get("clarification_field") else field is None
    subject = (actual.get("clarification") or {}).get("question", "")
    subject_spec = expected.get("clarification_subject") or {}
    subject_match = not subject_spec or _contains_allowed(subject, subject_spec.get("variants") or [subject_spec.get("label", "")])
    option_spec = expected.get("clarification_options", [])
    actual_options = (actual.get("clarification") or {}).get("options", [])
    clarification_options_match = True
    if expected.get("clarification_field"):
        if option_spec:
            expected_ids = [str(row["id"]) for row in option_spec if isinstance(row, dict) and row.get("id")]
            actual_ids = [str(row.get("id")) for row in actual_options if isinstance(row, dict) and row.get("id")]
            if expected_ids:
                # Canonical IDs define the complete option set. Labels are checked
                # only after exact ID matching, so aliases cannot mask substitutions.
                by_id = {str(row["id"]): row for row in option_spec if isinstance(row, dict) and row.get("id")}
                clarification_options_match = (
                    len(expected_ids) == len(option_spec)
                    and
                    len(actual_options) == len(expected_ids)
                    and len(set(expected_ids)) == len(expected_ids)
                    and len(actual_ids) == len(actual_options)
                    and len(set(actual_ids)) == len(actual_ids)
                    and set(actual_ids) == set(expected_ids)
                    and all(_normalize_semantic(str(actual.get("label", ""))) in {
                        _normalize_semantic(str(label)) for label in by_id[str(actual["id"])].get("labels", []) if str(label).strip()
                    } for actual in actual_options)
                )
            else:
                # Legacy tuning rows without canonical term IDs use a deterministic
                # one-to-one exact normalized-label match; duplicates/extras fail.
                labels = [str(label) for row in option_spec if isinstance(row, dict) for label in row.get("labels", []) if str(label).strip()]
                actual_labels = [str(row.get("label", "")) for row in actual_options if isinstance(row, dict)]
                expected_norm = [_normalize_semantic(label) for label in labels]
                actual_norm = [_normalize_semantic(label) for label in actual_labels]
                clarification_options_match = (len(actual_options) == len(option_spec) == len(labels)
                                               and len(set(expected_norm)) == len(expected_norm)
                                               and len(actual_norm) == len(actual_options)
                                               and len(set(actual_norm)) == len(actual_norm)
                                               and set(actual_norm) == set(expected_norm))
        else:
            clarification_options_match = not actual_options
    evidence_coverage = task_match
    source_manifest = (actual.get("trace") or {}).get("source_manifest") or {}
    from router_draft import extract_source_ref
    for expected_index, wanted in enumerate(expected_tasks):
        actual_index = assignment.get(expected_index)
        if actual_index is None:
            evidence_coverage = False
            continue
        evidence_rows = actual_tasks[actual_index].get("evidence", [])
        for ref in wanted.get("source_refs", []):
            try:
                required = extract_source_ref(ref, source_manifest)
            except Exception:
                evidence_coverage = False
                continue
            if not any(row.get("turn_id") == required["turn_id"] and required["quote"] in str(row.get("quote", "")) for row in evidence_rows):
                evidence_coverage = False
    complexity_match = actual.get("complexity") == expected.get("complexity")
    status_match = actual.get("status") == expected.get("status")
    dialog_match = expected.get("dialog_action") is None or actual.get("dialog_action") == expected.get("dialog_action")
    substantive = " ".join(str(task.get(key) or "") for task in actual_tasks for key in ("query", "intent_text", "document_mention", "doc_id", "point_ref"))
    substantive += " " + " ".join(str(value) for task in actual_tasks for concept in task.get("concepts", [])
                                   for value in [concept.get("surface", ""), *concept.get("variants", [])])
    substantive += " " + " ".join(str(item.get("text") or "") for task in actual_tasks for item in task.get("constraints", []))
    forbidden_match = all(not _contains_allowed(substantive, [str(value).split("→")[-1].strip()]) for value in expected.get("forbidden_substitutions", []))
    plan_match = task_match and evidence_coverage and complexity_match and status_match and clarification_match and subject_match and clarification_options_match and dialog_match and forbidden_match
    return {"task_count_match": task_count, "task_checks": task_checks, "task_semantics_match": task_match,
            "classification_match": complexity_match, "status_match": status_match,
            "clarification_match": clarification_match, "clarification_subject_match": subject_match,
            "clarification_options_match": clarification_options_match, "evidence_coverage": evidence_coverage,
            "dependency_edges_match": dependency_edges_match,
            "dialog_action_match": dialog_match, "forbidden_substitutions_match": forbidden_match, "plan_match": plan_match}


def compare_router_v2_metamorphic(expected_a: dict[str, Any], actual_a: dict[str, Any], expected_b: dict[str, Any], actual_b: dict[str, Any]) -> dict[str, bool]:
    """Cross-case paraphrase guard; V03/V06 are one tuning pair, not independent cases."""
    wanted_a, wanted_b = expected_a.get("tasks", []), expected_b.get("tasks", [])
    tasks_a, tasks_b = actual_a.get("tasks", []), actual_b.get("tasks", [])
    if not (len(wanted_a) == len(wanted_b) == len(tasks_a) == len(tasks_b)):
        return {"task_count_match": False, "skill_scope_match": False, "concepts_compatible": False, "passed": False}
    skill_scope = all(left.get("skill") == right.get("skill") and left.get("scope") == right.get("scope") for left, right in zip(tasks_a, tasks_b))
    concept_checks = []
    for wa, aa, wb, ab in zip(wanted_a, tasks_a, wanted_b, tasks_b):
        variants_a = [v for item in wa.get("required_semantic_elements", []) if item.get("kind") == "object" for v in item.get("variants", [])]
        variants_b = [v for item in wb.get("required_semantic_elements", []) if item.get("kind") == "object" for v in item.get("variants", [])]
        shared = set(map(_normalize_semantic, variants_a)) & set(map(_normalize_semantic, variants_b))
        concepts_a = [value for concept in aa.get("concepts", []) for value in [concept.get("surface", ""), *concept.get("variants", [])]]
        concepts_b = [value for concept in ab.get("concepts", []) for value in [concept.get("surface", ""), *concept.get("variants", [])]]
        concept_checks.append(bool(shared) and any(_contains_allowed(" ".join(concepts_a), [value]) for value in shared)
                              and any(_contains_allowed(" ".join(concepts_b), [value]) for value in shared))
    concept_ok = bool(concept_checks) and all(concept_checks)
    return {"task_count_match": True, "skill_scope_match": skill_scope, "concepts_compatible": concept_ok, "passed": skill_scope and concept_ok}
