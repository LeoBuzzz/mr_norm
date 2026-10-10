from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from freeze_router_v3_h5 import RUN_DIR, validate_frozen
from prepare_router_subscription_h5 import H5SubscriptionCoordinator
from router_subscription_eval import ReplayInputError


def _copy(tmp_path: Path) -> Path:
    destination = tmp_path / "h5-run"
    shutil.copytree(RUN_DIR, destination)
    return destination


def _metadata():
    return {"agent_id": "isolated-worker-1", "started_at": "2026-10-09T10:00:00+00:00",
            "finished_at": "2026-10-09T10:00:01+00:00"}


def test_h5_frozen_validation_and_pre_dispatch_envelopes(tmp_path):
    inventory = json.loads((RUN_DIR / "hash_inventory.json").read_text(encoding="utf-8"))
    assert validate_frozen(RUN_DIR)["manifest_sha256"] == inventory["manifest_sha256"]
    coordinator = H5SubscriptionCoordinator(_copy(tmp_path))
    result = coordinator.prepare_classifier()
    rows = [json.loads(line) for line in coordinator.envelope_path.read_text(encoding="utf-8").splitlines()]
    assert result["prepared_count"] == len(rows) == 20
    assert all(row["capture_stage"] == "pre_dispatch" for row in rows)
    assert all(hashlib.sha256(row["envelope_utf8"].encode()).hexdigest() == row["envelope_sha256"] for row in rows)
    assert all(json.loads(row["envelope_utf8"])["role"] == "classifier" for row in rows)
    assert not (coordinator.run_dir / "receipts.jsonl").exists()


def test_h5_import_uses_frozen_prompts_and_allows_one_repair(tmp_path):
    coordinator = H5SubscriptionCoordinator(_copy(tmp_path))
    coordinator.prepare_classifier()
    classifier_rows = [json.loads(line) for line in coordinator.envelope_path.read_text(encoding="utf-8").splitlines()]
    for row in classifier_rows:
        coordinator.import_raw_final(case_id=row["case_id"], role="classifier", attempt=1,
            envelope_sha256=row["envelope_sha256"], raw_final_text='{"complexity":"simple"}', **_metadata())
    assert coordinator.prepare_parsers()["prepared_count"] == 20
    parser = coordinator._prepared_index()[("S01", "simple_parser", 1)]
    assert json.loads(parser["envelope_utf8"])["messages"][0]["content"] == coordinator.prompts["parser"]
    first = coordinator.import_raw_final(case_id="S01", role="simple_parser", attempt=1,
        envelope_sha256=parser["envelope_sha256"], raw_final_text="not-json", **_metadata())
    assert first["next_phase"] == "repair"
    repair = coordinator._prepared_index()[("S01", "simple_parser", 2)]
    assert json.loads(repair["envelope_utf8"])["messages"][0]["content"] == coordinator.prompts["repair"]
    second = coordinator.import_raw_final(case_id="S01", role="simple_parser", attempt=2,
        envelope_sha256=repair["envelope_sha256"], raw_final_text="still-not-json", **_metadata())
    assert second["next_phase"] == "terminal_invalid_repair"
    assert coordinator.status()["repair_attempts"] == 1
    with pytest.raises(ReplayInputError, match="duplicate immutable"):
        coordinator.import_raw_final(case_id="S01", role="simple_parser", attempt=2,
            envelope_sha256=repair["envelope_sha256"], raw_final_text="{}", **_metadata())


def test_h5_resource_substitution_and_parser_role_drift_fail_closed(tmp_path):
    from freeze_router_v3_h5 import validate_frozen

    run_dir = _copy(tmp_path)
    prompt = run_dir / "prompts/parser.txt"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen resource hash mismatch"):
        validate_frozen(run_dir)

    second = tmp_path / "second"
    second.mkdir()
    run_dir = _copy(second)
    coordinator = H5SubscriptionCoordinator(run_dir)
    coordinator.prepare_classifier()
    item = coordinator._prepared_index()[("S01", "classifier", 1)]
    for classifier in coordinator._prepared_index().values():
        if classifier["role"] == "classifier":
            coordinator.import_raw_final(case_id=classifier["case_id"], role="classifier", attempt=1,
                envelope_sha256=classifier["envelope_sha256"], raw_final_text='{"complexity":"simple"}', **_metadata())
    coordinator.prepare_parsers()
    simple = coordinator._prepared_index()[("S01", "simple_parser", 1)]
    with pytest.raises(ReplayInputError, match="does not bind to a prepared exact envelope"):
        coordinator.import_raw_final(case_id="S01", role="complex_parser", attempt=1,
            envelope_sha256=simple["envelope_sha256"], raw_final_text="{}", **_metadata())


def test_h5_coordinator_rejects_coherent_manifest_and_inventory_substitution(tmp_path):
    run_dir = _copy(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["transport"] += "; altered"
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    inventory_path = run_dir / "hash_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exact manifest SHA"):
        validate_frozen(run_dir)
    with pytest.raises(ReplayInputError, match="exact H5 r3 manifest"):
        H5SubscriptionCoordinator(run_dir)


def test_h5_r3_lineage_substitution_fails_validation_coordinator_and_lifecycle(tmp_path, monkeypatch):
    import freeze_router_v3_h5 as freeze_module
    from router_subscription_lifecycle_h5 import FileSubscriptionRunnerH5

    run_dir = _copy(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lineage"]["h4_manifest_sha256"] = manifest["lineage"]["parent_manifest_sha256"]
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    monkeypatch.setattr(freeze_module, "EXPECTED_MANIFEST_SHA256", hashlib.sha256(manifest_bytes).hexdigest())
    with pytest.raises(ValueError, match="lineage mismatch: h4_manifest_sha256"):
        validate_frozen(run_dir)
    with pytest.raises(ReplayInputError, match="exact H5 r3 manifest"):
        H5SubscriptionCoordinator(run_dir)
    with pytest.raises(ReplayInputError, match="exact H5 r3 manifest"):
        FileSubscriptionRunnerH5(run_dir, lambda _: "{}")
