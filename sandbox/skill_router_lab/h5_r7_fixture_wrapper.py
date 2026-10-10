from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

STREAMS = ("receipts.jsonl", "request_envelopes.jsonl", "receipt_envelope_bindings.jsonl")
FIXTURE_MODULES = {
    "test_router_h4_offline.py": "router_subscription_run_tuning_v3_h4_20261009",
    "test_router_h4_lifecycle_boundaries.py": "router_subscription_run_tuning_v3_h4_20261009",
    "test_router_subscription_h4_coordinator.py": "router_subscription_run_tuning_v3_h4_20261009",
    "test_router_subscription_r10_coordinator.py": "router_subscription_run_tuning_v3_h3_r10_20261009",
}

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def pytest_runtest_call(item):
    filename = Path(item.module.__file__).name
    if filename not in FIXTURE_MODULES or "tmp_path" not in item.funcargs:
        return
    original = Path(item.module.RUN_DIR).resolve()
    target = Path(item.funcargs["tmp_path"]) / "h5_r7_disposable_source"
    shutil.copytree(original, target)
    assert target.resolve() != original
    manifest_bytes = (target / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    resources_before = {rel: _sha(target / rel) for rel in manifest["files_sha256"]}
    for stream in STREAMS:
        (target / stream).unlink(missing_ok=True)
    assert all(not (target / stream).exists() for stream in STREAMS)
    assert (target / "manifest.json").read_bytes() == manifest_bytes
    assert resources_before == {rel: _sha(target / rel) for rel in manifest["files_sha256"]}
    item.module.RUN_DIR = target
