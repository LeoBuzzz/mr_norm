"""Create/validate H3 r5, pinning the snapshot replay and strict gates."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from router_draft import SEMANTIC_DRAFT_SCHEMA
from router_draft_h3 import VALIDATION_POLICY
from router_prompts_v3_h3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_subscription_eval import freeze_files

HERE = Path(__file__).resolve().parent
SOURCE_RUN = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
R4_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r4_20261008"
RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r5_20261009"
RUN_ID = "router-v3-h3-tuning-20261009-r5"
R4_RUN_ID = "router-v3-h3-tuning-20261008-r4"
R4_MANIFEST_SHA256 = "5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0"
H2_MANIFEST_SHA256 = "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
SUPERSEDED = ["router-v3-h3-tuning-20261008-r1", "router-v3-h3-tuning-20261008-r2",
              "router-v3-h3-tuning-20261008-r3", R4_RUN_ID]
PROMPTS = {"prompts/classifier.txt": CLASSIFIER_V3_SYSTEM.encode("utf-8"),
           "prompts/parser.txt": PARSER_V3_SYSTEM.encode("utf-8"),
           "prompts/repair.txt": REPAIR_V3_SYSTEM.encode("utf-8")}
H2_RUNTIME_FILES = ["generate_router_v2_tuning.py", "router_contracts.py", "router_draft.py",
                    "router_planning.py", "router_prompts_v2.py", "router_subscription_eval.py",
                    "router_v2_runtime.py", "run_router_v2_offline_gate.py", "evaluate_router_v2_tuning.py",
                    "router_prompts_v3.py", "router_subscription_eval_v3.py", "router_request_envelope_v3.py",
                    "router_subscription_lifecycle_v3.py", "evaluate_router_v3_file_replay.py",
                    "freeze_router_v3_run.py", "evaluate_router_v3_semantic_replay.py"]
H3_CODE_FILES = ["router_prompts_v3_h3.py", "router_draft_h3.py", "router_validation_policy.py",
                 "router_subscription_lifecycle_h3_r5.py", "evaluate_router_v3_h3_replay_r5.py",
                 "evaluate_router_v3_h3_semantic_replay_r5.py", "router_subscription_lifecycle_v3.py",
                 "router_draft.py", "router_subscription_eval_v3.py", "freeze_router_v3_h3_r5.py",
                 "replay_router_h2_snapshot.py", "evaluate_router_v3_semantic_replay.py"]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def freeze() -> dict[str, Any]:
    if RUN_DIR.exists():
        raise FileExistsError(f"reserved H3 r5 directory already exists: {RUN_DIR}")
    h2_manifest_bytes = (SOURCE_RUN / "manifest.json").read_bytes()
    h2_manifest = json.loads(h2_manifest_bytes)
    if h2_manifest.get("run_id") != "router-v3-h2-tuning-20261008-r2" or sha(h2_manifest_bytes) != H2_MANIFEST_SHA256:
        raise ValueError("H3 r5 is not based on the pinned H2 r2 freeze")
    r4_bytes = (R4_RUN_DIR / "manifest.json").read_bytes()
    r4_manifest = json.loads(r4_bytes)
    if r4_manifest.get("run_id") != R4_RUN_ID or sha(r4_bytes) != R4_MANIFEST_SHA256:
        raise ValueError("H3 r4 parent identity/hash mismatch")
    if (R4_RUN_DIR / "receipts.jsonl").exists():
        raise ValueError("r4 parent unexpectedly contains receipts")
    inputs = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    source_manifests = (SOURCE_RUN / "source_manifests.json").read_bytes()
    schema = (SOURCE_RUN / "schema.json").read_bytes()
    if json.loads(schema) != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("H3 r5 schema differs from frozen H2")
    RUN_DIR.mkdir(parents=True)
    frozen_files = {"inputs/input_manifest.jsonl": inputs, "source_manifests.json": source_manifests,
                    "schema.json": schema, **PROMPTS}
    for relative, content in frozen_files.items():
        target = RUN_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    files_sha = freeze_files(RUN_DIR, frozen_files)

    snapshot = RUN_DIR / "historical_h2_runtime"
    snapshot.mkdir()
    snapshot_sha: dict[str, str] = {}
    r4_snapshot = R4_RUN_DIR / "historical_h2_runtime"
    for name, expected in h2_manifest["code_files_sha256"].items():
        content = (r4_snapshot / name).read_bytes()
        if sha(content) != expected:
            raise ValueError(f"r4's H2 runtime snapshot differs from original frozen code: {name}")
        (snapshot / name).write_bytes(content)
        snapshot_sha[name] = expected
    # The semantic evaluator was not part of the H2 runner's runtime inventory;
    # freeze its exact replay implementation in this revision for reproduction.
    semantic_eval = (HERE / "evaluate_router_v3_semantic_replay.py").read_bytes()
    (snapshot / "evaluate_router_v3_semantic_replay.py").write_bytes(semantic_eval)
    snapshot_sha["evaluate_router_v3_semantic_replay.py"] = sha(semantic_eval)

    code_hashes = {name: sha((HERE / name).read_bytes()) for name in H3_CODE_FILES}
    evaluation_hashes = {
        name: sha((HERE / name).read_bytes()) for name in
        ("router_plan_cases_tuning_v2.json", "baseline_reconciled_v3_20261008.json")}
    manifest = {
        "schema_version": "codex_subscription_file_replay.v1", "experiment_mode": "codex_subscription_file_replay",
        "run_id": RUN_ID, "supersedes_unreceipted_run_ids": SUPERSEDED,
        "lineage": {"parent_run_id": R4_RUN_ID, "parent_manifest_sha256": R4_MANIFEST_SHA256,
                    "h2_parent_run_id": h2_manifest["run_id"], "h2_parent_manifest_sha256": H2_MANIFEST_SHA256,
                    "reason": "snapshot-isolated H2 historical replay and complete explicit H3 quality gates"},
        "split": "tuning", "case_count": 20, "case_order": h2_manifest["case_order"],
        "tokenizer_version": h2_manifest["tokenizer_version"], "locale": h2_manifest["locale"],
        "requested_effort": h2_manifest["requested_effort"], "requested_models": h2_manifest["requested_models"],
        "supports_json_schema": h2_manifest["supports_json_schema"],
        "transport": "Codex subscription file replay; r5 implementation freeze only, no sampling",
        "validation_policy": VALIDATION_POLICY, "files_sha256": files_sha, "code_files_sha256": code_hashes,
        "historical_h2_runtime_sha256": snapshot_sha, "historical_h2_manifest_sha256": H2_MANIFEST_SHA256,
        "r4_parent_manifest_sha256": R4_MANIFEST_SHA256, "evaluation_data_sha256": evaluation_hashes,
        "gold_included": False, "receipts_included": False,
    }
    (RUN_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validate_frozen()


def validate_frozen(run_dir: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir or RUN_DIR)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("run_id") != RUN_ID:
        raise ValueError("H3 r5 run identity mismatch")
    lineage = manifest.get("lineage") or {}
    if (lineage.get("parent_run_id") != R4_RUN_ID or lineage.get("parent_manifest_sha256") != R4_MANIFEST_SHA256
            or lineage.get("h2_parent_manifest_sha256") != H2_MANIFEST_SHA256):
        raise ValueError("H3 r5 direct/ultimate lineage mismatch")
    if sha((R4_RUN_DIR / "manifest.json").read_bytes()) != R4_MANIFEST_SHA256:
        raise ValueError("pinned r4 parent manifest changed")
    if sha((SOURCE_RUN / "manifest.json").read_bytes()) != H2_MANIFEST_SHA256:
        raise ValueError("pinned H2 parent manifest changed")
    for relative, expected in manifest["files_sha256"].items():
        if sha((run_dir / relative).read_bytes()) != expected:
            raise ValueError(f"H3 r5 frozen input hash mismatch: {relative}")
    for name, expected in manifest["code_files_sha256"].items():
        if sha((HERE / name).read_bytes()) != expected:
            raise ValueError(f"H3 r5 code hash mismatch: {name}")
    for name, expected in manifest["historical_h2_runtime_sha256"].items():
        if sha((run_dir / "historical_h2_runtime" / name).read_bytes()) != expected:
            raise ValueError(f"historical H2 runtime hash mismatch: {name}")
    source_inputs = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    if (run_dir / "inputs/input_manifest.jsonl").read_bytes() != source_inputs:
        raise ValueError("H3 r5 inputs differ from the frozen H2 input bytes")
    return {"status": "h3_r5_frozen_inputs_valid", "run_id": RUN_ID, "case_count": 20,
            "parent_manifest_sha256": H2_MANIFEST_SHA256,
            "direct_parent_manifest_sha256": R4_MANIFEST_SHA256,
            "receipts_present": (run_dir / "receipts.jsonl").exists()}


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, indent=2))
