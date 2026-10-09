"""H3 subscription lifecycle with frozen H3 prompts and policy-selected validation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from evaluate_router_v2_tuning import _candidate_sets, _parse_classifier, _replace_ids
from jsonschema import Draft202012Validator
from router_draft import SEMANTIC_DRAFT_SCHEMA, assemble_skill_query_plan
from router_subscription_eval import ReplayInputError, append_receipt, canonical_json_bytes, load_receipts
from router_subscription_lifecycle_v3 import FileSubscriptionRunnerV3, ModelFailure, TransportFailure, _append_jsonl
from router_request_envelope_v3 import capture_envelope
from router_validation_policy import validator_for_policy
from freeze_router_v3_h3_r7 import RUN_ID, validate_frozen


def validate_raw_h3(raw: str, case: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Apply the frozen wire schema then the manifest-selected H3 validator."""
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return None, f"invalid JSON: {type(exc).__name__}: {exc}", False
    errors = sorted(Draft202012Validator(SEMANTIC_DRAFT_SCHEMA).iter_errors(parsed),
                    key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message))
    if errors:
        return None, f"wire/schema: {errors[0].message}", False
    try:
        validator = validator_for_policy(manifest.get("validation_policy", ""))
        doc_ids, term_ids, _, _ = _candidate_sets(case)
        draft = validator(parsed, case["source_manifest"], max_tasks=6,
                          candidate_ids=doc_ids, term_ids=term_ids,
                          replace_ids=_replace_ids(case.get("context") or {}, case.get("pending_clarification")))
        return draft, None, True
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", True


def assemble_plan_h3(case: dict[str, Any], complexity: str, draft: dict[str, Any]) -> dict[str, Any]:
    _, _, documents, meaning_groups = _candidate_sets(case)
    plan = assemble_skill_query_plan(
        case["question"], complexity, draft, case["source_manifest"],
        catalog_rows=documents, vocab={"meaning_groups": meaning_groups},
        trace={"replay_mode": "codex_subscription_file_replay"},
    )
    result = plan.to_dict()
    result.setdefault("trace", {})["source_manifest"] = case["source_manifest"]
    result["trace"]["semantic_draft"] = draft
    return result


class FileSubscriptionRunnerH3(FileSubscriptionRunnerV3):
    """Reuse durable inherited receipt mechanics but select H3 frozen prompts/policy."""

    def __init__(self, run_dir: Path, dispatch: Callable[[dict[str, Any]], str | dict[str, Any]],
                 requested_models: dict[str, str] | None = None, effort: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.frozen_validation = validate_frozen(self.run_dir)
        self.dispatch = dispatch
        self.manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("run_id") != RUN_ID:
            raise ReplayInputError("H3 lifecycle refuses a different run identity")
        if self.manifest.get("validation_policy") != "topic_literal_guard.v1":
            raise ReplayInputError("H3 lifecycle requires topic_literal_guard.v1")
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
        self.prompts = {name: (self.run_dir / f"prompts/{name}.txt").read_text(encoding="utf-8")
                        for name in ("classifier", "parser", "repair")}

    def _call(self, case: dict[str, Any], role: str, attempt: int, system: str,
              user_content: str) -> tuple[str | None, dict[str, Any]]:
        key = (case["case_id"], role, attempt)
        if self._existing_key(*key):
            raise ReplayInputError(f"refusing to resample existing role/attempt: {key}")
        envelope = {"role": role, "attempt": attempt, "model": self.requested_models[role],
                    "effort": self.effort,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}]}
        captured = capture_envelope(envelope)
        _append_jsonl(self.envelope_path, {"run_id": self.manifest["run_id"], "case_id": key[0],
                                           "role": role, "attempt": attempt,
                                           "envelope_sha256": captured["envelope_sha256"],
                                           "envelope_utf8": captured["envelope_utf8"],
                                           "capture_stage": "pre_dispatch"})
        started = datetime.now(timezone.utc).isoformat()
        raw, error, finish = "", None, "completed"
        try:
            result = self.dispatch(envelope)
            if isinstance(result, dict) and result.get("failure"):
                failure = str(result["failure"])
                error = f"{'transport_failure' if failure == 'transport_failure' else 'model_failure'}: {result.get('error') or failure}"
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
        prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        receipt = {"run_id": self.manifest["run_id"], "case_id": key[0], "role": role, "attempt": attempt,
                  "input_sha256": hashlib.sha256(canonical_json_bytes(case)).hexdigest(),
                  "prompt_sha256": prompt_hash, "schema_sha256": self.manifest["files_sha256"]["schema.json"],
                  "agent_id": "injected-dispatch", "requested_model": envelope["model"],
                  "requested_effort": envelope["effort"], "actual_model": None,
                  "model_verification": "requested_by_host_only", "started_at": started,
                  "finished_at": datetime.now(timezone.utc).isoformat(), "raw_final_text": raw,
                  "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                  "event_id": None, "finish": finish, "error": error}
        append_receipt(self.receipt_path, receipt)
        _append_jsonl(self.binding_path, {"run_id": self.manifest["run_id"], "case_id": key[0],
                                          "role": role, "attempt": attempt,
                                          "envelope_sha256": captured["envelope_sha256"],
                                          "prompt_sha256": prompt_hash})
        return (None if error else raw), receipt

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        user = canonical_json_bytes({"input": case}).decode("utf-8")
        classifier_raw, classifier_receipt = self._call(case, "classifier", 1, self.prompts["classifier"], user)
        result: dict[str, Any] = {"case_id": case["case_id"], "classifier": classifier_receipt, "parser_attempts": []}
        if classifier_raw is None:
            result["status"] = "transport_failure" if str(classifier_receipt.get("error", "")).startswith("transport_failure:") else "model_failure"
            return result
        complexity, error = _parse_classifier(classifier_raw)
        if error:
            result.update(status="model_failure", classifier_error=error)
            return result
        role = f"{complexity}_parser"
        parser_user = canonical_json_bytes({"input": case, "classifier": {"complexity": complexity}}).decode("utf-8")
        raw, receipt = self._call(case, role, 1, self.prompts["parser"], parser_user)
        result["parser_attempts"].append(receipt)
        if raw is None:
            result["status"] = "transport_failure" if str(receipt.get("error", "")).startswith("transport_failure:") else "model_failure"
            return result
        draft, validation_error, _ = validate_raw_h3(raw, case, self.manifest)
        if validation_error is None:
            plan = assemble_plan_h3(case, complexity, draft)
            result.update(status="valid", semantic_draft=draft, plan=plan)
            return result
        repair_user = canonical_json_bytes({"original_request": parser_user, "raw_response": raw,
                                            "validation_errors": [validation_error]}).decode("utf-8")
        repaired, repair_receipt = self._call(case, role, 2, self.prompts["repair"], repair_user)
        result["parser_attempts"].append(repair_receipt)
        if repaired is None:
            result["status"] = "transport_failure" if str(repair_receipt.get("error", "")).startswith("transport_failure:") else "model_failure"
            return result
        repaired_draft, repair_error, _ = validate_raw_h3(repaired, case, self.manifest)
        if repair_error:
            result.update(status="model_failure", final_error=repair_error)
        else:
            plan = assemble_plan_h3(case, complexity, repaired_draft)
            result.update(status="valid", semantic_draft=repaired_draft, plan=plan)
        return result
