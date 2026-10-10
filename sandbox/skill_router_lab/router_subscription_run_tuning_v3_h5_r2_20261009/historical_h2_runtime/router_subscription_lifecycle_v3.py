"""Injected local file lifecycle using the inherited immutable receipt schema."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from evaluate_router_v2_tuning import _parse_classifier, _validate_raw
from router_prompts_v3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_request_envelope_v3 import capture_envelope
from router_subscription_eval import ReplayInputError, append_receipt, canonical_json_bytes, load_receipts


class TransportFailure(Exception):
    """Injected dispatcher could not deliver the request."""


class ModelFailure(Exception):
    """Injected dispatcher reported a model/provider failure."""


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FileSubscriptionRunnerV3:
    """Run one case through an injected dispatcher; there is no default transport."""

    def __init__(self, run_dir: Path, dispatch: Callable[[dict[str, Any]], str | dict[str, Any]],
                 requested_models: dict[str, str] | None = None, effort: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.dispatch = dispatch
        self.manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        prescribed_models = self.manifest["requested_models"]
        prescribed_effort = self.manifest["requested_effort"]
        if requested_models is not None and requested_models != prescribed_models:
            raise ValueError("requested models differ from the frozen manifest")
        if effort is not None and effort != prescribed_effort:
            raise ValueError("requested effort differs from the frozen manifest")
        self.requested_models = prescribed_models
        self.effort = prescribed_effort
        self.envelope_path = self.run_dir / "request_envelopes.jsonl"
        self.binding_path = self.run_dir / "receipt_envelope_bindings.jsonl"
        self.receipt_path = self.run_dir / "receipts.jsonl"

    def _existing_key(self, case_id: str, role: str, attempt: int) -> bool:
        key = (case_id, role, attempt)
        receipts = load_receipts(self.receipt_path) if self.receipt_path.exists() else []
        return (any((row.get("case_id"), row.get("role"), row.get("attempt")) == key
                    for row in _load_jsonl(self.envelope_path))
                or any((row.get("case_id"), row.get("role"), row.get("attempt")) == key
                       for row in receipts))

    def _call(self, case: dict[str, Any], role: str, attempt: int, system: str,
              user_content: str) -> tuple[str | None, dict[str, Any]]:
        case_id = case["case_id"]
        if self._existing_key(case_id, role, attempt):
            raise ReplayInputError(f"refusing to resample existing role/attempt: {(case_id, role, attempt)}")
        envelope = {"role": role, "attempt": attempt, "model": self.requested_models[role],
                    "effort": self.effort,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}]}
        captured = capture_envelope(envelope)
        envelope_row = {"run_id": self.manifest["run_id"], "case_id": case_id, "role": role,
                        "attempt": attempt, "envelope_sha256": captured["envelope_sha256"],
                        "envelope_utf8": captured["envelope_utf8"], "capture_stage": "pre_dispatch"}
        # Durable capture precedes the only call to the injected dispatcher.
        _append_jsonl(self.envelope_path, envelope_row)
        started = datetime.now(timezone.utc).isoformat()
        raw = ""
        error = None
        finish = "completed"
        try:
            result = self.dispatch(envelope)
            if isinstance(result, dict) and result.get("failure"):
                failure = str(result["failure"])
                is_transport = failure == "transport_failure"
                error = f"{'transport_failure' if is_transport else 'model_failure'}: {result.get('error') or failure}"
                finish = "error"
            elif isinstance(result, dict) and isinstance(result.get("raw_text"), str):
                raw = result["raw_text"]
            elif isinstance(result, str):
                raw = result
            else:
                error, finish = "model_failure: dispatch result missing raw_text", "error"
        except TransportFailure as exc:
            error, finish = f"transport_failure: {exc}", "error"
        except ModelFailure as exc:
            error, finish = f"model_failure: {exc}", "error"
        finished = datetime.now(timezone.utc).isoformat()
        prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        receipt = {
            "run_id": self.manifest["run_id"], "case_id": case_id, "role": role, "attempt": attempt,
            "input_sha256": hashlib.sha256(canonical_json_bytes(case)).hexdigest(),
            "prompt_sha256": prompt_hash, "schema_sha256": self.manifest["files_sha256"]["schema.json"],
            "agent_id": "injected-dispatch", "requested_model": envelope["model"],
            "requested_effort": envelope["effort"], "actual_model": None,
            "model_verification": "requested_by_host_only", "started_at": started, "finished_at": finished,
            "raw_final_text": raw, "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "event_id": None, "finish": finish, "error": error,
        }
        append_receipt(self.receipt_path, receipt)
        # Additive sidecar binds the immutable inherited receipt to its exact
        # pre-dispatch envelope without extending/mutating receipt fields.
        _append_jsonl(self.binding_path, {"run_id": self.manifest["run_id"], "case_id": case_id,
                                          "role": role, "attempt": attempt,
                                          "envelope_sha256": captured["envelope_sha256"],
                                          "prompt_sha256": prompt_hash})
        if error:
            return None, receipt
        return raw, receipt

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        user = canonical_json_bytes({"input": case}).decode("utf-8")
        classifier_raw, classifier_receipt = self._call(case, "classifier", 1, CLASSIFIER_V3_SYSTEM, user)
        result: dict[str, Any] = {"case_id": case["case_id"], "classifier": classifier_receipt, "parser_attempts": []}
        if classifier_raw is None:
            result["status"] = _failure_state(classifier_receipt)
            return result
        complexity, error = _parse_classifier(classifier_raw)
        if error:
            result.update(status="model_failure", classifier_error=error)
            return result
        role = f"{complexity}_parser"
        parser_user = canonical_json_bytes({"input": case, "classifier": {"complexity": complexity}}).decode("utf-8")
        raw, receipt = self._call(case, role, 1, PARSER_V3_SYSTEM, parser_user)
        result["parser_attempts"].append(receipt)
        if raw is None:
            result["status"] = _failure_state(receipt)
            return result
        draft, validation_error, _ = _validate_raw(raw, case)
        if validation_error is None:
            result.update(status="valid", semantic_draft=draft)
            return result
        repair_user = canonical_json_bytes({"original_request": parser_user, "raw_response": raw,
                                            "validation_errors": [validation_error]}).decode("utf-8")
        # Repair remains the classifier-predicted parser role with attempt=2.
        repaired, repair_receipt = self._call(case, role, 2, REPAIR_V3_SYSTEM, repair_user)
        result["parser_attempts"].append(repair_receipt)
        if repaired is None:
            result["status"] = _failure_state(repair_receipt)
            return result
        repaired_draft, repair_error, _ = _validate_raw(repaired, case)
        if repair_error:
            result.update(status="model_failure", final_error=repair_error)
        else:
            result.update(status="valid", semantic_draft=repaired_draft)
        return result

    def attempt_state(self, case_id: str, role: str, attempt: int) -> str:
        receipts = load_receipts(self.receipt_path) if self.receipt_path.exists() else []
        receipt_exists = any(row.get("case_id") == case_id and row.get("role") == role and row.get("attempt") == attempt
                             for row in receipts)
        envelope_exists = any(row.get("case_id") == case_id and row.get("role") == role and row.get("attempt") == attempt
                              for row in _load_jsonl(self.envelope_path))
        if receipt_exists:
            receipt = next(row for row in receipts
                           if row.get("case_id") == case_id and row.get("role") == role and row.get("attempt") == attempt)
            return _failure_state(receipt) if receipt.get("error") else "completed"
        return "missing_attempt" if envelope_exists else "missing_attempt"


def _failure_state(receipt: dict[str, Any]) -> str:
    error = str(receipt.get("error") or "")
    if error.startswith("transport_failure:"):
        return "transport_failure"
    if error.startswith("model_failure:"):
        return "model_failure"
    return "missing_attempt"
