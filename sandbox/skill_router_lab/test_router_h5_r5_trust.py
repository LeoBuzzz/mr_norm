from __future__ import annotations
import hashlib, json, shutil, subprocess, sys
import pytest
from pathlib import Path
from freeze_router_v3_h5_r5 import RUN_DIR
from router_subscription_eval import ReplayInputError
from prepare_router_subscription_h5_r5 import H5SubscriptionCoordinatorR5
from router_subscription_lifecycle_h5_r5 import FileSubscriptionRunnerH5R5

HERE=Path(__file__).resolve().parent
LAUNCHER=HERE/"release_router_h5_r5.py"
def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def _args(launcher,run,*,launcher_sha=None):
    return [sys.executable,str(launcher),"--anchor-launcher-sha256",launcher_sha or _sha(LAUNCHER),
            "--anchor-lock-sha256",_sha(RUN_DIR/"release_lock.json"),
            "--anchor-manifest-sha256",_sha(RUN_DIR/"manifest.json"),"--action","prepare","status"]
def _copy_closure(lab):
    lock=json.loads((RUN_DIR/"release_lock.json").read_text(encoding="utf-8"))
    for rel in lock["executable_closure_sha256"]:
        source=HERE/rel;target=lab/rel
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
def test_release_launcher_requires_external_anchor_and_is_stdlib_only():
    source=LAUNCHER.read_text(encoding="utf-8")
    assert "import argparse, ast, hashlib, json, os, runpy, sys" in source
    p=subprocess.run([sys.executable,str(LAUNCHER),"--action","prepare"],capture_output=True,text=True)
    assert p.returncode != 0
    args=_args(LAUNCHER,RUN_DIR);args[args.index("--anchor-launcher-sha256")+1]="0"*64
    p=subprocess.run(args,capture_output=True,text=True)
    assert p.returncode != 0 and "launcher anchor mismatch" in p.stderr

def test_changed_manifest_rejected_before_candidate_import(tmp_path):
    lab=tmp_path/"lab";lab.mkdir();run=lab/RUN_DIR.name;shutil.copytree(RUN_DIR,run)
    _copy_closure(lab)
    launcher=lab/LAUNCHER.name;shutil.copy2(LAUNCHER,launcher)
    manifest=run/"manifest.json";manifest.write_bytes(manifest.read_bytes()+b" ")
    p=subprocess.run(_args(launcher,run),cwd=lab,capture_output=True,text=True,encoding="utf-8")
    assert p.returncode != 0 and "manifest anchor mismatch" in p.stderr
    assert not (lab/"receipts.jsonl").exists()

def test_coherent_candidate_change_is_rejected_before_import_side_effect(tmp_path):
    lab=tmp_path/"lab";lab.mkdir();run=lab/RUN_DIR.name;shutil.copytree(RUN_DIR,run)
    _copy_closure(lab)
    launcher=lab/LAUNCHER.name;shutil.copy2(LAUNCHER,launcher)
    marker=lab/"candidate_imported.marker"
    candidate=lab/"freeze_router_v3_h5_r5.py"
    candidate.write_text("from pathlib import Path\nPath("+repr(str(marker))+ ").write_text('imported')\n",encoding="utf-8")
    p=subprocess.run(_args(launcher,run),cwd=lab,capture_output=True,text=True,encoding="utf-8")
    assert p.returncode != 0 and "closure inventory mismatch" in p.stderr
    assert not marker.exists()


def test_lock_missing_derived_executed_dependency_is_rejected(tmp_path):
    lab=tmp_path/"lab";lab.mkdir();run=lab/RUN_DIR.name;shutil.copytree(RUN_DIR,run)
    _copy_closure(lab)
    launcher=lab/LAUNCHER.name;shutil.copy2(LAUNCHER,launcher)
    lock_path=run/"release_lock.json";lock=json.loads(lock_path.read_text(encoding="utf-8"))
    lock["executable_closure_sha256"].pop("prepare_router_subscription_h4.py")
    lock_path.write_text(json.dumps(lock,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    args=[sys.executable,str(launcher),"--anchor-launcher-sha256",_sha(LAUNCHER),
          "--anchor-lock-sha256",_sha(lock_path),"--anchor-manifest-sha256",_sha(run/"manifest.json"),
          "--action","prepare","status"]
    p=subprocess.run(args,cwd=lab,capture_output=True,text=True,encoding="utf-8")
    assert p.returncode != 0
    assert "executed dependency absent from release closure: prepare_router_subscription_h4.py" in p.stderr


def test_candidate_entrypoints_fail_without_verified_host_context(monkeypatch):
    monkeypatch.delenv("ROUTER_H5_R5_PREFLIGHT_MANIFEST_SHA256",raising=False)
    monkeypatch.delenv("ROUTER_H5_R5_SYNTHETIC_TEST_MODE",raising=False)
    with pytest.raises(ReplayInputError,match="verified-launcher host context"):
        H5SubscriptionCoordinatorR5(RUN_DIR)
    with pytest.raises(ReplayInputError,match="verified-launcher host context"):
        FileSubscriptionRunnerH5R5(RUN_DIR,lambda envelope:"{}")
