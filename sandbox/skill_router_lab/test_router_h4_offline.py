from __future__ import annotations

import json
import shutil
from pathlib import Path

from freeze_router_v3_h4 import RUN_DIR, validate_frozen
from router_prompts_v3_h4 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM
from router_subscription_lifecycle_h4 import FileSubscriptionRunnerH4


def test_h4_prompt_policy_keeps_aliases_and_competing_candidates_distinct():
    assert "do not treat separate strings as synonyms" not in PARSER_V3_SYSTEM
    assert "Different strings alone do not prove different meanings" in PARSER_V3_SYSTEM
    assert "Zeta Quartz" in PARSER_V3_SYSTEM and "Zinc Quorum" in PARSER_V3_SYSTEM
    assert "two different document IDs" in PARSER_V3_SYSTEM
    assert "Several spellings of one established meaning do not make a request complex" in CLASSIFIER_V3_SYSTEM


def test_h4_freeze_and_lifecycle_use_only_frozen_h4_prompts(tmp_path: Path):
    frozen = validate_frozen(RUN_DIR)
    assert frozen["run_id"] == "router-v3-h4-tuning-20261009-r1"
    assert frozen["direct_parent_manifest_sha256"] == "ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc"
    assert frozen["receipts_present"] is False

    run_dir = tmp_path / "h4_run"
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
    result = FileSubscriptionRunnerH4(run_dir, dispatch).run_case(row)
    assert result["status"] == "valid"
    assert [e["role"] for e in envelopes] == ["classifier", "simple_parser"]
    assert envelopes[0]["messages"][0]["content"] == (run_dir / "prompts/classifier.txt").read_text(encoding="utf-8")
    assert envelopes[1]["messages"][0]["content"] == (run_dir / "prompts/parser.txt").read_text(encoding="utf-8")
    assert envelopes[1]["messages"][0]["content"] == PARSER_V3_SYSTEM
