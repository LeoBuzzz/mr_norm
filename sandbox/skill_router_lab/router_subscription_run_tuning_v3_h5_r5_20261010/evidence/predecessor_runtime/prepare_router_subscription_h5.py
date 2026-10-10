"""Host-side prepare/import coordinator for the frozen H5 file subscription run.

This module writes exact pre-dispatch request envelopes and imports raw finals
into the inherited receipt/binding protocol. It never dispatches a model call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_router_v2_tuning import _parse_classifier
from freeze_router_v3_h5 import HERE, RUN_DIR, RUN_ID, EXPECTED_MANIFEST_SHA256, validate_frozen
from router_subscription_eval import ReplayInputError, append_receipt, canonical_json_bytes, load_receipts
from router_subscription_lifecycle_h5 import validate_raw_h3
from router_request_envelope_v3 import capture_envelope

EXPECTED_RUN_ID = RUN_ID

ENVELOPE_FILE = "request_envelopes.jsonl"
BINDING_FILE = "receipt_envelope_bindings.jsonl"
RECEIPT_FILE = "receipts.jsonl"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayInputError(f"invalid JSONL {path.name}:{line_no}") from exc
            if not isinstance(row, dict):
                raise ReplayInputError(f"invalid row in {path.name}:{line_no}")
            result.append(row)
    return result


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(row) + b"\n")


class H5SubscriptionCoordinator:
    """Prepare envelopes and import host-returned outputs without model transport."""

    def __init__(self, run_dir: Path = RUN_DIR) -> None:
        self.run_dir = Path(run_dir)
        manifest_path = self.run_dir / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        if _sha(manifest_bytes) != EXPECTED_MANIFEST_SHA256:
            raise ReplayInputError("coordinator rejects an unpinned exact H5 r3 manifest")
        validated = validate_frozen(self.run_dir)
        if validated.get("run_id") != EXPECTED_RUN_ID:
            raise ReplayInputError("coordinator is pinned to the H5 r3 run identity")
        self.manifest = json.loads(manifest_bytes)
        self.run_id = self.manifest["run_id"]
        self.rows = [json.loads(line) for line in
                     (self.run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()
                     if line.strip()]
        if len(self.rows) != 20 or [row["case_id"] for row in self.rows] != self.manifest["case_order"]:
            raise ReplayInputError("H5 tuning inputs do not match the frozen case order")
        self.cases = {row["case_id"]: row for row in self.rows}
        self.prompts = {role: (self.run_dir / f"prompts/{role}.txt").read_text(encoding="utf-8")
                        for role in ("classifier", "parser", "repair")}
        self.envelope_path = self.run_dir / ENVELOPE_FILE
        self.binding_path = self.run_dir / BINDING_FILE
        self.receipt_path = self.run_dir / RECEIPT_FILE

    def _check_initial_empty(self) -> None:
        if load_receipts(self.receipt_path) if self.receipt_path.exists() else []:
            raise ReplayInputError("H5 coordinator start requires empty receipts")
        if _jsonl(self.envelope_path) or _jsonl(self.binding_path):
            raise ReplayInputError("H5 coordinator start requires no existing envelopes or bindings")

    def _receipts(self) -> list[dict[str, Any]]:
        return load_receipts(self.receipt_path) if self.receipt_path.exists() else []

    def _request_keys(self) -> set[tuple[str, str, int]]:
        return {(row.get("case_id"), row.get("role"), row.get("attempt"))
                for row in _jsonl(self.envelope_path)}

    def _receipt_keys(self) -> set[tuple[str, str, int]]:
        return {(row["case_id"], row["role"], row["attempt"]) for row in self._receipts()}

    def _prepared_index(self) -> dict[tuple[str, str, int], dict[str, Any]]:
        index = {}
        for row in _jsonl(self.envelope_path):
            if row.get("run_id") != self.run_id:
                raise ReplayInputError("prepared envelope run_id mismatch")
            key = (row.get("case_id"), row.get("role"), row.get("attempt"))
            if key in index:
                raise ReplayInputError(f"duplicate prepared envelope: {key}")
            try:
                envelope_bytes = row["envelope_utf8"].encode("utf-8")
                envelope = json.loads(envelope_bytes)
            except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReplayInputError("malformed prepared envelope row") from exc
            if _sha(envelope_bytes) != row.get("envelope_sha256"):
                raise ReplayInputError(f"prepared envelope hash mismatch: {key}")
            if (row.get("capture_stage") != "pre_dispatch"
                    or capture_envelope(envelope)["envelope_utf8"] != row["envelope_utf8"]):
                raise ReplayInputError(f"prepared envelope is not canonical pre-dispatch data: {key}")
            if (envelope.get("role"), envelope.get("attempt")) != key[1:]:
                raise ReplayInputError(f"prepared envelope key mismatch: {key}")
            index[key] = row
        return index

    def _build_envelope(self, case: dict[str, Any], role: str, attempt: int,
                        user_content: str) -> dict[str, Any]:
        model_key = "classifier" if role == "classifier" else role
        system_key = "repair" if attempt == 2 else ("classifier" if role == "classifier" else "parser")
        envelope = {"role": role, "attempt": attempt,
                    "model": self.manifest["requested_models"][model_key],
                    "effort": self.manifest["requested_effort"],
                    "messages": [{"role": "system", "content": self.prompts[system_key]},
                                 {"role": "user", "content": user_content}]}
        return envelope

    def _write_prepared(self, case: dict[str, Any], role: str, attempt: int,
                        user_content: str) -> dict[str, Any]:
        key = (case["case_id"], role, attempt)
        if key in self._request_keys() or key in self._receipt_keys():
            raise ReplayInputError(f"refusing duplicate/resumed role attempt: {key}")
        envelope = self._build_envelope(case, role, attempt, user_content)
        captured = capture_envelope(envelope)
        row = {"run_id": self.run_id, "case_id": case["case_id"], "role": role,
               "attempt": attempt, "envelope_sha256": captured["envelope_sha256"],
               "envelope_utf8": captured["envelope_utf8"], "capture_stage": "pre_dispatch"}
        _append(self.envelope_path, row)
        return row

    def prepare_classifier(self) -> dict[str, Any]:
        self._check_initial_empty()
        prepared = []
        for case in self.rows:
            user = canonical_json_bytes({"input": case}).decode("utf-8")
            prepared.append(self._write_prepared(case, "classifier", 1, user))
        return {"run_id": self.run_id, "phase": "classifier", "prepared_count": len(prepared),
                "envelope_file": str(self.envelope_path),
                "envelope_hashes": {row["case_id"]: row["envelope_sha256"] for row in prepared}}

    def _expected_parser_role(self, case_id: str, receipts: list[dict[str, Any]]) -> str:
        classifier = next((row for row in receipts if row["case_id"] == case_id and row["role"] == "classifier"), None)
        if classifier is None or classifier.get("error") or classifier.get("finish") not in {"completed", "stop"}:
            raise ReplayInputError(f"classifier output unavailable for parser preparation: {case_id}")
        complexity, error = _parse_classifier(classifier["raw_final_text"])
        if error or complexity not in {"simple", "complex"}:
            raise ReplayInputError(f"classifier output is invalid for parser preparation: {case_id}: {error}")
        return f"{complexity}_parser"

    def prepare_parsers(self) -> dict[str, Any]:
        receipts = self._receipts()
        classifier_by_case = {row["case_id"]: row for row in receipts if row["role"] == "classifier" and row["attempt"] == 1}
        if set(classifier_by_case) != set(self.cases):
            raise ReplayInputError("all 20 classifier finals must be imported before parser preparation")
        prepared = []
        skipped = []
        existing = self._prepared_index()
        imported = self._receipt_keys()
        for case_id in self.manifest["case_order"]:
            classifier = classifier_by_case[case_id]
            if classifier.get("error") or classifier.get("finish") not in {"completed", "stop"}:
                skipped.append({"case_id": case_id, "reason": "classifier_failure"})
                continue
            complexity, error = _parse_classifier(classifier["raw_final_text"])
            if error or complexity not in {"simple", "complex"}:
                skipped.append({"case_id": case_id, "reason": "invalid_classifier_output"})
                continue
            role = f"{complexity}_parser"
            key = (case_id, role, 1)
            if key in imported:
                skipped.append({"case_id": case_id, "reason": "parser_attempt_already_imported"})
                continue
            if key in existing:
                prepared.append(existing[key])
                continue
            case = self.cases[case_id]
            user = canonical_json_bytes({"input": case, "classifier": {"complexity": complexity}}).decode("utf-8")
            prepared.append(self._write_prepared(case, role, 1, user))
        return {"run_id": self.run_id, "phase": "parser", "prepared_count": len(prepared),
                "skipped": skipped, "envelope_file": str(self.envelope_path),
                "envelope_hashes": {row["case_id"]: row["envelope_sha256"] for row in prepared}}

    def import_raw_final(self, *, case_id: str, role: str, attempt: int, envelope_sha256: str,
                         raw_final_text: str | None = None, failure: str | None = None,
                         agent_id: str, started_at: str, finished_at: str,
                         actual_model: str | None = None, event_id: str | None = None) -> dict[str, Any]:
        if case_id not in self.cases:
            raise ReplayInputError(f"unknown H5 case_id: {case_id}")
        if role not in {"classifier", "simple_parser", "complex_parser"} or attempt not in {1, 2}:
            raise ReplayInputError("unsupported role/attempt; repair is parser role attempt 2")
        key = (case_id, role, attempt)
        prepared = self._prepared_index().get(key)
        if prepared is None or prepared.get("envelope_sha256") != envelope_sha256:
            raise ReplayInputError(f"raw final does not bind to a prepared exact envelope: {key}")
        if key in self._receipt_keys():
            raise ReplayInputError(f"duplicate immutable output for role/attempt: {key}")
        envelope = json.loads(prepared["envelope_utf8"])
        case = self.cases[case_id]
        receipts = self._receipts()
        self._validate_stage_binding(case, role, attempt, envelope, receipts)
        if not agent_id or not started_at or not finished_at:
            raise ReplayInputError("agent_id and timestamps must be supplied from the isolated agent turn")
        if failure is None:
            if not isinstance(raw_final_text, str):
                raise ReplayInputError("raw_final_text must be supplied exactly as returned")
            raw = raw_final_text
            finish, error = "completed", None
        else:
            raw = ""
            finish, error = "error", str(failure)
            if not error.startswith(("transport_failure:", "model_failure:")):
                raise ReplayInputError("failure must be tagged transport_failure: or model_failure:")
        prompt_hash = _sha(envelope["messages"][0]["content"].encode("utf-8"))
        model_verification = "host_verified" if actual_model else "requested_by_host_only"
        receipt = {"run_id": self.run_id, "case_id": case_id, "role": role, "attempt": attempt,
                  "input_sha256": _sha(canonical_json_bytes(case)), "prompt_sha256": prompt_hash,
                  "schema_sha256": self.manifest["files_sha256"]["schema.json"], "agent_id": agent_id,
                  "requested_model": envelope["model"], "requested_effort": envelope["effort"],
                  "actual_model": actual_model, "model_verification": model_verification,
                  "started_at": started_at, "finished_at": finished_at,
                  "raw_final_text": raw, "raw_sha256": _sha(raw.encode("utf-8")),
                  "event_id": event_id, "finish": finish, "error": error}
        binding = {"run_id": self.run_id, "case_id": case_id, "role": role, "attempt": attempt,
                   "envelope_sha256": envelope_sha256, "prompt_sha256": prompt_hash}
        existing_bindings = _jsonl(self.binding_path)
        if any((row.get("case_id"), row.get("role"), row.get("attempt")) == key for row in existing_bindings):
            raise ReplayInputError(f"duplicate envelope binding: {key}")
        append_receipt(self.receipt_path, receipt)
        _append(self.binding_path, binding)
        next_step = self._after_import(case, role, attempt, receipt, envelope)
        return {"status": "imported", "case_id": case_id, "role": role, "attempt": attempt,
                "receipt_sha256": _sha(canonical_json_bytes(receipt)), **next_step}

    def _validate_stage_binding(self, case: dict[str, Any], role: str, attempt: int,
                                envelope: dict[str, Any], receipts: list[dict[str, Any]]) -> None:
        classifier = next((row for row in receipts if row["case_id"] == case["case_id"]
                           and row["role"] == "classifier" and row["attempt"] == 1), None)
        if role == "classifier":
            if attempt != 1:
                raise ReplayInputError("classifier must be attempt 1")
            expected_user = canonical_json_bytes({"input": case}).decode("utf-8")
            expected_model = self.manifest["requested_models"]["classifier"]
            expected_prompt = self.prompts["classifier"]
        else:
            if classifier is None:
                raise ReplayInputError("parser/repair output requires imported classifier receipt")
            complexity, error = _parse_classifier(classifier["raw_final_text"])
            if error or role != f"{complexity}_parser":
                raise ReplayInputError("parser role does not match imported classifier result")
            expected_model = self.manifest["requested_models"][role]
            expected_prompt = self.prompts["repair" if attempt == 2 else "parser"]
            parser_user = canonical_json_bytes({"input": case, "classifier": {"complexity": complexity}}).decode("utf-8")
            if attempt == 1:
                expected_user = parser_user
            else:
                first = next((row for row in receipts if row["case_id"] == case["case_id"]
                              and row["role"] == role and row["attempt"] == 1), None)
                if first is None or first.get("error"):
                    raise ReplayInputError("repair requires successful parser attempt 1")
                _, validation_error, _ = validate_raw_h3(first["raw_final_text"], case, self.manifest)
                if validation_error is None:
                    raise ReplayInputError("repair not allowed after technically valid parser attempt")
                expected_user = canonical_json_bytes({"original_request": parser_user,
                    "raw_response": first["raw_final_text"], "validation_errors": [validation_error]}).decode("utf-8")
        if envelope.get("model") != expected_model or envelope.get("effort") != self.manifest["requested_effort"]:
            raise ReplayInputError("prepared request model/effort differs from frozen manifest")
        messages = envelope.get("messages") or []
        if (len(messages) != 2 or messages[0] != {"role": "system", "content": expected_prompt}
                or messages[1] != {"role": "user", "content": expected_user}):
            raise ReplayInputError("prepared request does not match exact lifecycle prompt/user payload")

    def _after_import(self, case: dict[str, Any], role: str, attempt: int,
                      receipt: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        if receipt.get("error"):
            return {"next_phase": "terminal_failure"}
        raw = receipt["raw_final_text"]
        if role == "classifier":
            complexity, error = _parse_classifier(raw)
            if error:
                return {"next_phase": "terminal_invalid_classifier", "validation_error": error}
            return {"next_phase": "prepare_parser", "parser_role": f"{complexity}_parser"}
        draft, error, _ = validate_raw_h3(raw, case, self.manifest)
        if attempt == 1 and error:
            parser_user = canonical_json_bytes({"input": case,
                "classifier": {"complexity": role.removesuffix("_parser")}}).decode("utf-8")
            repair_user = canonical_json_bytes({"original_request": parser_user,
                "raw_response": raw, "validation_errors": [error]}).decode("utf-8")
            repair = self._write_prepared(case, role, 2, repair_user)
            return {"next_phase": "repair", "repair_envelope_sha256": repair["envelope_sha256"]}
        return {"next_phase": "complete" if error is None else "terminal_invalid_repair",
                "validation_error": error}

    def status(self) -> dict[str, Any]:
        receipts = self._receipts()
        envelopes = _jsonl(self.envelope_path)
        bindings = _jsonl(self.binding_path)
        return {"run_id": self.run_id, "manifest_sha256": _sha((self.run_dir / "manifest.json").read_bytes()),
                "prepared_envelopes": len(envelopes), "imported_receipts": len(receipts),
                "receipt_bindings": len(bindings), "phase_counts": {
                    role: sum(row["role"] == role for row in envelopes)
                    for role in ("classifier", "simple_parser", "complex_parser")},
                "repair_attempts": sum(row["attempt"] == 2 for row in envelopes)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare-classifier", "prepare-parsers", "import-final", "status"))
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--case-id")
    parser.add_argument("--role", choices=("classifier", "simple_parser", "complex_parser"))
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--envelope-sha256")
    parser.add_argument("--raw-file", type=Path)
    parser.add_argument("--failure")
    parser.add_argument("--agent-id")
    parser.add_argument("--started-at")
    parser.add_argument("--finished-at")
    parser.add_argument("--actual-model")
    parser.add_argument("--event-id")
    args = parser.parse_args(argv)
    coordinator = H5SubscriptionCoordinator(args.run_dir)
    if args.action == "import-final":
        if not all((args.case_id, args.role, args.attempt, args.envelope_sha256,
                    args.agent_id, args.started_at, args.finished_at)):
            parser.error("import-final requires case/role/attempt/envelope hash, agent id, and both timestamps")
        if bool(args.raw_file) == bool(args.failure):
            parser.error("provide exactly one of --raw-file or --failure")
        raw = args.raw_file.read_text(encoding="utf-8") if args.raw_file else None
        result = coordinator.import_raw_final(case_id=args.case_id, role=args.role, attempt=args.attempt,
            envelope_sha256=args.envelope_sha256, raw_final_text=raw, failure=args.failure,
            agent_id=args.agent_id, started_at=args.started_at, finished_at=args.finished_at,
            actual_model=args.actual_model, event_id=args.event_id)
    else:
        result = {"prepare-classifier": coordinator.prepare_classifier,
                  "prepare-parsers": coordinator.prepare_parsers,
                  "status": coordinator.status}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

