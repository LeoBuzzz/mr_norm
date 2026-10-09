"""Run the historical H2 replay using only its frozen executable snapshot.

The launcher deliberately binds snapshot code to the original frozen H2/v2
artifact directories. It never imports the mutable working-tree replay stack.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
R4_DIR = HERE / "router_subscription_run_tuning_v3_h3_r4_20261008"
R5_DIR = HERE / "router_subscription_run_tuning_v3_h3_r5_20261009"
H2_DIR = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
H2_V1_DIR = HERE / "router_subscription_run_tuning_v3_h2_r1_20261008"
V2_DIR = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
H2_MANIFEST_SHA256 = "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
H2_RECEIPTS_SHA256 = "184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160"

_CHILD = r'''
import json, sys
from pathlib import Path
snapshot, h2_dir, h2_parent, v2_dir, artifacts = map(Path, sys.argv[1:6])
sys.path.insert(0, str(snapshot))
import freeze_router_v3_run as frozen
frozen.SOURCE_RUN = v2_dir
frozen.PARENT_RUN_DIR = h2_parent
frozen.RUN_DIR = h2_dir
import evaluate_router_v2_tuning as v2_eval
v2_eval.RUN_DIR = v2_dir
v2_eval.GOLD_PATH = artifacts / "router_plan_cases_tuning_v2.json"
import evaluate_router_v3_file_replay as tech
tech.RUN_DIR = h2_dir
import evaluate_router_v3_semantic_replay as semantic
semantic.RUN_DIR = h2_dir
semantic.HERE = artifacts
semantic.GOLD_PATH = artifacts / "router_plan_cases_tuning_v2.json"
technical = tech.replay_v3(h2_dir)
score = semantic.semantic_replay(h2_dir)
print(json.dumps({"technical": technical, "semantic": score}, ensure_ascii=False))
'''


class SnapshotReplayError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_snapshot_bindings(snapshot_dir: Path | None = None) -> dict[str, Any]:
    snapshot_dir = Path(snapshot_dir or (R5_DIR / "historical_h2_runtime"))
    r5_manifest_path = R5_DIR / "manifest.json"
    if not r5_manifest_path.is_file():
        raise SnapshotReplayError("r5 freeze is required to launch the historical H2 snapshot")
    r5_manifest = json.loads(r5_manifest_path.read_text(encoding="utf-8"))
    if (r5_manifest.get("run_id") != "router-v3-h3-tuning-20261009-r5"
            or r5_manifest.get("lineage", {}).get("h2_parent_manifest_sha256") != H2_MANIFEST_SHA256):
        raise SnapshotReplayError("r5 identity does not pin the original H2 run")
    h2_bytes = (H2_DIR / "manifest.json").read_bytes()
    if _sha(H2_DIR / "manifest.json") != H2_MANIFEST_SHA256:
        raise SnapshotReplayError("original H2 manifest identity/hash mismatch")
    if _sha(H2_DIR / "receipts.jsonl") != H2_RECEIPTS_SHA256:
        raise SnapshotReplayError("original H2 receipts hash mismatch")
    h2_manifest = json.loads(h2_bytes)
    snapshot_hashes = h2_manifest.get("code_files_sha256") or {}
    if not snapshot_hashes:
        raise SnapshotReplayError("H2 manifest has no frozen code inventory")
    for name, expected in snapshot_hashes.items():
        target = snapshot_dir / name
        if not target.is_file() or _sha(target) != expected:
            raise SnapshotReplayError(f"immutable H2 snapshot code hash mismatch: {name}")
    auxiliary = r5_manifest.get("code_files_sha256", {}).get("evaluate_router_v3_semantic_replay.py")
    semantic_snapshot = snapshot_dir / "evaluate_router_v3_semantic_replay.py"
    if not auxiliary or not semantic_snapshot.is_file() or _sha(semantic_snapshot) != auxiliary:
        raise SnapshotReplayError("frozen H2 semantic replay module hash mismatch")
    v2_manifest = json.loads((V2_DIR / "manifest.json").read_text(encoding="utf-8"))
    if v2_manifest.get("run_id") != "router-v2-tuning-20261007":
        raise SnapshotReplayError("original frozen v2 source run identity mismatch")
    return {"snapshot_dir": str(snapshot_dir), "h2_manifest_sha256": H2_MANIFEST_SHA256,
            "h2_receipts_sha256": H2_RECEIPTS_SHA256,
            "v2_manifest_sha256": _sha(V2_DIR / "manifest.json"),
            "code_file_count": len(snapshot_hashes) + 1}


def replay_h2_snapshot(run_dir: Path | None = None, snapshot_dir: Path | None = None) -> dict[str, Any]:
    bindings = validate_snapshot_bindings(snapshot_dir)
    run_dir = Path(run_dir or H2_DIR)
    snapshot_dir = Path(bindings["snapshot_dir"])
    payload = subprocess.run(
        [sys.executable, "-c", _CHILD, str(snapshot_dir), str(run_dir), str(H2_V1_DIR), str(V2_DIR), str(HERE)],
        cwd=str(snapshot_dir), capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if payload.returncode:
        detail = payload.stderr.strip() or payload.stdout.strip()
        raise SnapshotReplayError(f"snapshot H2 replay rejected: {detail}")
    try:
        result = json.loads(payload.stdout)
    except json.JSONDecodeError as exc:
        raise SnapshotReplayError("snapshot H2 replay returned malformed output") from exc
    technical, semantic = result["technical"], result["semantic"]
    if (technical.get("first_technical_valid"), technical.get("final_technical_valid")) != (15, 20):
        raise SnapshotReplayError("historical H2 technical metrics drifted from 15/20 -> 20/20")
    if semantic.get("baseline_reconciled", {}).get("matched") != 11 or semantic.get("metrics", {}).get("semantic_plan", {}).get("matched") != 5:
        raise SnapshotReplayError("historical H2 semantic metrics drifted from B=11/H2=5")
    return {"bindings": bindings, **result}


if __name__ == "__main__":
    result = replay_h2_snapshot()
    print(json.dumps({"bindings": result["bindings"],
                      "technical": {key: result["technical"][key] for key in
                                    ("first_technical_valid", "final_technical_valid", "complete")},
                      "baseline": result["semantic"]["baseline_reconciled"],
                      "semantic_matched": result["semantic"]["metrics"]["semantic_plan"]["matched"]},
                     ensure_ascii=False, indent=2))
