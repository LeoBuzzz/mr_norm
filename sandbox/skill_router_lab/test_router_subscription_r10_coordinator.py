from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from freeze_router_v3_h3_r10 import RUN_DIR
from freeze_router_v3_h3_r9 import RUN_DIR as R9_RUN_DIR
from prepare_router_subscription_r10 import R10SubscriptionCoordinator
from router_subscription_eval import ReplayInputError, append_receipt


def _clone(tmp_path: Path) -> Path:
    target = tmp_path / "run-r10"
    shutil.copytree(RUN_DIR, target)
    return target


def _metadata():
    return {"agent_id": "isolated-test-turn", "started_at": "2026-10-09T10:00:00+00:00",
            "finished_at": "2026-10-09T10:00:01+00:00"}


def test_r10_coordinator_prepares_exactly_twenty_classifier_envelopes(tmp_path):
    coordinator = R10SubscriptionCoordinator(_clone(tmp_path))
    result = coordinator.prepare_classifier()
    rows = [json.loads(line) for line in (coordinator.run_dir / "request_envelopes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result["prepared_count"] == 20
    assert len(rows) == 20
    for row in rows:
        envelope = json.loads(row["envelope_utf8"])
        assert row["capture_stage"] == "pre_dispatch"
        assert row["run_id"] == "router-v3-h3-tuning-20261009-r10"
        assert row["envelope_sha256"] == hashlib.sha256(row["envelope_utf8"].encode("utf-8")).hexdigest()
        assert envelope["role"] == "classifier" and envelope["attempt"] == 1
        assert envelope["model"] == "gpt-6-luna" and envelope["effort"] == "medium"
    assert not (coordinator.run_dir / "receipts.jsonl").exists()


def test_r10_coordinator_rejects_wrong_run_and_changed_manifest(tmp_path):
    with pytest.raises(ValueError, match="run identity mismatch"):
        R10SubscriptionCoordinator(R9_RUN_DIR)
    changed = _clone(tmp_path)
    manifest = changed / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ReplayInputError, match="approved R10 run and manifest"):
        R10SubscriptionCoordinator(changed)


def test_r10_coordinator_refuses_nonempty_receipts_at_initial_prepare(tmp_path):
    coordinator = R10SubscriptionCoordinator(_clone(tmp_path))
    receipt_path = coordinator.run_dir / "receipts.jsonl"
    raw = ""
    digest = hashlib.sha256(raw.encode()).hexdigest()
    append_receipt(receipt_path, {
        "run_id": coordinator.run_id, "case_id": "S01", "role": "classifier", "attempt": 1,
        "input_sha256": "0" * 64, "prompt_sha256": "1" * 64, "schema_sha256": "2" * 64,
        "agent_id": "preexisting", "requested_model": "gpt-6-luna", "requested_effort": "medium",
        "actual_model": None, "model_verification": "requested_by_host_only",
        "started_at": "start", "finished_at": "finish", "raw_final_text": raw,
        "raw_sha256": digest, "event_id": None, "finish": "completed", "error": None,
    })
    with pytest.raises(ReplayInputError, match="requires empty receipts"):
        coordinator.prepare_classifier()


def test_r10_classifier_import_drives_parser_and_repair_envelopes_without_dispatch(tmp_path):
    coordinator = R10SubscriptionCoordinator(_clone(tmp_path))
    coordinator.prepare_classifier()
    envelope_rows = [json.loads(line) for line in coordinator.envelope_path.read_text(encoding="utf-8").splitlines()]
    for row in envelope_rows:
        coordinator.import_raw_final(case_id=row["case_id"], role="classifier", attempt=1,
            envelope_sha256=row["envelope_sha256"], raw_final_text='{"complexity":"simple"}', **_metadata())
    parser_prepared = coordinator.prepare_parsers()
    assert parser_prepared["prepared_count"] == 20
    parser_row = next(row for row in coordinator._prepared_index().values()
                      if row["role"] == "simple_parser" and row["case_id"] == "S01")
    result = coordinator.import_raw_final(case_id="S01", role="simple_parser", attempt=1,
        envelope_sha256=parser_row["envelope_sha256"], raw_final_text="not-json", **_metadata())
    assert result["next_phase"] == "repair"
    repair_row = coordinator._prepared_index()[("S01", "simple_parser", 2)]
    repair_envelope = json.loads(repair_row["envelope_utf8"])
    repair_user = json.loads(repair_envelope["messages"][1]["content"])
    assert repair_envelope["role"] == "simple_parser" and repair_envelope["attempt"] == 2
    assert repair_envelope["messages"][0]["content"] == coordinator.prompts["repair"]
    assert repair_user["raw_response"] == "not-json"
    assert len(coordinator._receipts()) == 21
    assert len([row for row in coordinator._prepared_index().values() if row["attempt"] == 2]) == 1


def test_r10_import_rejects_unbound_or_duplicate_raw_finals(tmp_path):
    coordinator = R10SubscriptionCoordinator(_clone(tmp_path))
    coordinator.prepare_classifier()
    with pytest.raises(ReplayInputError, match="does not bind"):
        coordinator.import_raw_final(case_id="S01", role="classifier", attempt=1,
            envelope_sha256="0" * 64, raw_final_text='{"complexity":"simple"}', **_metadata())
