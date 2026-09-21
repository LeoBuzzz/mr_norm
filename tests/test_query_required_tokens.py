from __future__ import annotations

from mr_norm.retrieval.document_knowledge import QueryTermMatches, query_required_tokens
from mr_norm.runtime.query_planner import _payload_required_tokens


def test_query_required_tokens_extracts_distinctive_terms() -> None:
    tokens = query_required_tokens(
        "требования к заземлению при напряжении",
        ["требования", "заземлению", "напряжении"],
    )
    assert tokens == ("заземлению", "напряжении")


def test_query_required_tokens_skips_fillers() -> None:
    tokens = query_required_tokens(
        "правила технической эксплуатации",
        ["правила", "технической", "эксплуатации"],
    )
    assert tokens == ()


def test_query_required_tokens_empty_for_point_lookup() -> None:
    tokens = query_required_tokens(
        "требования к заземлению при напряжении",
        ["заземлению", "напряжении"],
        question_type="point_lookup",
    )
    assert tokens == ()


def test_query_required_tokens_requires_token_in_query() -> None:
    tokens = query_required_tokens(
        "требования к заземлению",
        ["заземлению", "квалификация"],
    )
    assert tokens == ()


def test_auto_required_tokens_feature_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("MR_NORM_DISABLE_AUTO_REQUIRED_TOKENS", "1")
    tokens = _payload_required_tokens(
        QueryTermMatches(),
        original_query="требования к заземлению при напряжении",
        significant_words=["заземлению", "напряжении"],
        question_type="factual",
    )
    assert tokens == ()
