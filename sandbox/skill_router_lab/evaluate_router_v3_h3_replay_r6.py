"""H3 frozen-prompt, policy-aware technical replay and empty-run gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_router_v2_tuning import _parse_classifier
from freeze_router_v3_h3_r6 import RUN_DIR, RUN_ID, validate_frozen
from router_subscription_eval import ReplayAdapter, ReplayInputError, canonical_json_bytes, load_receipts
from router_subscription_lifecycle_h3_r6 import assemble_plan_h3, validate_raw_h3


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _state(receipt: dict[str, Any] | None) -> str:
    if receipt is None:
        return "missing_attempt"
    error = str(receipt.get("error") or "")
    if error.startswith("transport_failure:"):
        return "transport_failure"
    if error.startswith("model_failure:"):
        return "model_failure"
    return "completed"


def _user(row: dict[str, Any], complexity: str | None = None) -> str:
    payload = {"input": row}
    if complexity is not None:
        payload["classifier"] = {"complexity": complexity}
    return canonical_json_bytes(payload).decode("utf-8")


def _expected_envelopes(run_dir: Path, manifest: dict[str, Any], rows: list[dict[str, Any]],
                        keyed: dict[tuple[str, str, int], dict[str, Any]],
                        receipts: list[dict[str, Any]]) -> dict[tuple[str, str, int], dict[str, Any]]:
    prompts = {name: (run_dir / f"prompts/{name}.txt").read_text(encoding="utf-8")
               for name in ("classifier", "parser", "repair")}
    by_id = {row["case_id"]: row for row in rows}
    captures = {}
    for capture in _jsonl(run_dir / "request_envelopes.jsonl"):
        key = (capture.get("case_id"), capture.get("role"), capture.get("attempt"))
        if key in captures or capture.get("run_id") != RUN_ID:
            raise ReplayInputError(f"duplicate or wrong-run H3 envelope: {key}")
        encoded = capture.get("envelope_utf8")
        if not isinstance(encoded, str) or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != capture.get("envelope_sha256"):
            raise ReplayInputError(f"H3 envelope hash mismatch: {key}")
        captures[key] = (capture, json.loads(encoded))

    expected = {}
    for key, (capture, envelope) in captures.items():
        case_id, role, attempt = key
        row = by_id.get(case_id)
        if row is None:
            raise ReplayInputError(f"H3 envelope references unknown frozen case: {key}")
        if role == "classifier" and attempt == 1:
            prompt_name, user = "classifier", _user(row)
        elif role in {"simple_parser", "complex_parser"} and attempt in {1, 2}:
            classifier = keyed.get((case_id, "classifier", 1))
            if classifier is None or classifier.get("error"):
                raise ReplayInputError(f"H3 parser capture lacks classifier receipt: {key}")
            complexity, error = _parse_classifier(classifier["raw_final_text"])
            if error or role != f"{complexity}_parser":
                raise ReplayInputError(f"H3 parser role differs from observed classifier: {key}")
            if attempt == 1:
                prompt_name, user = "parser", _user(row, complexity)
            else:
                first = keyed.get((case_id, role, 1))
                if first is None or first.get("error"):
                    raise ReplayInputError(f"H3 repair capture lacks successful attempt 1: {key}")
                _, validation_error, _ = validate_raw_h3(first["raw_final_text"], row, manifest)
                if validation_error is None:
                    raise ReplayInputError(f"H3 repair followed a valid first draft: {key}")
                prompt_name = "repair"
                repair_payload = {"original_request": _user(row, complexity),
                                  "raw_response": first["raw_final_text"],
                                  "validation_errors": [validation_error]}
                user = canonical_json_bytes(repair_payload).decode("utf-8")
        else:
            raise ReplayInputError(f"unsupported H3 role/attempt: {key}")
        want = {"role": role, "attempt": attempt, "model": manifest["requested_models"][role],
                "effort": manifest["requested_effort"],
                "messages": [{"role": "system", "content": prompts[prompt_name]},
                             {"role": "user", "content": user}]}
        if envelope != want:
            raise ReplayInputError(f"H3 envelope is not reconstructed from frozen prompt/input: {key}")
        expected[key] = {"capture": capture, "envelope": envelope,
                         "prompt_sha256": hashlib.sha256(prompts[prompt_name].encode("utf-8")).hexdigest()}

    bindings = {}
    for binding in _jsonl(run_dir / "receipt_envelope_bindings.jsonl"):
        key = (binding.get("case_id"), binding.get("role"), binding.get("attempt"))
        if key in bindings or binding.get("run_id") != RUN_ID:
            raise ReplayInputError(f"duplicate or wrong-run H3 receipt binding: {key}")
        if set(binding) != {"run_id", "case_id", "role", "attempt", "envelope_sha256", "prompt_sha256"}:
            raise ReplayInputError(f"malformed H3 receipt binding: {key}")
        bindings[key] = binding

    receipt_keys = set(keyed)
    if set(bindings) != receipt_keys:
        raise ReplayInputError("H3 receipt bindings do not exactly cover receipts")
    for key, receipt in keyed.items():
        reconstructed = expected.get(key)
        binding = bindings[key]
        if reconstructed is None:
            raise ReplayInputError(f"H3 receipt has no pre-dispatch envelope: {key}")
        envelope_hash = reconstructed["capture"]["envelope_sha256"]
        prompt_hash = reconstructed["prompt_sha256"]
        if binding["envelope_sha256"] != envelope_hash or binding["prompt_sha256"] != prompt_hash:
            raise ReplayInputError(f"H3 receipt binding differs from reconstructed envelope: {key}")
        if receipt.get("run_id") != RUN_ID or receipt.get("prompt_sha256") != prompt_hash:
            raise ReplayInputError(f"H3 receipt provenance/prompt hash mismatch: {key}")
        if receipt.get("input_sha256") != hashlib.sha256(canonical_json_bytes(by_id[key[0]])).hexdigest():
            raise ReplayInputError(f"H3 receipt input hash mismatch: {key}")
    return expected


def replay_h3(run_dir: Path = RUN_DIR) -> dict[str, Any]:
    run_dir = Path(run_dir)
    frozen = validate_frozen(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    receipts = load_receipts(run_dir / "receipts.jsonl") if (run_dir / "receipts.jsonl").exists() else []
    keyed = {}
    for receipt in receipts:
        key = (receipt["case_id"], receipt["role"], receipt["attempt"])
        if key in keyed:
            raise ReplayInputError(f"duplicate H3 immutable receipt: {key}")
        if receipt.get("requested_model") != manifest["requested_models"].get(key[1]) or receipt.get("requested_effort") != manifest["requested_effort"]:
            raise ReplayInputError(f"H3 requested model/effort differs from manifest: {key}")
        keyed[key] = receipt
    envelopes = _expected_envelopes(run_dir, manifest, rows, keyed, receipts)
    input_hashes = {row["case_id"]: hashlib.sha256(canonical_json_bytes(row)).hexdigest() for row in rows}
    for key, receipt in keyed.items():
        prompt_hash = envelopes[key]["prompt_sha256"]
        adapter = ReplayAdapter([receipt], run_id=RUN_ID, input_hashes={key[0]: input_hashes[key[0]]},
                                prompt_hashes={key[1]: prompt_hash}, schema_hash=manifest["files_sha256"]["schema.json"],
                                expected_models=manifest["requested_models"], expected_effort=manifest["requested_effort"])
        if not receipt.get("error"):
            adapter.get(key[0], key[1], key[2], input_sha256=receipt["input_sha256"],
                        prompt_sha256=prompt_hash, schema_sha256=manifest["files_sha256"]["schema.json"])
    results = []
    classifier_count = parser_count = first_wire = first_technical = final_technical = repair_ok = 0
    for row in rows:
        case_id = row["case_id"]
        classifier = keyed.get((case_id, "classifier", 1))
        if classifier is None or classifier.get("error"):
            state = "missing_attempt" if classifier is None else _state(classifier)
            results.append({"case_id": case_id, "classification": state, "predicted_complexity": None, "parser_role": None,
                            "first_wire_valid": None, "first_technical_valid": None,
                            "final_technical_valid": None, "final_state": state,
                            "final_draft": None, "actual_plan": None})
            continue
        classifier_count += 1
        complexity, class_error = _parse_classifier(classifier["raw_final_text"])
        if class_error:
            results.append({"case_id": case_id, "classification": "model_failure", "predicted_complexity": None, "parser_role": None,
                            "first_wire_valid": None, "first_technical_valid": None,
                            "final_technical_valid": None, "final_state": "model_failure",
                            "final_draft": None, "actual_plan": None})
            continue
        role = f"{complexity}_parser"
        parser = keyed.get((case_id, role, 1))
        if parser is None or parser.get("error"):
            state = "missing_attempt" if parser is None else _state(parser)
            results.append({"case_id": case_id, "classification": "completed", "predicted_complexity": complexity, "parser_role": role,
                            "first_wire_valid": None, "first_technical_valid": None,
                            "final_technical_valid": None, "final_state": state,
                            "final_draft": None, "actual_plan": None})
            continue
        parser_count += 1
        first_draft, error, wire = validate_raw_h3(parser["raw_final_text"], row, manifest)
        first_wire += int(wire)
        first_valid = error is None
        first_technical += int(first_valid)
        repair = keyed.get((case_id, role, 2))
        if first_valid and repair is not None:
            raise ReplayInputError(f"H3 repair exists after a valid first draft: {case_id}")
        repair_state = "not_needed"
        final_valid = first_valid
        final_draft = first_draft if first_valid else None
        final_state = "completed" if first_valid else "model_failure"
        if not first_valid:
            if repair is None:
                repair_state = final_state = "missing_attempt"
            elif repair.get("error"):
                repair_state = final_state = _state(repair)
            else:
                repaired_draft, repair_error, _ = validate_raw_h3(repair["raw_final_text"], row, manifest)
                final_valid = repair_error is None
                final_draft = repaired_draft if final_valid else None
                repair_state = "succeeded" if final_valid else "failed_validation"
                final_state = "completed" if final_valid else "model_failure"
        final_technical += int(final_valid)
        repair_ok += int(repair_state == "succeeded")
        actual_plan = assemble_plan_h3(row, complexity, final_draft) if final_draft is not None else None
        results.append({"case_id": case_id, "classification": "completed", "predicted_complexity": complexity, "parser_role": role,
                        "first_wire_valid": bool(wire), "first_technical_valid": first_valid,
                        "final_technical_valid": final_valid, "repair_state": repair_state,
                        "final_state": final_state, "final_draft": final_draft, "actual_plan": actual_plan})

    denominator = 20
    complete = classifier_count == denominator and parser_count == denominator and all(
        row["final_state"] in {"completed", "transport_failure", "model_failure"} for row in results)
    missing = sorted(row["case_id"] for row in results if row["final_state"] == "missing_attempt")
    transport = sorted(row["case_id"] for row in results if row["final_state"] == "transport_failure")
    model = sorted(row["case_id"] for row in results if row["final_state"] == "model_failure")
    return {
        "schema_version": "router_v3_h3_technical_replay.v1", "run_id": RUN_ID,
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "validation_policy": manifest["validation_policy"], "frozen_validation": frozen["status"],
        "receipt_count": len(receipts), "expected_case_denominator": denominator,
        "classifier_coverage": {"received": classifier_count, "expected": denominator},
        "predicted_parser_coverage": {"received": parser_count, "expected": denominator},
        "coverage_complete": classifier_count == denominator and parser_count == denominator,
        "complete": complete,
        "first_wire_valid": first_wire, "first_wire_denominator": denominator,
        "first_technical_valid": first_technical, "first_technical_denominator": denominator,
        "first_technical_pass": first_technical >= 19,
        "final_technical_valid": final_technical, "final_technical_denominator": denominator,
        "final_technical_pass": final_technical == denominator,
        "overall_gate_pass": complete and first_technical >= 19 and final_technical == denominator,
        "repair_succeeded": repair_ok, "missing_attempt_case_ids": missing,
        "transport_failure_case_ids": transport, "model_failure_case_ids": model,
        "case_results": results,
        "provenance": {"network_used": False, "gold_loaded": False, "holdout_loaded": False},
    }


if __name__ == "__main__":
    print(json.dumps(replay_h3(), ensure_ascii=False, indent=2))
