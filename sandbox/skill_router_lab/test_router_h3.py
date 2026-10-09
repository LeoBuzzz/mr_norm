from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from router_draft import DraftValidationError, SEMANTIC_DRAFT_SCHEMA, build_source_manifest
from router_draft_h3 import validate_semantic_draft
from router_prompts_v3_h3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_validation_policy import validator_for_policy

HERE = Path(__file__).resolve().parent
H2_RUN = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"


def _draft(topic: str, *, operation: str = "search", point: str | None = None):
    task = {"operation": operation, "source": ["current@1:2"], "topic": topic}
    if point is not None:
        task["operation"] = "text"
        task["point"] = point
    return {"dialog": "new", "tasks": [task]}


@pytest.mark.parametrize("topic", [
    "заземление",
    "тема 1.5 кВ и 50 Гц: проверка",
    "hello@example.com",
    "по пункту 1:2 без locator",
    "не заземление, а освещение (на опоре)",
])
def test_h3_literal_topics_keep_ordinary_text_unchanged(topic):
    manifest = build_source_manifest("Найди тему")
    result = validate_semantic_draft(_draft(topic), manifest)
    assert result["tasks"][0]["topic"] == topic


@pytest.mark.parametrize("operation", ["search", "all", "define", "compare", "conclude", "summarize"])
def test_h3_literal_topic_contract_is_operation_independent(operation):
    manifest = build_source_manifest("Запрос пользователя")
    result = validate_semantic_draft(_draft("вентиляция и условия объекта", operation=operation), manifest)
    assert result["tasks"][0]["topic"] == "вентиляция и условия объекта"


@pytest.mark.parametrize("topic", [
    "current@1:3",
    "unknown_turn@1:3",
    "current@999:999",
    "тема current@1:3 внутри",
    '«current@1:3»',
    "current@1:3 и other@2:4",
])
def test_h3_topic_locator_rejected_before_source_lookup(topic):
    manifest = build_source_manifest("Найди тему")
    with pytest.raises(DraftValidationError, match=r"TOPIC_MUST_BE_LITERAL_TEXT task 1"):
        validate_semantic_draft(_draft(topic), manifest)


def test_h3_point_must_remain_source_ref_and_existing_quantity_guard_is_preserved():
    manifest = build_source_manifest("Покажи пункт 1.5")
    with pytest.raises(DraftValidationError, match="malformed SourceRef"):
        validate_semantic_draft(_draft("пункт", point="1.5"), manifest)
    valid = validate_semantic_draft(_draft("пункт", point="current@3:3"), manifest)
    assert valid["tasks"][0]["point_evidence"][0]["quote"] == "1.5"

    measured = build_source_manifest("Покажи пункт 1.5 кВ")
    with pytest.raises(DraftValidationError, match="measured quantity"):
        validate_semantic_draft(_draft("напряжение", point="current@3:4"), measured)


def test_h3_prompt_uses_literal_topic_and_valid_synthetic_source_point_refs():
    assert 'topic `вентиляции`' in PARSER_V3_SYSTEM
    assert 'topic `освещению`' in PARSER_V3_SYSTEM
    assert "point SourceRefs `current@4:4` and `current@6:6`" in PARSER_V3_SYSTEM
    assert "TOPIC_MUST_BE_LITERAL_TEXT" in REPAIR_V3_SYSTEM
    assert "topic=current@" not in PARSER_V3_SYSTEM
    assert "topic uses `current@" not in PARSER_V3_SYSTEM
    assert not re.search(r"\b(?:АВР|ВЛ|ПУЭ)\b", PARSER_V3_SYSTEM)

    # Source spans in the examples are checked with the same tokenizer as runtime.
    examples = {
        "“Найди требования к вентиляции”": {"current@1:6": "“Найди требования к вентиляции”"},
        "“Перечисли все требования к освещению”": {"current@1:7": "“Перечисли все требования к освещению”"},
        "“Найди требования к вентиляции для всех смен”": {
            "current@1:9": "“Найди требования к вентиляции для всех смен”", "current@7:8": "всех смен"
        },
        "“Покажи пункты 2.1 и 2.2 вымышленной инструкции Q”": {
            "current@4:4": "2.1", "current@6:6": "2.2", "current@1:10": "“Покажи пункты 2.1 и 2.2 вымышленной инструкции Q”"
        },
        "“для внутренних лестниц”": {"current@2:4": "для внутренних лестниц"},
    }
    for query, refs in examples.items():
        manifest = build_source_manifest(query)
        from router_draft import extract_source_ref
        for ref, expected in refs.items():
            assert extract_source_ref(ref, manifest)["quote"] == expected


def test_h3_freeze_matches_h2_schema_classifier_and_parent_hash_without_mutating_h2():
    manifest = json.loads((H2_RUN / "manifest.json").read_text(encoding="utf-8"))
    classifier_hash = hashlib.sha256(CLASSIFIER_V3_SYSTEM.encode("utf-8")).hexdigest()
    assert classifier_hash == manifest["files_sha256"]["prompts/classifier.txt"]
    assert json.loads((H2_RUN / "schema.json").read_bytes()) == SEMANTIC_DRAFT_SCHEMA
    assert (H2_RUN / "manifest.json").exists()
    assert validator_for_policy("legacy.v2") is not validate_semantic_draft
    assert validator_for_policy("topic_literal_guard.v1") is validate_semantic_draft
    with pytest.raises(ValueError, match="unsupported validation policy"):
        validator_for_policy("unrecognized")
