from __future__ import annotations

from pathlib import Path

from mr_norm.config.pue_aliases import (
    active_known_query_aliases,
    active_topic_aliases,
    resolve_enable_pue_aliases,
)
from mr_norm.retrieval.document_knowledge import find_knowledge_candidates, load_document_knowledge


def test_resolve_enable_pue_aliases_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("MR_NORM_ENABLE_PUE_ALIASES", raising=False)
    assert resolve_enable_pue_aliases() is False
    assert resolve_enable_pue_aliases(None) is False


def test_resolve_enable_pue_aliases_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MR_NORM_ENABLE_PUE_ALIASES", "1")
    assert resolve_enable_pue_aliases() is True
    assert resolve_enable_pue_aliases(False) is False


def test_active_known_query_aliases_excludes_pue_by_default() -> None:
    aliases = active_known_query_aliases(enable_pue_aliases=False)
    assert "пуэ" not in aliases
    assert "птэ" in aliases


def test_knowledge_pue_topic_alias_requires_flag() -> None:
    path = Path(__file__).parent / "fixtures" / "document_knowledge_sample.json"
    knowledge = load_document_knowledge(path)

    disabled = find_knowledge_candidates("что в пуэ про заземление", knowledge, limit=5)
    enabled = find_knowledge_candidates(
        "что в пуэ про заземление",
        knowledge,
        limit=5,
        enable_pue_aliases=True,
    )

    assert not any("topic_alias:пуэ" in candidate.reasons for candidate in disabled)
    assert any("topic_alias:пуэ" in candidate.reasons for candidate in enabled)


def test_active_topic_aliases_filters_pue_phrase() -> None:
    topics = [
        {"phrase": "пуэ", "doc_name_substrings": ["правила устройства электроустановок"]},
        {"phrase": "птэ", "doc_name_substrings": ["правила технической эксплуатации"]},
    ]
    filtered = active_topic_aliases(topics, enable_pue_aliases=False)
    assert len(filtered) == 1
    assert filtered[0]["phrase"] == "птэ"
