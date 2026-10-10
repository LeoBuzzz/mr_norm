"""Disposable, pre-import parent-integrity negatives for the H5 r6 launcher."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R6 = HERE / "router_subscription_run_tuning_v3_h5_r6_20261010"
R4_NAME = "router_subscription_run_tuning_v3_h5_r4_20261010"
R5_NAME = "router_subscription_run_tuning_v3_h5_r5_20261010"
R6_NAME = "router_subscription_run_tuning_v3_h5_r6_20261010"
LAUNCHER = "release_router_h5_r6.py"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def _copy_checkout(dst):
    lock = json.loads((R6 / "release_lock.json").read_text(encoding="utf-8"))
    for rel in lock["executable_closure_sha256"]:
        src = HERE / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for rel in lock["predecessor_trees"]:
        shutil.copytree(HERE / rel, dst / rel)
    for rel in (R4_NAME, R5_NAME, R6_NAME):
        shutil.copytree(HERE / rel, dst / rel)
    return dst / LAUNCHER

def _run(tmp_path, target_rel):
    launcher = _copy_checkout(tmp_path)
    # A candidate import writes this marker. Parent corruption must stop before it runs.
    candidate = tmp_path / "freeze_router_v3_h5_r6.py"
    marker = tmp_path / "candidate-imported.marker"
    with candidate.open("a", encoding="utf-8") as stream:
        stream.write("\nfrom pathlib import Path\nPath(r'%s').write_text('imported')\n" % marker)
    run_lock_path = tmp_path / R6_NAME / "release_lock.json"
    run_lock = json.loads(run_lock_path.read_text(encoding="utf-8"))
    run_lock["executable_closure_sha256"][candidate.name] = sha(candidate)
    run_lock_path.write_text(json.dumps(run_lock, indent=2) + "\n", encoding="utf-8")
    target = tmp_path / target_rel
    with target.open("ab") as stream: stream.write(b"\x00")
    run = tmp_path / R6_NAME
    result = subprocess.run([sys.executable, "-B", str(launcher),
        "--anchor-launcher-sha256", sha(launcher),
        "--anchor-lock-sha256", sha(run / "release_lock.json"),
        "--anchor-manifest-sha256", sha(run / "manifest.json"),
        "--action", "freeze"], cwd=tmp_path, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "direct parent tree changed" in combined, combined
    assert not marker.exists(), "candidate Python imported before parent verification"

def test_r4_release_lock_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/release_lock.json")

def test_r4_manifest_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/manifest.json")

def test_r4_resource_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/prompts/parser.txt")

def test_r5_direct_parent_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R5_NAME}/release_lock.json")
