from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from freeze_router_v3_h3_r8 import RUN_DIR, validate_frozen
from replay_router_h2_snapshot_r8 import SnapshotReplayError, replay_h2_snapshot


def _copy_frozen(tmp_path: Path) -> Path:
    target = tmp_path / "r8-copy"
    shutil.copytree(RUN_DIR, target)
    return target


@pytest.mark.parametrize("relative", [
    "evaluation/router_plan_cases_tuning_v2.json",
    "evaluation/baseline_reconciled_v3_20261008.json",
    "evaluation/v2_manifest.json",
    "evaluation/h2_manifest.json",
    "historical_h2_runtime/router_v2_runtime.py",
])
def test_r8_replay_rejects_frozen_resource_drift(tmp_path, relative):
    run_dir = _copy_frozen(tmp_path)
    target = run_dir / relative
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises((SnapshotReplayError, ValueError), match="hash|drift|resource|manifest"):
        replay_h2_snapshot(run_dir=run_dir)


def test_r8_success_evidence_explicitly_identifies_all_bound_resources():
    result = replay_h2_snapshot()
    bindings = result["bindings"]
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert bindings["r8_run_id"] == manifest["run_id"]
    assert bindings["r8_manifest_sha256"]
    assert bindings["r8_snapshot_identity"] == "historical_h2_runtime"
    assert bindings["r8_snapshot_inventory_sha256"]
    assert bindings["v2_manifest_sha256"] == manifest["external_resources"]["v2_source"]["manifest_sha256"]
    assert bindings["gold_sha256"] == manifest["evaluation_data_sha256"]["evaluation/router_plan_cases_tuning_v2.json"]
    assert bindings["baseline_sha256"] == manifest["evaluation_data_sha256"]["evaluation/baseline_reconciled_v3_20261008.json"]


def test_r8_launcher_rejects_snapshot_directory_substitution(tmp_path):
    replacement = tmp_path / "substitute"
    shutil.copytree(RUN_DIR / "historical_h2_runtime", replacement)
    with pytest.raises(SnapshotReplayError, match="substitution"):
        replay_h2_snapshot(snapshot_dir=replacement)


def test_r8_frozen_manifest_validates_from_its_versioned_run_directory():
    result = validate_frozen()
    assert result["status"] == "h3_r8_frozen_inputs_valid"
    assert result["run_id"] == "router-v3-h3-tuning-20261009-r8"
    assert result["manifest_sha256"] == replay_h2_snapshot()["bindings"]["r8_manifest_sha256"]
