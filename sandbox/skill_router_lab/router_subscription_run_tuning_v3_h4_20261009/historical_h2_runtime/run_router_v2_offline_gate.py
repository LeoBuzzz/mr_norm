"""Validate frozen v2 tuning inputs and receipts without workers or network."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router_draft import SEMANTIC_DRAFT_SCHEMA, build_source_manifest
from router_subscription_eval import ReplayAdapter, canonical_json_bytes

HERE = Path(__file__).resolve().parent
RUN_DIR = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"


def _receipt_set_complete(keyed: dict[tuple[str, str, int], dict], case_order: list[str]) -> bool:
    complete = True
    for case_id in case_order:
        classifier = keyed.get((case_id, "classifier", 1))
        if classifier is None:
            complete = False
            continue
        classifier_attempts = sorted(key[2] for key in keyed if key[0] == case_id and key[1] == "classifier")
        if classifier_attempts != [1]:
            raise ValueError(f"classifier receipts must contain only attempt 1: {case_id}")
        try:
            complexity = json.loads(classifier["raw_final_text"]).get("complexity")
        except Exception:
            complexity = None
        if complexity not in {"simple", "complex"}:
            if any(key[0] == case_id and key[1] != "classifier" for key in keyed):
                raise ValueError(f"unexpected parser receipt after invalid classifier: {case_id}")
            continue
        parser_role = "simple_parser" if complexity == "simple" else "complex_parser"
        if (case_id, parser_role, 1) not in keyed:
            complete = False
        attempts = sorted(key[2] for key in keyed if key[0] == case_id and key[1] == parser_role)
        if attempts not in ([1], [1, 2]):
            complete = False
        if any(key[0] == case_id and key[1] not in {"classifier", parser_role} for key in keyed):
            raise ValueError(f"unexpected receipt role for case: {case_id}")
    return complete


def validate_run(run_dir: Path = RUN_DIR) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("split") != "tuning" or manifest.get("case_count") != 20:
        raise ValueError("frozen run must contain exactly 20 tuning cases")
    for relative, expected in manifest["files_sha256"].items():
        actual = hashlib.sha256((run_dir / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen file hash mismatch: {relative}")
    for relative, expected in manifest["code_files_sha256"].items():
        actual = hashlib.sha256((HERE / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"code version hash mismatch: {relative}")
    schema = json.loads((run_dir / "schema.json").read_text(encoding="utf-8"))
    if schema != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("frozen schema differs from validator schema")
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 20 or len({row.get("case_id") for row in rows}) != 20:
        raise ValueError("input manifest must have 20 unique tuning case IDs")
    if [row["case_id"] for row in rows] != manifest["case_order"]:
        raise ValueError("input order differs from frozen manifest")
    if any({"expected", "gold", "split"} & set(row) for row in rows):
        raise ValueError("model input contains expected/gold/split leakage")
    for row in rows:
        source = row["source_manifest"]
        if source.get("tokenizer_version") != manifest.get("tokenizer_version", "source_tokens.v1"):
            raise ValueError("tokenizer version mismatch")
        expected_source = build_source_manifest(row["question"], row.get("context", {}), row.get("pending_clarification"))
        if source != expected_source:
            raise ValueError(f"source manifest mismatch for {row['case_id']}")
    receipts_path = run_dir / "receipts.jsonl"
    receipts = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines() if line.strip()] if receipts_path.is_file() else []
    receipt_state = "awaiting_subscription_receipts"
    if receipts:
        inputs = {row["case_id"]: hashlib.sha256(canonical_json_bytes(row)).hexdigest() for row in rows}
        prompts = {"classifier": manifest["files_sha256"]["prompts/classifier.txt"], "simple_parser": manifest["files_sha256"]["prompts/parser.txt"], "complex_parser": manifest["files_sha256"]["prompts/parser.txt"]}
        models = manifest["requested_models"]
        adapter = ReplayAdapter(receipts, run_id=manifest["run_id"], input_hashes=inputs, prompt_hashes=prompts,
                                schema_hash=manifest["files_sha256"]["schema.json"], expected_models=models,
                                expected_effort=manifest["requested_effort"])
        keyed = {}
        for item in receipts:
            key = (item["case_id"], item["role"], item["attempt"])
            if key in keyed:
                raise ValueError(f"duplicate receipt key: {key}")
            keyed[key] = item
        complete = _receipt_set_complete(keyed, manifest["case_order"])
        receipt_state = "receipts_validated" if complete else "partial_receipts"
        if not complete:
            adapter = None  # Incomplete subscriptions are not replay-ready.
    return {"status": "offline_frozen_inputs_valid", "run_id": manifest["run_id"], "case_count": len(rows), "receipt_state": receipt_state, "live_workers_started": False}


if __name__ == "__main__":
    print(json.dumps(validate_run(), ensure_ascii=False, sort_keys=True))
