from __future__ import annotations
import hashlib, json, shutil, subprocess, sys
import pytest
from pathlib import Path
from freeze_router_v3_h5_r4 import RUN_DIR
from router_subscription_eval import ReplayInputError
from prepare_router_subscription_h5_r4 import H5SubscriptionCoordinatorR4
from router_subscription_lifecycle_h5_r4 import FileSubscriptionRunnerH5R4

HERE=Path(__file__).resolve().parent
LAUNCHER=HERE/"release_router_h5_r4.py"
def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def _args(launcher,run,*,launcher_sha=None):
    return [sys.executable,str(launcher),"--anchor-launcher-sha256",launcher_sha or _sha(LAUNCHER),
            "--anchor-lock-sha256",_sha(RUN_DIR/"release_lock.json"),
            "--anchor-manifest-sha256",_sha(RUN_DIR/"manifest.json"),"--action","prepare","status"]
def test_release_launcher_requires_external_anchor_and_is_stdlib_only():
    source=LAUNCHER.read_text(encoding="utf-8")
    assert "import argparse, hashlib, json, os, runpy, sys" in source
    p=subprocess.run([sys.executable,str(LAUNCHER),"--action","prepare"],capture_output=True,text=True)
    assert p.returncode != 0
    args=_args(LAUNCHER,RUN_DIR);args[args.index("--anchor-launcher-sha256")+1]="0"*64
    p=subprocess.run(args,capture_output=True,text=True)
    assert p.returncode != 0 and "launcher anchor mismatch" in p.stderr

def test_changed_manifest_rejected_before_candidate_import(tmp_path):
    lab=tmp_path/"lab";lab.mkdir();run=lab/RUN_DIR.name;shutil.copytree(RUN_DIR,run)
    launcher=lab/LAUNCHER.name;shutil.copy2(LAUNCHER,launcher)
    manifest=run/"manifest.json";manifest.write_bytes(manifest.read_bytes()+b" ")
    p=subprocess.run(_args(launcher,run),cwd=lab,capture_output=True,text=True,encoding="utf-8")
    assert p.returncode != 0 and "manifest anchor mismatch" in p.stderr
    assert not (lab/"receipts.jsonl").exists()

def test_coherent_candidate_change_is_rejected_before_import_side_effect(tmp_path):
    lab=tmp_path/"lab";lab.mkdir();run=lab/RUN_DIR.name;shutil.copytree(RUN_DIR,run)
    launcher=lab/LAUNCHER.name;shutil.copy2(LAUNCHER,launcher)
    marker=lab/"candidate_imported.marker"
    candidate=lab/"freeze_router_v3_h5_r4.py"
    candidate.write_text("from pathlib import Path\nPath("+repr(str(marker))+ ").write_text('imported')\n",encoding="utf-8")
    p=subprocess.run(_args(launcher,run),cwd=lab,capture_output=True,text=True,encoding="utf-8")
    assert p.returncode != 0 and "closure inventory mismatch" in p.stderr
    assert not marker.exists()


def test_candidate_entrypoints_fail_without_verified_host_context(monkeypatch):
    monkeypatch.delenv("ROUTER_H5_R4_PREFLIGHT_MANIFEST_SHA256",raising=False)
    monkeypatch.delenv("ROUTER_H5_R4_SYNTHETIC_TEST_MODE",raising=False)
    with pytest.raises(ReplayInputError,match="verified-launcher host context"):
        H5SubscriptionCoordinatorR4(RUN_DIR)
    with pytest.raises(ReplayInputError,match="verified-launcher host context"):
        FileSubscriptionRunnerH5R4(RUN_DIR,lambda envelope:"{}")
