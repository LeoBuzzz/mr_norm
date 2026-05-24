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
    looks_like_requirement_query,
    normalize_routing_question_type,
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
    assert tools[0] == "payload"
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
