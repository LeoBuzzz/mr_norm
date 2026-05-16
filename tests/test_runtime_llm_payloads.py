from __future__ import annotations

import pytest

from mr_norm.runtime.llm_clients import parse_json_object
from mr_norm.runtime.llm_payloads import (
    normalize_final_answer_payload,
    normalize_planner_payload,
    normalize_reranker_payload,
)


def test_parse_json_object_extracts_object_from_prose() -> None:
    payload = parse_json_object(
        'Here is the result:\n{"answer":"ok","citations":["chunk_1"]}\nThanks.'
    )

    assert payload["answer"] == "ok"
    assert payload["citations"] == ["chunk_1"]


def test_parse_json_object_accepts_top_level_ranked_list() -> None:
    payload = parse_json_object('["chunk_b", "chunk_a"]')

    assert payload["ranked_chunk_ids"] == ["chunk_b", "chunk_a"]


def test_normalize_planner_payload_accepts_comma_separated_tools() -> None:
    normalized, warnings = normalize_planner_payload(
        {"selected_tools": "payload, vector", "routing_reasons": "payload: lookup"}
    )

    assert normalized["selected_tools"] == ["payload", "vector"]
    assert warnings == []


def test_normalize_reranker_payload_accepts_items_alias() -> None:
    normalized, warnings = normalize_reranker_payload(
        {"items": [{"chunk_id": "chunk_2"}, {"chunk_id": "chunk_1"}]}
    )

    assert normalized["ranked_chunk_ids"] == ["chunk_2", "chunk_1"]
    assert warnings == []


def test_normalize_final_answer_payload_accepts_string_citations() -> None:
    normalized, warnings = normalize_final_answer_payload(
        {"answer": "Ответ", "citations": "chunk_1, chunk_2"}
    )

    assert normalized["answer"] == "Ответ"
    assert normalized["citations"] == [{"chunk_id": "chunk_1"}, {"chunk_id": "chunk_2"}]
    assert warnings == []


def test_normalize_final_answer_payload_requires_answer() -> None:
    with pytest.raises(ValueError, match="answer must be a non-empty string"):
        normalize_final_answer_payload({"answer": "", "citations": []})
