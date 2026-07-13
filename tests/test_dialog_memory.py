from __future__ import annotations

import pytest

from mr_norm.runtime.contracts import PreparedQueryPlan, DocumentResolution
from mr_norm.runtime.dialog_memory import (
    DialogContext,
    DialogSessionStore,
    DialogTurn,
    apply_dialog_document_inheritance,
    build_retrieval_query,
    build_session_key,
    is_follow_up_query,
    parse_dialog_input,
    query_has_explicit_document_or_point,
)


def test_parse_dialog_input_new_dialog() -> None:
    assert parse_dialog_input(".какие виды тренировок проводят оперативному персоналу?") == (
        "какие виды тренировок проводят оперативному персоналу?",
        True,
        False,
    )
    assert parse_dialog_input(". какие виды тренировок") == (
        "какие виды тренировок",
        True,
        False,
    )


def test_parse_dialog_input_reset_only() -> None:
    assert parse_dialog_input(".") == ("", True, True)


def test_parse_dialog_input_continue_dialog() -> None:
    assert parse_dialog_input("как часто?") == ("как часто?", False, False)


def test_build_session_key_isolates_users_in_peer() -> None:
    assert build_session_key(peer_id=1, from_id=10) != build_session_key(peer_id=1, from_id=11)


def test_dialog_session_store_keeps_last_n_turns() -> None:
    store = DialogSessionStore(max_turns=2)
    key = "1:2"
    store.append_turn(key, user_text="q1", answer="a1")
    store.append_turn(key, user_text="q2", answer="a2")
    store.append_turn(key, user_text="q3", answer="a3")
    context = store.get(key)
    assert [turn.user_text for turn in context.turns] == ["q2", "q3"]


def test_dialog_session_store_reset_clears_history() -> None:
    store = DialogSessionStore()
    key = "1:2"
    store.append_turn(key, user_text="q1", answer="a1")
    store.reset(key)
    assert store.get(key).turns == ()


def test_build_retrieval_query_expands_short_follow_up() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="Какие виды тренировок проводят оперативному персоналу?",
                answer="Ответ",
            ),
        ),
    )
    retrieval = build_retrieval_query("как часто?", context)
    assert "тренировок" in retrieval.lower()
    assert retrieval.endswith("как часто?")


def test_build_retrieval_query_keeps_standalone_question() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(DialogTurn(user_text="первый", answer="a"),),
    )
    query = "Какие требования к хранению проектной документации в правилах работы с персоналом?"
    assert build_retrieval_query(query, context) == query


def test_apply_dialog_document_inheritance_requires_unambiguous_prior_doc() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="первый",
                answer="a",
                resolved_doc_id="doc_1",
                resolved_doc_names=("Правила",),
                resolve_unambiguous=True,
            ),
        ),
    )
    filters, warnings = apply_dialog_document_inheritance("как часто?", {}, context)
    assert filters["doc_id"] == "doc_1"
    assert "dialog:inherited_document_context" in warnings


def test_apply_dialog_document_inheritance_skips_when_new_doc_mentioned() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="первый",
                answer="a",
                resolved_doc_id="doc_1",
                resolved_doc_names=("Правила",),
                resolve_unambiguous=True,
            ),
        ),
    )
    filters, warnings = apply_dialog_document_inheritance("а в ПУЭ?", {}, context)
    assert "doc_id" not in filters
    assert warnings == []


def test_is_follow_up_query() -> None:
    assert is_follow_up_query("как часто?") is True
    assert is_follow_up_query("а в ПУЭ?") is True
    assert query_has_explicit_document_or_point("пункт 24 правил работы с персоналом") is True


def test_append_turn_stores_plan_metadata() -> None:
    store = DialogSessionStore()
    plan = PreparedQueryPlan(
        original_query="вопрос",
        resolved_doc_names=("Правила работы с персоналом",),
        point_number_hints=("24",),
        concepts=("тренировки",),
        ambiguous=False,
        document_resolution=DocumentResolution(catalog_id="doc_1", confidence=0.9),
    )
    store.append_turn(
        "1:2",
        user_text="вопрос",
        answer="ответ",
        prepared_plan=plan,
    )
    turn = store.get("1:2").last_turn
    assert turn is not None
    assert turn.resolved_doc_id == "doc_1"
    assert turn.point_number_hints == ("24",)
    assert turn.resolve_unambiguous is True
