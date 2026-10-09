"""Offline technical gate for immutable v3 file-subscription receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_router_v2_tuning import _parse_classifier, _validate_raw
from freeze_router_v3_run import RUN_DIR, validate_frozen
from router_subscription_eval import ReplayAdapter, ReplayInputError, canonical_json_bytes, load_receipts


HERE = Path(__file__).resolve().parent


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _failure_state(receipt: dict[str, Any] | None) -> str:
    if receipt is None:
        return "missing_attempt"
    error = str(receipt.get("error") or "")
    if error.startswith("transport_failure:"):
        return "transport_failure"
    if error.startswith("model_failure:"):
        return "model_failure"
    return "completed"


def _request_user(row: dict[str, Any], complexity: str | None = None) -> str:
    payload = {"input": row}
    if complexity is not None:
        payload["classifier"] = {"complexity": complexity}
    return canonical_json_bytes(payload).decode("utf-8")


def _validate_envelope_bindings(run_dir: Path, manifest: dict, rows: list[dict],
                                keyed: dict[tuple[str, str, int], dict],
                                receipts: list[dict]) -> dict[tuple[str, str, int], dict]:
    row_by_id = {row["case_id"]: row for row in rows}
    prompt_bytes = {
        "classifier": (run_dir / "prompts/classifier.txt").read_bytes(),
        "parser": (run_dir / "prompts/parser.txt").read_bytes(),
        "repair": (run_dir / "prompts/repair.txt").read_bytes(),
    }
    for relative, payload in (("prompts/classifier.txt", prompt_bytes["classifier"]),
                              ("prompts/parser.txt", prompt_bytes["parser"]),
                              ("prompts/repair.txt", prompt_bytes["repair"])):
        if hashlib.sha256(payload).hexdigest() != manifest["files_sha256"][relative]:
            raise ReplayInputError(f"frozen prompt hash mismatch: {relative}")
    envelopes = {}
    for row in _jsonl(run_dir / "request_envelopes.jsonl"):
        key = (row.get("case_id"), row.get("role"), row.get("attempt"))
        if key in envelopes:
            raise ReplayInputError(f"duplicate pre-dispatch envelope: {key}")
        encoded = row.get("envelope_utf8")
        if not isinstance(encoded, str) or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row.get("envelope_sha256"):
            raise ReplayInputError(f"invalid envelope hash: {key}")
        if row.get("run_id") != manifest["run_id"]:
            raise ReplayInputError(f"envelope run id mismatch: {key}")
        envelope = json.loads(encoded)
        if (envelope.get("role"), envelope.get("attempt"), envelope.get("model"), envelope.get("effort")) != (
                row.get("role"), row.get("attempt"), manifest["requested_models"].get(row.get("role")), manifest["requested_effort"]):
            raise ReplayInputError(f"envelope request parameters differ from manifest: {key}")
        row["envelope"] = envelope
        envelopes[key] = row
    bindings = {}
    for row in _jsonl(run_dir / "receipt_envelope_bindings.jsonl"):
        key = (row.get("case_id"), row.get("role"), row.get("attempt"))
        if key in bindings:
            raise ReplayInputError(f"duplicate receipt/envelope binding: {key}")
        if row.get("run_id") != manifest["run_id"]:
            raise ReplayInputError(f"binding run id mismatch: {key}")
        if set(row) != {"run_id", "case_id", "role", "attempt", "envelope_sha256", "prompt_sha256"}:
            raise ReplayInputError(f"binding fields malformed: {key}")
        bindings[key] = row
    # Verify every capture, including a pre-dispatch envelope whose receipt is
    # missing after interruption, against exact inputs and frozen prompt bytes.
    for key, capture in envelopes.items():
        case_id, role, attempt = key
        source_row = row_by_id.get(case_id)
        if source_row is None:
            raise ReplayInputError(f"envelope references a case outside frozen inputs: {key}")
        if role == "classifier" and attempt == 1:
            prompt_kind, system, user = "classifier", prompt_bytes["classifier"].decode("utf-8"), _request_user(source_row)
        elif role in {"simple_parser", "complex_parser"} and attempt in {1, 2}:
            classifier = keyed.get((case_id, "classifier", 1))
            if classifier is None or classifier.get("error"):
                raise ReplayInputError(f"parser envelope has no successful classifier receipt: {key}")
            complexity, class_error = _parse_classifier(classifier["raw_final_text"])
            if class_error or role != f"{complexity}_parser":
                raise ReplayInputError(f"parser envelope role does not match classifier result: {key}")
            if attempt == 1:
                prompt_kind, system, user = "parser", prompt_bytes["parser"].decode("utf-8"), _request_user(source_row, complexity)
            else:
                first = keyed.get((case_id, role, 1))
                if first is None or first.get("error"):
                    raise ReplayInputError(f"repair envelope has no successful first parser receipt: {key}")
                _, validation_error, _ = _validate_raw(first["raw_final_text"], source_row)
                if validation_error is None:
                    raise ReplayInputError(f"repair envelope follows a technically valid first draft: {key}")
                parser_user = _request_user(source_row, complexity)
                repair_payload = {"original_request": parser_user, "raw_response": first["raw_final_text"],
                                  "validation_errors": [validation_error]}
                prompt_kind, system = "repair", prompt_bytes["repair"].decode("utf-8")
                user = canonical_json_bytes(repair_payload).decode("utf-8")
        else:
            raise ReplayInputError(f"unsupported envelope role/attempt: {key}")
        expected = {"role": role, "attempt": attempt, "model": manifest["requested_models"][role],
                    "effort": manifest["requested_effort"],
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if capture["envelope"] != expected:
            raise ReplayInputError(f"envelope content differs from exact frozen lifecycle payload: {key}")
        if key in bindings:
            prompt_hash = hashlib.sha256(prompt_bytes[prompt_kind]).hexdigest()
            if bindings[key]["prompt_sha256"] != prompt_hash:
                raise ReplayInputError(f"binding prompt hash differs from frozen prompt: {key}")
    for receipt in receipts:
        key = (receipt["case_id"], receipt["role"], receipt["attempt"])
        envelope = envelopes.get(key)
        binding = bindings.get(key)
        if not envelope or not binding or binding.get("envelope_sha256") != envelope["envelope_sha256"]:
            raise ReplayInputError(f"receipt lacks exact pre-dispatch envelope binding: {key}")
        system = envelope["envelope"]["messages"][0]["content"]
        actual_prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        if receipt["prompt_sha256"] != actual_prompt_hash or binding.get("prompt_sha256") != actual_prompt_hash:
            raise ReplayInputError(f"receipt prompt hash does not match envelope: {key}")
        if receipt["run_id"] != manifest["run_id"]:
            raise ReplayInputError(f"receipt run id mismatch: {key}")
    if set(bindings) != {(r["case_id"], r["role"], r["attempt"]) for r in receipts}:
        raise ReplayInputError("receipt binding sidecar contains unreceipted entries")
    return envelopes


def replay_v3(run_dir: Path = RUN_DIR) -> dict[str, Any]:
    run_dir = Path(run_dir)
    frozen = validate_frozen(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    inputs = {row["case_id"]: canonical_json_bytes(row) for row in rows}
    receipts = load_receipts(run_dir / "receipts.jsonl") if (run_dir / "receipts.jsonl").exists() else []
    keyed = {}
    for receipt in receipts:
        key = (receipt["case_id"], receipt["role"], receipt["attempt"])
        if key in keyed:
            raise ReplayInputError(f"duplicate immutable receipt: {key}")
        keyed[key] = receipt
    envelopes = _validate_envelope_bindings(run_dir, manifest, rows, keyed, receipts)
    # ReplayAdapter remains the receipt validator. Parser attempt 2 is grouped
    # separately because its frozen system prompt is the repair prompt.
    for key, receipt in keyed.items():
        if key[0] not in inputs or receipt["input_sha256"] != hashlib.sha256(inputs[key[0]]).hexdigest():
            raise ReplayInputError(f"receipt input hash differs from frozen model input: {key}")
        env = envelopes[key]["envelope"]
        system = env["messages"][0]["content"]
        prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        adapter = ReplayAdapter([receipt], run_id=manifest["run_id"], input_hashes={receipt["case_id"]: receipt["input_sha256"]},
                                prompt_hashes={receipt["role"]: prompt_hash}, schema_hash=manifest["files_sha256"]["schema.json"],
                                expected_models=manifest["requested_models"], expected_effort=manifest["requested_effort"])
        if not receipt.get("error"):
            adapter.get(receipt["case_id"], receipt["role"], receipt["attempt"], input_sha256=receipt["input_sha256"],
                        prompt_sha256=prompt_hash, schema_sha256=manifest["files_sha256"]["schema.json"])
    known_ids = set(inputs)
    if any(receipt["case_id"] not in known_ids for receipt in receipts):
        raise ReplayInputError("receipt references a case outside frozen inputs")
    results = []
    classifier_coverage = sum((case_id, "classifier", 1) in keyed for case_id in inputs)
    predicted_parser_coverage = 0
    for row in rows:
        case_id = row["case_id"]
        classifier = keyed.get((case_id, "classifier", 1))
        classifier_attempts = sorted(key[2] for key in keyed if key[0] == case_id and key[1] == "classifier")
        if classifier_attempts not in ([], [1]):
            raise ReplayInputError(f"classifier attempts must be empty or exactly attempt 1: {case_id}")
        status = _failure_state(classifier)
        if status != "completed":
            if any(key[0] == case_id and key[1] != "classifier" for key in keyed):
                raise ReplayInputError(f"parser receipt exists without a completed classifier: {case_id}")
            results.append({"case_id": case_id, "classification": status, "first_wire_valid": None,
                            "first_technical_valid": None, "final_technical_valid": None, "final_state": status})
            continue
        complexity, classifier_error = _parse_classifier(classifier["raw_final_text"])
        if classifier_error:
            if any(key[0] == case_id and key[1] != "classifier" for key in keyed):
                raise ReplayInputError(f"parser receipt exists after an invalid classifier: {case_id}")
            results.append({"case_id": case_id, "classification": "model_failure", "first_wire_valid": None,
                            "first_technical_valid": None, "final_technical_valid": None, "final_state": "model_failure"})
            continue
        role = f"{complexity}_parser"
        if any(key[0] == case_id and key[1] not in {"classifier", role} for key in keyed):
            raise ReplayInputError(f"receipt role does not match classifier result: {case_id}")
        parser_attempts = sorted(key[2] for key in keyed if key[0] == case_id and key[1] == role)
        if parser_attempts not in ([], [1], [1, 2]):
            raise ReplayInputError(f"parser attempts must be attempt 1 and optionally one repair at attempt 2: {case_id}")
        if (case_id, role, 1) in keyed:
            predicted_parser_coverage += 1
        first = keyed.get((case_id, role, 1))
        first_state = _failure_state(first)
        if first_state != "completed":
            results.append({"case_id": case_id, "classification": "completed", "parser_role": role,
                            "first_wire_valid": None, "first_technical_valid": None,
                            "final_technical_valid": None, "final_state": first_state})
            continue
        draft, error, wire_valid = _validate_raw(first["raw_final_text"], row)
        first_technical = error is None
        if first_technical and 2 in parser_attempts:
            raise ReplayInputError(f"unexpected repair after a technically valid first draft: {case_id}")
        final_technical = first_technical
        repair_state = "not_needed"
        if not first_technical:
            repair = keyed.get((case_id, role, 2))
            repair_state = _failure_state(repair)
            if repair_state == "completed":
                _, repair_error, _ = _validate_raw(repair["raw_final_text"], row)
                final_technical = repair_error is None
                repair_state = "succeeded" if final_technical else "model_failure"
            else:
                final_technical = False
        final_state = "completed" if final_technical else (repair_state if repair_state != "not_needed" else "model_failure")
        results.append({"case_id": case_id, "classification": "completed", "parser_role": role,
                        "first_wire_valid": bool(wire_valid), "first_technical_valid": first_technical,
                        "final_technical_valid": final_technical, "repair_state": repair_state,
                        "final_state": final_state})
    expected_denominator = len(rows)
    first_wire_valid = sum(row.get("first_wire_valid") is True for row in results)
    first_technical_valid = sum(row.get("first_technical_valid") is True for row in results)
    final_technical_valid = sum(row.get("final_technical_valid") is True for row in results)
    expected_coverage_complete = classifier_coverage == expected_denominator and predicted_parser_coverage == expected_denominator
    attempts_complete = not any(row["final_state"] == "missing_attempt" for row in results)
    summary = {
        "schema_version": "router_v3_offline_technical_replay.v1", "run_id": manifest["run_id"],
        "run_manifest_sha256": hashlib.sha256((run_dir / "manifest.json").read_bytes()).hexdigest(),
        "frozen_validation": frozen["status"], "receipt_count": len(receipts),
        "expected_case_denominator": expected_denominator,
        "classifier_coverage": {"received": classifier_coverage, "expected": expected_denominator},
        "predicted_parser_coverage": {"received": predicted_parser_coverage, "expected": expected_denominator},
        "coverage_complete": expected_coverage_complete,
        "complete": expected_coverage_complete and attempts_complete,
        "first_wire_valid": first_wire_valid, "first_wire_denominator": expected_denominator,
        "first_technical_valid": first_technical_valid, "first_technical_denominator": expected_denominator,
        "first_technical_pass": first_technical_valid >= 19,
        "final_technical_valid": final_technical_valid, "final_technical_denominator": expected_denominator,
        "final_technical_pass": final_technical_valid == expected_denominator,
        "overall_gate_pass": expected_coverage_complete and attempts_complete
                             and first_technical_valid >= 19 and final_technical_valid == expected_denominator,
        "repair_succeeded": sum(row.get("repair_state") == "succeeded" for row in results),
        "missing_attempt_case_ids": sorted(row["case_id"] for row in results if row["final_state"] == "missing_attempt"),
        "transport_failure_case_ids": sorted(row["case_id"] for row in results if row["final_state"] == "transport_failure"),
        "model_failure_case_ids": sorted(row["case_id"] for row in results if row["final_state"] == "model_failure"),
        "case_results": results,
        "provenance": {"network_used": False, "gold_loaded": False, "holdout_loaded": False,
                       "actual_model_metadata": "requested_by_host_only"},
    }
    return summary


def main() -> int:
    report = replay_v3()
    output = RUN_DIR / "offline_replay_report.json"
    output.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    print(json.dumps({key: report[key] for key in ("run_id", "receipt_count", "first_wire_valid", "first_technical_valid", "final_technical_valid", "missing_attempt_case_ids", "transport_failure_case_ids", "model_failure_case_ids")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
