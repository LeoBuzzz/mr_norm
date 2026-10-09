from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from freeze_router_v3_h4 import RUN_DIR, validate_frozen
from prepare_router_subscription_h4 import H4SubscriptionCoordinator
from router_subscription_eval import ReplayInputError


def _copy(tmp_path: Path) -> Path:
    destination = tmp_path / "h4-run"
    shutil.copytree(RUN_DIR, destination)
    return destination


def _metadata():
    return {"agent_id": "isolated-worker-1", "started_at": "2026-10-09T10:00:00+00:00",
            "finished_at": "2026-10-09T10:00:01+00:00"}


def test_h4_frozen_validation_and_pre_dispatch_envelopes(tmp_path):
    assert validate_frozen(RUN_DIR)["manifest_sha256"] == "4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa"
    coordinator = H4SubscriptionCoordinator(_copy(tmp_path))
    result = coordinator.prepare_classifier()
    rows = [json.loads(line) for line in coordinator.envelope_path.read_text(encoding="utf-8").splitlines()]
    assert result["prepared_count"] == len(rows) == 20
    assert all(row["capture_stage"] == "pre_dispatch" for row in rows)
    assert all(hashlib.sha256(row["envelope_utf8"].encode()).hexdigest() == row["envelope_sha256"] for row in rows)
    assert all(json.loads(row["envelope_utf8"])["role"] == "classifier" for row in rows)
    assert not (coordinator.run_dir / "receipts.jsonl").exists()


def test_h4_import_uses_frozen_prompts_and_allows_one_repair(tmp_path):
    coordinator = H4SubscriptionCoordinator(_copy(tmp_path))
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
