"""Create/validate H3 r9 with immutable scorer-resource bindings."""
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
from freeze_router_v3_h3_r8 import validate_frozen as validate_r8

HERE = Path(__file__).resolve().parent
SOURCE_RUN = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
R8_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r8_20261009"
RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r9_20261009"
RUN_ID = "router-v3-h3-tuning-20261009-r9"
R8_RUN_ID = "router-v3-h3-tuning-20261009-r8"
R8_MANIFEST_SHA256 = "ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50"
R7_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r7_20261009"
R7_MANIFEST_SHA256 = "53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417"
H2_MANIFEST_SHA256 = "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
SUPERSEDED = ["router-v3-h3-tuning-20261008-r1", "router-v3-h3-tuning-20261008-r2",
              "router-v3-h3-tuning-20261008-r3", "router-v3-h3-tuning-20261008-r4",
              "router-v3-h3-tuning-20261009-r5", "router-v3-h3-tuning-20261009-r6",
              "router-v3-h3-tuning-20261009-r7", R8_RUN_ID]
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
                 "router_subscription_lifecycle_h3_r9.py", "evaluate_router_v3_h3_replay_r9.py",
                 "evaluate_router_v3_h3_semantic_replay_r9.py", "router_subscription_lifecycle_v3.py",
                 "router_draft.py", "router_subscription_eval_v3.py", "freeze_router_v3_h3_r9.py",
                 "replay_router_h2_snapshot_r9.py", "evaluate_router_v3_semantic_replay.py",
                 "evaluate_router_v2_tuning.py", "router_request_envelope_v3.py", "router_subscription_eval.py",
                 "freeze_router_v3_h3_r8.py", "replay_router_h2_snapshot_r8.py",
                 "evaluate_router_v3_h3_replay_r8.py", "evaluate_router_v3_h3_semantic_replay_r8.py",
                 "router_subscription_lifecycle_h3_r8.py"]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def freeze() -> dict[str, Any]:
    if RUN_DIR.exists():
        raise FileExistsError(f"reserved H3 r9 directory already exists: {RUN_DIR}")
    validate_r8(R8_RUN_DIR)
    r8_manifest_bytes = (R8_RUN_DIR / "manifest.json").read_bytes()
    if sha(r8_manifest_bytes) != R8_MANIFEST_SHA256:
        raise ValueError("H3 r8 parent identity/hash mismatch")
    r8_manifest = json.loads(r8_manifest_bytes)
    h2_manifest_bytes = (R8_RUN_DIR / "evaluation/h2_manifest.json").read_bytes()
    h2_manifest = json.loads(h2_manifest_bytes)
    if h2_manifest.get("run_id") != "router-v3-h2-tuning-20261008-r2" or sha(h2_manifest_bytes) != H2_MANIFEST_SHA256:
        raise ValueError("H3 r8 is not based on the pinned H2 r2 freeze")
    inputs = (R8_RUN_DIR / "inputs/input_manifest.jsonl").read_bytes()
    source_manifests = (R8_RUN_DIR / "source_manifests.json").read_bytes()
    schema = (R8_RUN_DIR / "schema.json").read_bytes()
    if json.loads(schema) != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("H3 r9 schema differs from frozen H2")
    h2_parent_dir = HERE / "router_subscription_run_tuning_v3_h2_r1_20261008"
    v2_dir = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
    h2_parent_bytes = (R8_RUN_DIR / "evaluation/h2_parent_manifest.json").read_bytes()
    v2_manifest_bytes = (R8_RUN_DIR / "evaluation/v2_manifest.json").read_bytes()
    v2_manifest = json.loads(v2_manifest_bytes)
    evaluation_files = {
        "evaluation/router_plan_cases_tuning_v2.json": (R8_RUN_DIR / "evaluation/router_plan_cases_tuning_v2.json").read_bytes(),
        "evaluation/baseline_reconciled_v3_20261008.json": (R8_RUN_DIR / "evaluation/baseline_reconciled_v3_20261008.json").read_bytes(),
        "evaluation/h2_manifest.json": h2_manifest_bytes,
        "evaluation/h2_parent_manifest.json": h2_parent_bytes,
        "evaluation/v2_manifest.json": v2_manifest_bytes,
    }
    RUN_DIR.mkdir(parents=True)
    frozen_files = {"inputs/input_manifest.jsonl": inputs, "source_manifests.json": source_manifests,
                    "schema.json": schema, **PROMPTS, **evaluation_files}
    for relative, content in frozen_files.items():
        target = RUN_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    files_sha = freeze_files(RUN_DIR, frozen_files)

    snapshot = RUN_DIR / "historical_h2_runtime"
    snapshot.mkdir()
    snapshot_sha: dict[str, str] = {}
    r4_snapshot = R8_RUN_DIR / "historical_h2_runtime"
    for name, expected in h2_manifest["code_files_sha256"].items():
        content = (r4_snapshot / name).read_bytes()
        if sha(content) != expected:
            raise ValueError(f"r8's H2 runtime snapshot differs from original frozen code: {name}")
        (snapshot / name).write_bytes(content)
        snapshot_sha[name] = expected
    # The semantic evaluator was not part of the H2 runner's runtime inventory;
    # freeze its exact replay implementation in this revision for reproduction.
    semantic_eval = (R8_RUN_DIR / "historical_h2_runtime/evaluate_router_v3_semantic_replay.py").read_bytes()
    (snapshot / "evaluate_router_v3_semantic_replay.py").write_bytes(semantic_eval)
    snapshot_sha["evaluate_router_v3_semantic_replay.py"] = sha(semantic_eval)

    code_hashes = {name: sha((HERE / name).read_bytes()) for name in H3_CODE_FILES}
    evaluation_hashes = {relative: sha(payload) for relative, payload in evaluation_files.items()}
    manifest = {
        "schema_version": "codex_subscription_file_replay.v1", "experiment_mode": "codex_subscription_file_replay",
        "run_id": RUN_ID, "supersedes_unreceipted_run_ids": SUPERSEDED,
        "lineage": {"parent_run_id": R8_RUN_ID, "parent_manifest_sha256": R8_MANIFEST_SHA256,
                    "h2_parent_run_id": h2_manifest["run_id"], "h2_parent_manifest_sha256": H2_MANIFEST_SHA256,
                    "reason": "r9 binds H3 scorer resources and H2 regressions to the validated R8 frozen lineage"},
        "split": "tuning", "case_count": 20, "case_order": h2_manifest["case_order"],
        "tokenizer_version": h2_manifest["tokenizer_version"], "locale": h2_manifest["locale"],
        "requested_effort": h2_manifest["requested_effort"], "requested_models": h2_manifest["requested_models"],
        "supports_json_schema": h2_manifest["supports_json_schema"],
        "transport": "Codex subscription file replay; r9 implementation freeze only, no sampling",
        "validation_policy": VALIDATION_POLICY, "files_sha256": files_sha, "code_files_sha256": code_hashes,
        "historical_h2_runtime_sha256": snapshot_sha, "historical_h2_manifest_sha256": H2_MANIFEST_SHA256,
        "r8_parent_manifest_sha256": R8_MANIFEST_SHA256, "evaluation_data_sha256": evaluation_hashes,
        "external_resources": {
            "r8_parent": {"run_id": r8_manifest["run_id"], "manifest_sha256": sha(r8_manifest_bytes)},
            "h2_run": {"run_id": h2_manifest["run_id"], "manifest_sha256": sha(h2_manifest_bytes),
                       "receipts_sha256": sha((SOURCE_RUN / "receipts.jsonl").read_bytes()),
                       "files_sha256": h2_manifest["files_sha256"]},
            "h2_parent": {"run_id": json.loads(h2_parent_bytes)["run_id"], "manifest_sha256": sha(h2_parent_bytes)},
            "v2_source": {"run_id": v2_manifest["run_id"], "manifest_sha256": sha(v2_manifest_bytes),
                          "files_sha256": v2_manifest["files_sha256"]},
        },
        "gold_included": False, "receipts_included": False,
    }
    (RUN_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validate_frozen()


def validate_frozen(run_dir: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir or RUN_DIR)
    validate_r8(R8_RUN_DIR)
    r8_bytes = (R8_RUN_DIR / "manifest.json").read_bytes()
    if sha(r8_bytes) != R8_MANIFEST_SHA256:
        raise ValueError("pinned r8 parent manifest changed")
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("run_id") != RUN_ID:
        raise ValueError("H3 r9 run identity mismatch")
    lineage = manifest.get("lineage") or {}
    if (lineage.get("parent_run_id") != R8_RUN_ID or lineage.get("parent_manifest_sha256") != R8_MANIFEST_SHA256
            or lineage.get("h2_parent_manifest_sha256") != H2_MANIFEST_SHA256):
        raise ValueError("H3 r9 direct/ultimate lineage mismatch")
    parent_resource = manifest.get("external_resources", {}).get("r8_parent", {})
    if parent_resource.get("run_id") != R8_RUN_ID or parent_resource.get("manifest_sha256") != R8_MANIFEST_SHA256:
        raise ValueError("R9 manifest does not pin exact R8 parent")
    for relative, expected in manifest["files_sha256"].items():
        if sha((run_dir / relative).read_bytes()) != expected:
            raise ValueError(f"H3 r9 frozen input hash mismatch: {relative}")
    for name, expected in manifest["code_files_sha256"].items():
        if sha((HERE / name).read_bytes()) != expected:
            raise ValueError(f"H3 r9 code hash mismatch: {name}")
    for name, expected in manifest["historical_h2_runtime_sha256"].items():
        if sha((run_dir / "historical_h2_runtime" / name).read_bytes()) != expected:
            raise ValueError(f"historical H2 runtime hash mismatch: {name}")
        if name in manifest["code_files_sha256"] and name != "router_subscription_lifecycle_v3.py":
            if manifest["code_files_sha256"][name] != expected:
                raise ValueError(f"H3 runtime dependency is not the pinned historical implementation: {name}")
    rows_bytes = (run_dir / "inputs/input_manifest.jsonl").read_bytes()
    if (rows_bytes != (R8_RUN_DIR / "inputs/input_manifest.jsonl").read_bytes()
            or (run_dir / "source_manifests.json").read_bytes() != (R8_RUN_DIR / "source_manifests.json").read_bytes()
            or (run_dir / "schema.json").read_bytes() != (R8_RUN_DIR / "schema.json").read_bytes()):
        raise ValueError("H3 r9 model inputs/source/schema differ from pinned R8 bytes")
    for relative, expected in manifest["evaluation_data_sha256"].items():
        if sha((run_dir / relative).read_bytes()) != expected:
            raise ValueError(f"H3 r9 evaluation resource hash mismatch: {relative}")
        parent_relative = relative
        if relative.startswith("evaluation/") and sha((R8_RUN_DIR / parent_relative).read_bytes()) != expected:
            raise ValueError(f"H3 r9 evaluation resource differs from pinned R8: {relative}")
    rows = [json.loads(line) for line in rows_bytes.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != 20 or [row["case_id"] for row in rows] != manifest["case_order"]:
        raise ValueError("H3 r9 frozen input count/order mismatch")
    if (run_dir / "gold.json").exists() or any({"expected", "gold", "split"} & set(row) for row in rows):
        raise ValueError("gold leaked into H3 r9 model inputs")
    snapshot_hash = sha(json.dumps(manifest["historical_h2_runtime_sha256"], sort_keys=True, separators=(",", ":")).encode())
    return {"status": "h3_r9_frozen_inputs_valid", "run_id": RUN_ID, "manifest_sha256": sha(manifest_bytes), "case_count": 20,
            "parent_manifest_sha256": H2_MANIFEST_SHA256,
            "direct_parent_run_id": R8_RUN_ID, "direct_parent_manifest_sha256": R8_MANIFEST_SHA256,
            "snapshot_identity": "historical_h2_runtime", "snapshot_inventory_sha256": snapshot_hash,
            "v2_manifest_sha256": manifest["external_resources"]["v2_source"]["manifest_sha256"],
            "receipts_present": (run_dir / "receipts.jsonl").exists()}


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, indent=2))
