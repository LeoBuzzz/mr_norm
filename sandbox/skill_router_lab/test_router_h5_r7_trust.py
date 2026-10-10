"""Disposable, pre-import parent-integrity negatives for the H5 r7 launcher."""
import hashlib
import json
import shutil
import subprocess
import pytest
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R7 = HERE / "router_subscription_run_tuning_v3_h5_r7_20261010"
R4_NAME = "router_subscription_run_tuning_v3_h5_r4_20261010"
R5_NAME = "router_subscription_run_tuning_v3_h5_r5_20261010"
R6_NAME = "router_subscription_run_tuning_v3_h5_r6_20261010"
R7_NAME = "router_subscription_run_tuning_v3_h5_r7_20261010"
LAUNCHER = "release_router_h5_r7.py"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def _copy_checkout(dst):
    lock = json.loads((R7 / "release_lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((R7 / "manifest.json").read_text(encoding="utf-8"))
    release_files = set(lock["executable_closure_sha256"])
    release_files.update(manifest["code_files_sha256"])
    release_files.update(manifest["test_files_sha256"])
    for rel in release_files:
        src = HERE / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for rel in lock["predecessor_trees"]:
        shutil.copytree(HERE / rel, dst / rel)
    for rel in (R4_NAME, R5_NAME, R6_NAME, R7_NAME):
        shutil.copytree(HERE / rel, dst / rel)
    return dst / LAUNCHER

def _run(tmp_path, target_rel):
    launcher = _copy_checkout(tmp_path)
    target = tmp_path / target_rel
    with target.open("ab") as stream: stream.write(b"\x00")
    run = tmp_path / R7_NAME
    anchors = json.loads((HERE / "reviewer_anchor_inputs_h5_r7_20261010.json").read_text(encoding="utf-8"))
    result = subprocess.run([sys.executable, "-I", "-S", str(launcher),
        "--anchor-launcher-sha256", anchors["launcher_sha256"],
        "--anchor-lock-sha256", anchors["release_lock_sha256"],
        "--anchor-manifest-sha256", anchors["manifest_sha256"],
        "--action", "freeze"], cwd=tmp_path, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "release-lock anchor mismatch" in combined or "direct parent tree changed" in combined, combined

def test_r4_release_lock_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/release_lock.json")

def test_r4_manifest_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/manifest.json")

def test_r4_resource_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R4_NAME}/prompts/parser.txt")

def test_r5_direct_parent_tamper_fails_before_candidate_import(tmp_path):
    _run(tmp_path, f"{R5_NAME}/release_lock.json")


@pytest.mark.parametrize("shadow", ["hashlib.py", "jsonschema/__init__.py", "jsonschema/", "attrs.py", "referencing/__init__.py", "sitecustomize.py", "usercustomize.py", "release-shadow.pth"])
@pytest.mark.parametrize("action", ["freeze", "replay", "prepare"])
def test_preimport_shadow_markers_never_execute(tmp_path, shadow, action):
    launcher = _copy_checkout(tmp_path)
    marker = tmp_path / "shadow-marker.txt"
    shadow_path = tmp_path / shadow
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    if shadow.endswith("/"):
        shadow_path.mkdir(parents=True, exist_ok=True)
    else:
        shadow_path.write_text("import pathlib\npathlib.Path(r'%s').write_text('executed')\n" % marker,
                               encoding="utf-8")
    run = tmp_path / R7_NAME
    command = [sys.executable, "-I", "-S", str(launcher),
        "--anchor-launcher-sha256", sha(launcher),
        "--anchor-lock-sha256", sha(run / "release_lock.json"),
        "--anchor-manifest-sha256", sha(run / "manifest.json"),
        "--action", action]
    if action == "prepare": command.append("status")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert not marker.exists(), "shadow module executed before rejection"
    assert any(token in combined.lower() for token in ("shadow", "package directory", ".pth", "customize")), combined


def test_isolated_bootstrap_reports_verified_interpreter_path_and_import_origins(tmp_path):
    launcher = _copy_checkout(tmp_path)
    run = tmp_path / R7_NAME
    result = subprocess.run([sys.executable, "-I", "-S", str(launcher),
        "--anchor-launcher-sha256", sha(launcher),
        "--anchor-lock-sha256", sha(run / "release_lock.json"),
        "--anchor-manifest-sha256", sha(run / "manifest.json"),
        "--action", "prepare", "status"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    audit = next(line for line in result.stderr.splitlines() if line.startswith("H5_R7_BOOTSTRAP_AUDIT "))
    report = json.loads(audit.split(" ", 1)[1])
    assert Path(report["interpreter"]).resolve() == Path(sys.executable).resolve()
    assert str(tmp_path.resolve()).casefold() not in [path.casefold() for path in report["sys_path"]]
    assert str(Path(r"C:\Users\buleo\miniconda3\Lib\site-packages").resolve()) in report["sys_path"]
    assert str(Path(r"C:\Users\buleo\miniconda3\Lib\site-packages\jsonschema\__init__.py").resolve()) in report["import_origins"].values()
    assert str(Path(r"C:\Users\buleo\miniconda3\Lib\hashlib.py").resolve()) in report["import_origins"].values()


def test_populated_h4_and_r10_evidence_remains_byte_pinned():
    expected = {
        "router_subscription_run_tuning_v3_h4_20261009": {
            "receipts.jsonl": ("6e40bf122b8c1a0145967adf3045c2c35adb9e89bbc9ee1ba3f0e9ad2ccf4b88", 40),
            "request_envelopes.jsonl": ("e052b331a00e75e766cb988492404ae6c3a986cee4b2cbf1beedeb244d214200", 40),
            "receipt_envelope_bindings.jsonl": ("eb999ddc529a9f0cf09767fff2bf3c701c00c128c47b4011cab66dde89468949", 40)},
        "router_subscription_run_tuning_v3_h3_r10_20261009": {
            "receipts.jsonl": ("53991d3a3864c6ce70dc385f419890cd39ff35e120b0a4ff2d8240348f755573", 40),
            "request_envelopes.jsonl": ("7a50fe149266c0c567a577349633c4b90baee57f2d25341f1a33aff7abaa25d6", 40),
            "receipt_envelope_bindings.jsonl": ("233b591382662a670ce11c20ea663569420641c10cff8f196f7b03d5e664d212", 40)}}
    for run_name, streams in expected.items():
        for name, (digest, count) in streams.items():
            data = (HERE / run_name / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest
            assert len([line for line in data.splitlines() if line.strip()]) == count
    archived = HERE / "router_subscription_run_tuning_v3_h5_r5_20261010" / "evidence" / "original_tests"
    original_hashes = {"test_router_h4_offline.py": "8722d2eeaf74eaf9dee83717fae2b033ce7a6fca72049a6bb83941f26b093279",
                       "test_router_h4_lifecycle_boundaries.py": "e039c6edf0677ad7c437a18fad909ee3488852f8e23b7338818a55992f1cfde5",
                       "test_router_subscription_h4_coordinator.py": "fb934ad977a9913057b5b541857392bcc80a594b0dfa56cce616691d6fd1442a"}
    for name, digest in original_hashes.items():
        assert sha(archived / name) == digest
        assert sha(HERE / name) == digest


