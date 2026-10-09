"""R8-bound deterministic historical H2 replay; no mutable resource loading."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from freeze_router_v3_h3_r8 import HERE, RUN_DIR, validate_frozen

H2_DIR = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
H2_PARENT_DIR = HERE / "router_subscription_run_tuning_v3_h2_r1_20261008"
V2_DIR = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"

_CHILD = r'''
import json, sys
from pathlib import Path
snapshot, h2_dir, h2_parent, v2_dir, evaluation = map(Path, sys.argv[1:6])
sys.path.insert(0, str(snapshot))
import freeze_router_v3_run as frozen
frozen.SOURCE_RUN = v2_dir
frozen.PARENT_RUN_DIR = h2_parent
frozen.RUN_DIR = h2_dir
import evaluate_router_v2_tuning as v2_eval
v2_eval.RUN_DIR = v2_dir
v2_eval.GOLD_PATH = evaluation / "router_plan_cases_tuning_v2.json"
import evaluate_router_v3_file_replay as tech
tech.RUN_DIR = h2_dir
import evaluate_router_v3_semantic_replay as semantic
semantic.RUN_DIR = h2_dir
semantic.HERE = evaluation
semantic.GOLD_PATH = evaluation / "router_plan_cases_tuning_v2.json"
semantic.BASELINE_PATH = evaluation / "baseline_reconciled_v3_20261008.json"
technical = tech.replay_v3(h2_dir)
score = semantic.semantic_replay(h2_dir)
print(json.dumps({"technical": technical, "semantic": score}, ensure_ascii=False))
'''


class SnapshotReplayError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_snapshot_bindings(run_dir: Path | None = None, *, snapshot_dir: Path | None = None,
                               v2_dir: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir or RUN_DIR)
    snapshot_dir = Path(snapshot_dir or (run_dir / "historical_h2_runtime"))
    v2_dir = Path(v2_dir or V2_DIR)
    try:
        validated = validate_frozen(run_dir)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        h2_manifest = json.loads((run_dir / "evaluation/h2_manifest.json").read_text(encoding="utf-8"))
        v2_manifest = json.loads((run_dir / "evaluation/v2_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SnapshotReplayError(f"r8 frozen resource validation failed: {exc}") from exc
    if snapshot_dir.resolve() != (run_dir / "historical_h2_runtime").resolve():
        raise SnapshotReplayError("snapshot substitution is not the r8-frozen H2 runtime")
    if v2_dir.resolve() != V2_DIR.resolve():
        raise SnapshotReplayError("v2 source directory substitution is not permitted")
    snapshot_hashes = manifest.get("historical_h2_runtime_sha256", {})
    if not snapshot_hashes:
        raise SnapshotReplayError("r8 manifest has no historical snapshot inventory")
    for name, expected in snapshot_hashes.items():
        target = snapshot_dir / name
        if not target.is_file() or _sha(target) != expected:
            raise SnapshotReplayError(f"r8 historical snapshot drift: {name}")
    evaluation_hashes = manifest.get("evaluation_data_sha256", {})
    gold_rel = "evaluation/router_plan_cases_tuning_v2.json"
    baseline_rel = "evaluation/baseline_reconciled_v3_20261008.json"
    if gold_rel not in evaluation_hashes or baseline_rel not in evaluation_hashes:
        raise SnapshotReplayError("r8 manifest does not pin gold and reconciled baseline")
    snapshot_identity = hashlib.sha256(json.dumps(snapshot_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest_sha = _sha(run_dir / "manifest.json")
    return {"r8_run_id": manifest["run_id"], "r8_manifest_sha256": manifest_sha,
            "r8_snapshot_identity": "historical_h2_runtime", "r8_snapshot_inventory_sha256": snapshot_identity,
            "snapshot_dir": str(snapshot_dir), "snapshot_file_count": len(snapshot_hashes),
            "h2_manifest_sha256": manifest["external_resources"]["h2_run"]["manifest_sha256"],
            "h2_receipts_sha256": manifest["external_resources"]["h2_run"]["receipts_sha256"],
            "v2_manifest_sha256": _sha(run_dir / "evaluation/v2_manifest.json"),
            "v2_source_manifest_sha256": manifest["external_resources"]["v2_source"]["manifest_sha256"],
            "gold_sha256": evaluation_hashes[gold_rel], "baseline_sha256": evaluation_hashes[baseline_rel],
            "code_file_count": len(manifest["code_files_sha256"]),
            "case_count": manifest["case_count"], "validated_status": validated["status"],
            "_manifest": manifest, "_h2_manifest": h2_manifest, "_v2_manifest": v2_manifest}


def replay_h2_snapshot(run_dir: Path | None = None, snapshot_dir: Path | None = None,
                       v2_dir: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir or RUN_DIR)
    bindings = validate_snapshot_bindings(run_dir, snapshot_dir=snapshot_dir, v2_dir=v2_dir)
    manifest = bindings.pop("_manifest")
    bindings.pop("_h2_manifest")
    bindings.pop("_v2_manifest")
    snapshot_dir = Path(bindings["snapshot_dir"])
    evaluation = run_dir / "evaluation"
    payload = subprocess.run(
        [sys.executable, "-c", _CHILD, str(snapshot_dir), str(H2_DIR), str(H2_PARENT_DIR), str(V2_DIR), str(evaluation)],
        cwd=str(snapshot_dir), capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if payload.returncode:
        raise SnapshotReplayError(f"snapshot H2 replay rejected: {payload.stderr.strip() or payload.stdout.strip()}")
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
