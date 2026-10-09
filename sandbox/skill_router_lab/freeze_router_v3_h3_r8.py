"""Create/validate H3 r8 with frozen evaluation resources."""
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
R7_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r7_20261009"
RUN_DIR = HERE / "router_subscription_run_tuning_v3_h3_r8_20261009"
RUN_ID = "router-v3-h3-tuning-20261009-r8"
R7_RUN_ID = "router-v3-h3-tuning-20261009-r7"
R7_MANIFEST_SHA256 = "53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417"
H2_MANIFEST_SHA256 = "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
SUPERSEDED = ["router-v3-h3-tuning-20261008-r1", "router-v3-h3-tuning-20261008-r2",
              "router-v3-h3-tuning-20261008-r3", "router-v3-h3-tuning-20261008-r4",
              "router-v3-h3-tuning-20261009-r5", "router-v3-h3-tuning-20261009-r6", R7_RUN_ID]
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
                 "router_subscription_lifecycle_h3_r8.py", "evaluate_router_v3_h3_replay_r8.py",
                 "evaluate_router_v3_h3_semantic_replay_r8.py", "router_subscription_lifecycle_v3.py",
                 "router_draft.py", "router_subscription_eval_v3.py", "freeze_router_v3_h3_r8.py",
                 "replay_router_h2_snapshot_r8.py", "evaluate_router_v3_semantic_replay.py",
                 "evaluate_router_v2_tuning.py", "router_request_envelope_v3.py", "router_subscription_eval.py"]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def freeze() -> dict[str, Any]:
    if RUN_DIR.exists():
        raise FileExistsError(f"reserved H3 r8 directory already exists: {RUN_DIR}")
    h2_manifest_bytes = (SOURCE_RUN / "manifest.json").read_bytes()
    h2_manifest = json.loads(h2_manifest_bytes)
    if h2_manifest.get("run_id") != "router-v3-h2-tuning-20261008-r2" or sha(h2_manifest_bytes) != H2_MANIFEST_SHA256:
        raise ValueError("H3 r8 is not based on the pinned H2 r2 freeze")
    r7_bytes = (R7_RUN_DIR / "manifest.json").read_bytes()
    r7_manifest = json.loads(r7_bytes)
    if r7_manifest.get("run_id") != R7_RUN_ID or sha(r7_bytes) != R7_MANIFEST_SHA256:
        raise ValueError("H3 r7 parent identity/hash mismatch")
    if (R7_RUN_DIR / "receipts.jsonl").exists():
        raise ValueError("r7 parent unexpectedly contains receipts")
    inputs = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    source_manifests = (SOURCE_RUN / "source_manifests.json").read_bytes()
    schema = (SOURCE_RUN / "schema.json").read_bytes()
    if json.loads(schema) != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("H3 r8 schema differs from frozen H2")
    h2_parent_dir = HERE / "router_subscription_run_tuning_v3_h2_r1_20261008"
    v2_dir = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
    h2_parent_bytes = (h2_parent_dir / "manifest.json").read_bytes()
    v2_manifest_bytes = (v2_dir / "manifest.json").read_bytes()
    v2_manifest = json.loads(v2_manifest_bytes)
    evaluation_files = {
        "evaluation/router_plan_cases_tuning_v2.json": (HERE / "router_plan_cases_tuning_v2.json").read_bytes(),
        "evaluation/baseline_reconciled_v3_20261008.json": (HERE / "baseline_reconciled_v3_20261008.json").read_bytes(),
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
    r4_snapshot = R7_RUN_DIR / "historical_h2_runtime"
    for name, expected in h2_manifest["code_files_sha256"].items():
        content = (r4_snapshot / name).read_bytes()
        if sha(content) != expected:
            raise ValueError(f"r7's H2 runtime snapshot differs from original frozen code: {name}")
        (snapshot / name).write_bytes(content)
        snapshot_sha[name] = expected
    # The semantic evaluator was not part of the H2 runner's runtime inventory;
    # freeze its exact replay implementation in this revision for reproduction.
    semantic_eval = (HERE / "evaluate_router_v3_semantic_replay.py").read_bytes()
    (snapshot / "evaluate_router_v3_semantic_replay.py").write_bytes(semantic_eval)
    snapshot_sha["evaluate_router_v3_semantic_replay.py"] = sha(semantic_eval)

    code_hashes = {name: sha((HERE / name).read_bytes()) for name in H3_CODE_FILES}
    evaluation_hashes = {relative: sha(payload) for relative, payload in evaluation_files.items()}
    manifest = {
        "schema_version": "codex_subscription_file_replay.v1", "experiment_mode": "codex_subscription_file_replay",
        "run_id": RUN_ID, "supersedes_unreceipted_run_ids": SUPERSEDED,
        "lineage": {"parent_run_id": R7_RUN_ID, "parent_manifest_sha256": R7_MANIFEST_SHA256,
                    "h2_parent_run_id": h2_manifest["run_id"], "h2_parent_manifest_sha256": H2_MANIFEST_SHA256,
                    "reason": "r8 binds H2 historical replay and all evaluation resources to frozen copies/hashes"},
        "split": "tuning", "case_count": 20, "case_order": h2_manifest["case_order"],
        "tokenizer_version": h2_manifest["tokenizer_version"], "locale": h2_manifest["locale"],
        "requested_effort": h2_manifest["requested_effort"], "requested_models": h2_manifest["requested_models"],
        "supports_json_schema": h2_manifest["supports_json_schema"],
        "transport": "Codex subscription file replay; r8 implementation freeze only, no sampling",
        "validation_policy": VALIDATION_POLICY, "files_sha256": files_sha, "code_files_sha256": code_hashes,
        "historical_h2_runtime_sha256": snapshot_sha, "historical_h2_manifest_sha256": H2_MANIFEST_SHA256,
        "r7_parent_manifest_sha256": R7_MANIFEST_SHA256, "evaluation_data_sha256": evaluation_hashes,
        "external_resources": {
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
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("run_id") != RUN_ID:
        raise ValueError("H3 r8 run identity mismatch")
    lineage = manifest.get("lineage") or {}
    if (lineage.get("parent_run_id") != R7_RUN_ID or lineage.get("parent_manifest_sha256") != R7_MANIFEST_SHA256
            or lineage.get("h2_parent_manifest_sha256") != H2_MANIFEST_SHA256):
        raise ValueError("H3 r8 direct/ultimate lineage mismatch")
    if sha((R7_RUN_DIR / "manifest.json").read_bytes()) != R7_MANIFEST_SHA256:
        raise ValueError("pinned r7 parent manifest changed")
    for relative, expected in manifest["files_sha256"].items():
        if sha((run_dir / relative).read_bytes()) != expected:
            raise ValueError(f"H3 r8 frozen input hash mismatch: {relative}")
    for name, expected in manifest["code_files_sha256"].items():
        if sha((HERE / name).read_bytes()) != expected:
            raise ValueError(f"H3 r8 code hash mismatch: {name}")
    for name, expected in manifest["historical_h2_runtime_sha256"].items():
        if sha((run_dir / "historical_h2_runtime" / name).read_bytes()) != expected:
            raise ValueError(f"historical H2 runtime hash mismatch: {name}")
        if name in manifest["code_files_sha256"] and name != "router_subscription_lifecycle_v3.py":
            if manifest["code_files_sha256"][name] != expected:
                raise ValueError(f"H3 runtime dependency is not the pinned historical implementation: {name}")
    ext = manifest.get("external_resources", {})
    h2 = ext.get("h2_run", {})
    h2_manifest_path = run_dir / "evaluation/h2_manifest.json"
    h2_manifest_bytes = h2_manifest_path.read_bytes()
    if (sha(h2_manifest_bytes) != H2_MANIFEST_SHA256 or sha((SOURCE_RUN / "manifest.json").read_bytes()) != H2_MANIFEST_SHA256
            or sha(h2_manifest_bytes) != h2.get("manifest_sha256")
            or json.loads(h2_manifest_bytes).get("run_id") != h2.get("run_id")):
        raise ValueError("pinned H2 manifest content/identity mismatch")
    source_h2_manifest = json.loads(h2_manifest_bytes)
    if source_h2_manifest.get("files_sha256") != h2.get("files_sha256"):
        raise ValueError("pinned H2 source inventory mismatch")
    for relative, expected in h2.get("files_sha256", {}).items():
        if sha((SOURCE_RUN / relative).read_bytes()) != expected:
            raise ValueError(f"pinned H2 source resource drift: {relative}")
    if sha((SOURCE_RUN / "receipts.jsonl").read_bytes()) != h2.get("receipts_sha256"):
        raise ValueError("pinned H2 receipts drift")
    h2_parent_copy = run_dir / "evaluation/h2_parent_manifest.json"
    h2_parent_info = ext.get("h2_parent", {})
    if sha(h2_parent_copy.read_bytes()) != h2_parent_info.get("manifest_sha256") or sha((HERE / "router_subscription_run_tuning_v3_h2_r1_20261008/manifest.json").read_bytes()) != h2_parent_info.get("manifest_sha256"):
        raise ValueError("pinned H2 parent manifest drift")
    v2_info = ext.get("v2_source", {})
    v2_manifest_path = run_dir / "evaluation/v2_manifest.json"
    v2_manifest_bytes = v2_manifest_path.read_bytes()
    v2_manifest = json.loads(v2_manifest_bytes)
    v2_source = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
    source_v2_manifest_bytes = (v2_source / "manifest.json").read_bytes()
    if (sha(v2_manifest_bytes) != v2_info.get("manifest_sha256")
            or sha(source_v2_manifest_bytes) != v2_info.get("manifest_sha256")
            or source_v2_manifest_bytes != v2_manifest_bytes
            or v2_manifest.get("run_id") != v2_info.get("run_id")
            or v2_manifest.get("files_sha256") != v2_info.get("files_sha256")):
        raise ValueError("pinned v2 manifest content/identity drift")
    for relative, expected in v2_info.get("files_sha256", {}).items():
        if sha((v2_source / relative).read_bytes()) != expected:
            raise ValueError(f"pinned v2 source resource drift: {relative}")
    source_inputs = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    if ((run_dir / "inputs/input_manifest.jsonl").read_bytes() != source_inputs
            or (run_dir / "source_manifests.json").read_bytes() != (SOURCE_RUN / "source_manifests.json").read_bytes()
            or (run_dir / "schema.json").read_bytes() != (SOURCE_RUN / "schema.json").read_bytes()):
        raise ValueError("H3 r8 inputs/source manifests/schema differ from frozen H2 bytes")
    if ((run_dir / "prompts/classifier.txt").read_bytes() != (SOURCE_RUN / "prompts/classifier.txt").read_bytes()
            or (run_dir / "prompts/parser.txt").read_bytes() != PARSER_V3_SYSTEM.encode("utf-8")
            or (run_dir / "prompts/repair.txt").read_bytes() != REPAIR_V3_SYSTEM.encode("utf-8")):
        raise ValueError("H3 r8 prompt bytes differ from frozen policy/source")
    rows = [json.loads(line) for line in source_inputs.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != 20 or [row["case_id"] for row in rows] != manifest["case_order"]:
        raise ValueError("H3 r8 frozen input count/order mismatch")
    if (run_dir / "gold.json").exists() or any({"expected", "gold", "split"} & set(row) for row in rows):
        raise ValueError("gold leaked into H3 r8 model inputs")
    for name, expected in manifest["evaluation_data_sha256"].items():
        if sha((run_dir / name).read_bytes()) != expected:
            raise ValueError(f"H3 r8 frozen evaluation resource hash mismatch: {name}")
    snapshot_hash = sha(json.dumps(manifest["historical_h2_runtime_sha256"], sort_keys=True, separators=(",", ":")).encode())
    return {"status": "h3_r8_frozen_inputs_valid", "run_id": RUN_ID, "manifest_sha256": sha(manifest_bytes), "case_count": 20,
            "parent_manifest_sha256": H2_MANIFEST_SHA256,
            "direct_parent_run_id": R7_RUN_ID, "direct_parent_manifest_sha256": R7_MANIFEST_SHA256,
            "snapshot_identity": "historical_h2_runtime", "snapshot_inventory_sha256": snapshot_hash,
            "v2_manifest_sha256": v2_info["manifest_sha256"],
            "receipts_present": (run_dir / "receipts.jsonl").exists()}


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False, indent=2))
