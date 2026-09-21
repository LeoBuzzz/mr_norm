"""Tests for pipeline feature flags and routing fixes."""

from __future__ import annotations

import os

import pytest

from mr_norm.runtime.contracts import PreparedToolQuery, RuntimeRequest
from mr_norm.runtime.pipeline_diagnostics import (
    apply_intent_tool_routing,
    should_apply_doc_point_boost,
)
from mr_norm.runtime.pipeline_features import (
    auto_required_tokens_enabled,
    default_reranker_backend,
    effective_doc_confidence_threshold,
    looks_like_requirement_query,
    normalize_routing_question_type,
    query_has_explicit_doc_reference,
    rrf_weights_enabled,
    tail_point_fallback_enabled,
)


def test_regulation_scope_keeps_vector_in_tool_order():
    prepared = (
        PreparedToolQuery(tool_name="vector", queries=("query",)),
        PreparedToolQuery(tool_name="payload", queries=("query",)),
        PreparedToolQuery(tool_name="point", queries=("18",)),
    )
    tools, _, mode = apply_intent_tool_routing(
        prepared,
        question_type="regulation_scope",
        doc_scoped=False,
        point_number_hints=[],
        original_query="Что регулирует приказ?",
    )
    assert "vector" in tools
    assert mode == "regulation_scope_hybrid"


def test_regulation_scope_requirement_alias_routes_broad():
    prepared = (
        PreparedToolQuery(tool_name="vector", queries=("query",)),
        PreparedToolQuery(tool_name="payload", queries=("query",)),
    )
    query = "Какие требования предъявляются к хранению документации?"
    assert looks_like_requirement_query(query)
    assert normalize_routing_question_type("regulation_scope", query) == "requirement"
    tools, _, mode = apply_intent_tool_routing(
        prepared,
        question_type="regulation_scope",
        doc_scoped=False,
        point_number_hints=[],
        original_query=query,
    )
    assert tools[0] == "vector"
    assert mode == "requirement_broad"


def test_should_apply_doc_point_boost_requires_confirmed_doc_id():
    request = RuntimeRequest(
        query="test",
        filters={"doc_name": "Some Doc"},
        limit=40,
    )
    assert should_apply_doc_point_boost(request) is False

    request = RuntimeRequest(
        query="test",
        filters={"doc_id": "doc_123"},
        limit=40,
    )
    assert should_apply_doc_point_boost(request) is True


def test_disable_intent_routing_env(monkeypatch):
    monkeypatch.setenv("MR_NORM_DISABLE_INTENT_ROUTING", "1")
    prepared = (
        PreparedToolQuery(tool_name="vector", queries=("query",)),
        PreparedToolQuery(tool_name="payload", queries=("query",)),
    )
    tools, _, mode = apply_intent_tool_routing(
        prepared,
        question_type="regulation_scope",
        doc_scoped=False,
        point_number_hints=[],
        original_query="Какие требования?",
    )
    assert mode == "default_muted"
    assert tools == ("payload", "vector")


def test_disable_doc_point_boost_env(monkeypatch):
    monkeypatch.setenv("MR_NORM_DISABLE_DOC_POINT_BOOST", "1")
    request = RuntimeRequest(query="test", filters={"doc_id": "doc_123"}, limit=40)
    assert should_apply_doc_point_boost(request) is False


def test_natural_query_uses_soft_doc_confidence_threshold():
    natural = "Когда нужно рассчитать показатели технико-экономического состояния?"
    explicit = "Что установлено в пункте 4 приказа Минэнерго №1401?"
    assert effective_doc_confidence_threshold(natural) == 0.50
    assert effective_doc_confidence_threshold(explicit) == 0.55
    assert not query_has_explicit_doc_reference(natural)
    assert query_has_explicit_doc_reference(explicit)


def test_document_lookup_natural_when_maps_to_requirement():
    query = "Когда нужно рассчитать показатели?"
    assert normalize_routing_question_type("document_lookup", query) == "requirement"


def test_default_reranker_backend_deep_prompt():
    assert default_reranker_backend("deep") == "prompt"


def test_default_reranker_backend_balanced_score():
    assert default_reranker_backend("balanced") == "score"


def test_default_reranker_backend_fast_passthrough():
    assert default_reranker_backend("fast") == "passthrough"


def test_default_reranker_backend_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("MR_NORM_DISABLE_PROFILE_RERANKER", "1")
    assert default_reranker_backend("deep") == "passthrough"
    assert default_reranker_backend("balanced") == "passthrough"


def test_rrf_weights_and_auto_tokens_flags(monkeypatch):
    assert rrf_weights_enabled() is True
    assert auto_required_tokens_enabled() is True
    monkeypatch.setenv("MR_NORM_DISABLE_RRF_WEIGHTS", "1")
    monkeypatch.setenv("MR_NORM_DISABLE_AUTO_REQUIRED_TOKENS", "1")
    assert rrf_weights_enabled() is False
    assert auto_required_tokens_enabled() is False


def test_tail_point_fallback_is_off_by_default_and_opt_in(monkeypatch):
    monkeypatch.delenv("MR_NORM_ENABLE_TAIL_POINT_FALLBACK", raising=False)
    assert tail_point_fallback_enabled() is False
    monkeypatch.setenv("MR_NORM_ENABLE_TAIL_POINT_FALLBACK", "1")
    assert tail_point_fallback_enabled() is True
