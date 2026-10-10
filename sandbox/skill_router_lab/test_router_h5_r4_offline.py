from __future__ import annotations
import json
from pathlib import Path
from freeze_router_v3_h5_r4 import RUN_DIR, PARENT_MANIFEST_SHA256, empty_replay_summary, validate_frozen

def test_r4_inherits_exact_twenty_resources_and_fail_closed_empty_replay():
    assert validate_frozen(RUN_DIR)["direct_parent_manifest_sha256"] == PARENT_MANIFEST_SHA256
    summary = empty_replay_summary(RUN_DIR)
    assert summary["coverage"] == 0 and summary["denominator"] == 20
    assert summary["complete"] is False and summary["overall_gate"] is False
    assert len(summary["missing_cases"]) == 20

def test_r4_run_has_no_evidence_streams():
    for name in ("receipts.jsonl", "request_envelopes.jsonl", "receipt_envelope_bindings.jsonl"):
        assert not (RUN_DIR / name).exists()
