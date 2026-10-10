from __future__ import annotations

import json
import shutil
from pathlib import Path

from freeze_router_v3_h5 import RUN_DIR, empty_replay_summary, validate_frozen
from router_prompts_v3_h5 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM
from router_subscription_lifecycle_h5 import FileSubscriptionRunnerH5


def test_h5_prompt_policy_keeps_aliases_and_competing_candidates_distinct():
    assert "do not treat separate strings as synonyms" not in PARSER_V3_SYSTEM
    assert "Different strings alone do not prove different meanings" in PARSER_V3_SYSTEM
    assert "Zeta Quartz" in PARSER_V3_SYSTEM and "Zinc Quorum" in PARSER_V3_SYSTEM
    assert "Distinct supplied document IDs remain distinct" in PARSER_V3_SYSTEM
    assert "in both directions" in PARSER_V3_SYSTEM
    assert "Before returning JSON, self-check" in PARSER_V3_SYSTEM
    assert "historical document/topic spans for inherited values" in PARSER_V3_SYSTEM
    assert "every explicit restriction remains in conditions" in PARSER_V3_SYSTEM
    assert "new point in the confirmed document is continue" in PARSER_V3_SYSTEM
    assert "Classify by independent requested results or operations" in CLASSIFIER_V3_SYSTEM
    assert all(token not in CLASSIFIER_V3_SYSTEM + PARSER_V3_SYSTEM for token in ("D03", "D04", "V03", "A02"))
    for case in ("D03", "D04", "V01", "V03", "A02", "A03"):
        assert case not in CLASSIFIER_V3_SYSTEM + PARSER_V3_SYSTEM


def test_h5_freeze_and_lifecycle_use_only_frozen_h5_prompts(tmp_path: Path):
    frozen = validate_frozen(RUN_DIR)
    assert frozen["run_id"] == "router-v3-h5-tuning-20261009-r3"
    assert frozen["direct_parent_manifest_sha256"] == "0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194"
    assert frozen["receipts_present"] is False
    parent = RUN_DIR.parent / "router_subscription_run_tuning_v3_h5_r2_20261009"
    assert (RUN_DIR / "prompts/repair.txt").read_bytes() == (parent / "prompts/repair.txt").read_bytes()
    inventory = json.loads((RUN_DIR / "hash_inventory.json").read_text(encoding="utf-8"))
    assert inventory["manifest_sha256"] == frozen["manifest_sha256"]
    assert set(inventory["test_sha256"]) == {"test_router_h5_offline.py", "test_router_h5_lifecycle_boundaries.py", "test_router_subscription_h5_coordinator.py"}

    run_dir = tmp_path / "h5_run"
    shutil.copytree(RUN_DIR, run_dir)
    row = json.loads((run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    outputs = iter([
        '{"complexity":"simple"}',
        json.dumps({"dialog": "new", "tasks": [{"operation": "search", "source": ["current@3:9"],
                                                     "topic": "пункт 6.25 правила охраны труда"}]}, ensure_ascii=False),
    ])
    envelopes = []
    def dispatch(envelope):
        envelopes.append(envelope)
        return next(outputs)
    result = FileSubscriptionRunnerH5(run_dir, dispatch).run_case(row)
    assert result["status"] == "valid"
    assert [e["role"] for e in envelopes] == ["classifier", "simple_parser"]
    assert envelopes[0]["messages"][0]["content"] == (run_dir / "prompts/classifier.txt").read_text(encoding="utf-8")
    assert envelopes[1]["messages"][0]["content"] == (run_dir / "prompts/parser.txt").read_text(encoding="utf-8")
    assert envelopes[1]["messages"][0]["content"] == PARSER_V3_SYSTEM


def test_h5_empty_run_is_fail_closed_at_twenty_case_denominator():
    summary = empty_replay_summary(RUN_DIR)
    assert summary["coverage"] == 0 and summary["denominator"] == 20
    assert summary["complete"] is False and summary["overall_gate"] is False
    assert len(summary["missing_cases"]) == 20
