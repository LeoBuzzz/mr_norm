"""Freeze the H3 tuning inputs and versioned prompt/validator policy offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router_draft import SEMANTIC_DRAFT_SCHEMA
from router_draft_h3 import VALIDATION_POLICY
from router_prompts_v3_h3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_subscription_eval import freeze_files

HERE = Path(__file__).resolve().parent
SOURCE_RUN = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
R3_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r3_20261008"
RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r4_20261008"
RUN_ID = "router-v3-h3-tuning-20261008-r4"
R3_RUN_ID = "router-v3-h3-tuning-20261008-r3"
R3_MANIFEST_SHA256 = "509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5"
H2_PARENT_RUN_ID = "router-v3-h2-tuning-20261008-r2"
H2_PARENT_MANIFEST_SHA256 = "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
SUPERSEDED_H3_RUN_IDS = ["router-v3-h3-tuning-20261008-r1", "router-v3-h3-tuning-20261008-r2", R3_RUN_ID]
PROMPTS = {
    "prompts/classifier.txt": CLASSIFIER_V3_SYSTEM.encode("utf-8"),
    "prompts/parser.txt": PARSER_V3_SYSTEM.encode("utf-8"),
    "prompts/repair.txt": REPAIR_V3_SYSTEM.encode("utf-8"),
}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def freeze() -> dict:
    if RUN_DIR.exists():
        raise FileExistsError(f"reserved H3 run directory already exists: {RUN_DIR}")
    source_manifest_bytes = (SOURCE_RUN / "manifest.json").read_bytes()
    source_manifest = json.loads(source_manifest_bytes)
    if source_manifest["run_id"] != H2_PARENT_RUN_ID or sha256(source_manifest_bytes) != H2_PARENT_MANIFEST_SHA256:
        raise ValueError("H3 parent is not the frozen H2 r2 run")
    inputs = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    input_rows = [json.loads(line) for line in inputs.decode("utf-8").splitlines() if line.strip()]
    source_manifests = (SOURCE_RUN / "source_manifests.json").read_bytes()
    schema = (SOURCE_RUN / "schema.json").read_bytes()
    if json.loads(schema) != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("H3 schema differs from frozen H2 schema")

    RUN_DIR.mkdir(parents=True)
    frozen_files = {
        "inputs/input_manifest.jsonl": inputs,
        "source_manifests.json": source_manifests,
        "schema.json": schema,
        **PROMPTS,
    }
    for relative, payload in frozen_files.items():
        path = RUN_DIR / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    file_hashes = freeze_files(RUN_DIR, frozen_files)

    r3_manifest_bytes = (R3_RUN_DIR / "manifest.json").read_bytes()
    r3_manifest = json.loads(r3_manifest_bytes)
    if r3_manifest.get("run_id") != R3_RUN_ID or sha256(r3_manifest_bytes) != R3_MANIFEST_SHA256:
        raise ValueError("r4 parent is not the pinned unmodified H3 r3 freeze")
    # Preserve exact pre-H3 runtime from the immutable r3 snapshot. The one
    # guarded legacy entry point is versioned separately below.
    snapshot_dir = RUN_DIR / "historical_h2_runtime"
    snapshot_dir.mkdir()
    snapshot_hashes = {}
    for filename, expected in source_manifest["code_files_sha256"].items():
        source = R3_RUN_DIR / "historical_h2_runtime" / filename
        payload = source.read_bytes()
        if sha256(payload) != expected:
            raise ValueError(f"H2 runtime snapshot in r3 differs from frozen source: {filename}")
        (snapshot_dir / filename).write_bytes(payload)
        snapshot_hashes[filename] = expected

    parent_hash = sha256(source_manifest_bytes)
    h3_code = ["router_prompts_v3_h3.py", "router_draft_h3.py", "router_validation_policy.py",
               "router_subscription_lifecycle_h3.py", "evaluate_router_v3_h3_replay.py",
               "evaluate_router_v3_h3_semantic_replay.py", "router_subscription_lifecycle_v3.py",
               "router_draft.py", "router_subscription_eval_v3.py", "freeze_router_v3_h3_run.py"]
    code_hashes = {name: sha256((HERE / name).read_bytes()) for name in h3_code}
    evaluation_data = {
        "router_plan_cases_tuning_v2.json": sha256((HERE / "router_plan_cases_tuning_v2.json").read_bytes()),
        "baseline_reconciled_v3_20261008.json": sha256((HERE / "baseline_reconciled_v3_20261008.json").read_bytes()),
    }
    manifest = {
        "schema_version": "codex_subscription_file_replay.v1",
        "experiment_mode": "codex_subscription_file_replay",
        "run_id": RUN_ID,
        "supersedes_unreceipted_run_ids": SUPERSEDED_H3_RUN_IDS,
        "lineage": {"parent_run_id": R3_RUN_ID, "parent_manifest_sha256": R3_MANIFEST_SHA256,
                    "h2_parent_run_id": H2_PARENT_RUN_ID, "h2_parent_manifest_sha256": H2_PARENT_MANIFEST_SHA256,
                    "reason": "H3 closes legacy-runner bypass and freezes actual assembly/semantic replay"},
        "split": "tuning",
        "case_count": 20,
        "case_order": source_manifest["case_order"],
        "tokenizer_version": source_manifest["tokenizer_version"],
        "locale": source_manifest["locale"],
        "requested_effort": source_manifest["requested_effort"],
        "requested_models": source_manifest["requested_models"],
        "supports_json_schema": source_manifest["supports_json_schema"],
        "transport": "Codex subscription file replay; H3 implementation freeze only, no sampling",
        "validation_policy": VALIDATION_POLICY,
        "files_sha256": file_hashes,
        "code_files_sha256": code_hashes,
        "historical_h2_runtime_sha256": snapshot_hashes,
        "historical_h2_manifest_sha256": parent_hash,
        "r3_parent_manifest_sha256": R3_MANIFEST_SHA256,
        "evaluation_data_sha256": evaluation_data,
        "parity": {"input_manifest_sha256_same": file_hashes["inputs/input_manifest.jsonl"] == source_manifest["files_sha256"]["inputs/input_manifest.jsonl"],
                   "schema_sha256_same": file_hashes["schema.json"] == source_manifest["files_sha256"]["schema.json"],
                   "case_order_same": [row["case_id"] for row in input_rows] == source_manifest["case_order"]},
        "gold_included": False,
        "receipts_included": False,
    }
    (RUN_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validate_frozen()


def validate_frozen(run_dir: Path | None = None) -> dict:
    run_dir = Path(run_dir or RUN_DIR)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("run_id") != RUN_ID:
        raise ValueError("H3 run identity mismatch")
    if manifest.get("supersedes_unreceipted_run_ids") != SUPERSEDED_H3_RUN_IDS:
        raise ValueError("H3 superseded-run lineage mismatch")
    lineage = manifest.get("lineage") or {}
    if (lineage.get("parent_run_id") != R3_RUN_ID or lineage.get("parent_manifest_sha256") != R3_MANIFEST_SHA256
            or lineage.get("h2_parent_run_id") != H2_PARENT_RUN_ID
            or lineage.get("h2_parent_manifest_sha256") != H2_PARENT_MANIFEST_SHA256):
        raise ValueError("H3 direct/ultimate parent identity/hash mismatch")
    if manifest.get("historical_h2_manifest_sha256") != H2_PARENT_MANIFEST_SHA256:
        raise ValueError("H3 historical H2 manifest hash mismatch")
    r3_parent_bytes = (R3_RUN_DIR / "manifest.json").read_bytes()
    if sha256(r3_parent_bytes) != R3_MANIFEST_SHA256 or json.loads(r3_parent_bytes).get("run_id") != R3_RUN_ID:
        raise ValueError("pinned H3 r3 parent artifact changed")
    parent_bytes = (SOURCE_RUN / "manifest.json").read_bytes()
    if sha256(parent_bytes) != H2_PARENT_MANIFEST_SHA256 or json.loads(parent_bytes).get("run_id") != H2_PARENT_RUN_ID:
        raise ValueError("frozen H2 parent artifact changed")
    source_manifest = json.loads(parent_bytes)
    source_prompt = (SOURCE_RUN / "prompts/classifier.txt").read_bytes()
    if source_prompt != (run_dir / "prompts/classifier.txt").read_bytes():
        raise ValueError("H3 classifier prompt must remain byte-identical to H2")
    if (run_dir / "prompts/parser.txt").read_bytes() != PARSER_V3_SYSTEM.encode("utf-8"):
        raise ValueError("H3 parser prompt bytes do not match the H3 implementation")
    if (run_dir / "prompts/repair.txt").read_bytes() != REPAIR_V3_SYSTEM.encode("utf-8"):
        raise ValueError("H3 repair prompt bytes do not match the H3 implementation")
    if manifest.get("validation_policy") != VALIDATION_POLICY:
        raise ValueError("H3 validation policy mismatch")
    for relative, expected in manifest["files_sha256"].items():
        if sha256((run_dir / relative).read_bytes()) != expected:
            raise ValueError(f"frozen H3 file hash mismatch: {relative}")
    for filename, expected in manifest["code_files_sha256"].items():
        if sha256((HERE / filename).read_bytes()) != expected:
            raise ValueError(f"H3 executable hash mismatch: {filename}")
    for filename, expected in manifest["historical_h2_runtime_sha256"].items():
        if sha256((run_dir / "historical_h2_runtime" / filename).read_bytes()) != expected:
            raise ValueError(f"historical H2 snapshot hash mismatch: {filename}")
        if filename != "router_subscription_lifecycle_v3.py" and sha256((HERE / filename).read_bytes()) != expected:
            raise ValueError(f"live inherited dependency differs from historical H2 hash: {filename}")
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    source_rows = [json.loads(line) for line in (SOURCE_RUN / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if ((run_dir / "inputs/input_manifest.jsonl").read_bytes() != (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
            or (run_dir / "source_manifests.json").read_bytes() != (SOURCE_RUN / "source_manifests.json").read_bytes()
            or (run_dir / "schema.json").read_bytes() != (SOURCE_RUN / "schema.json").read_bytes()):
        raise ValueError("H3 inputs, source manifests, or schema differ byte-for-byte from H2")
    if len(rows) != 20 or rows != source_rows or [row["case_id"] for row in rows] != manifest["case_order"]:
        raise ValueError("H3 input bytes/order differ from H2 tuning run")
    if (run_dir / "gold.json").exists() or any({"expected", "gold", "split"} & set(row) for row in rows):
        raise ValueError("gold leakage into H3 frozen model inputs")
    for filename, expected in manifest["evaluation_data_sha256"].items():
        if sha256((HERE / filename).read_bytes()) != expected:
            raise ValueError(f"H3 evaluator source data changed: {filename}")
    receipts_present = (run_dir / "receipts.jsonl").exists()
    receipt_count = sum(bool(line.strip()) for line in (run_dir / "receipts.jsonl").read_text(encoding="utf-8").splitlines()) if receipts_present else 0
    return {"status": "h3_frozen_inputs_valid", "run_id": RUN_ID, "case_count": len(rows),
            "parent_manifest_sha256": manifest["historical_h2_manifest_sha256"],
            "direct_parent_manifest_sha256": manifest["r3_parent_manifest_sha256"],
            "validation_policy": VALIDATION_POLICY, "gold_present": False,
            "receipts_present": receipts_present, "receipt_count": receipt_count}


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, indent=2))
