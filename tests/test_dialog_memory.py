from __future__ import annotations

import pytest

from mr_norm.runtime.contracts import PreparedQueryPlan, DocumentResolution, Citation
from mr_norm.runtime.dialog_memory import (
    DialogContext,
    DialogSessionStore,
    DialogSourceReference,
    DialogTurn,
    apply_dialog_followup_filters,
    build_retrieval_query,
    build_session_key,
    extract_source_references_from_citations,
    is_follow_up_query,
    parse_dialog_input,
    query_has_explicit_document,
    query_has_explicit_point,
)
from mr_norm.retrieval.contracts import RetrievedItem


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


def test_apply_dialog_followup_inherits_doc_for_point_only_query() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="что такое оперативный персонал",
                answer="определение",
                resolved_doc_id="doc_pte",
                resolved_doc_names=("Правила технической эксплуатации",),
                resolve_unambiguous=True,
            ),
        ),
    )
    filters, warnings = apply_dialog_followup_filters("Дай текст п. 65", {}, context)
    assert filters["doc_id"] == "doc_pte"
    assert filters["point_number"] == "65"
    assert "dialog:inherited_document_context" in warnings


def test_apply_dialog_followup_resolves_doc_from_prior_citation() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="что такое оперативный персонал",
                answer="определение",
                source_references=(
                    DialogSourceReference(
                        doc_id="doc_pte",
                        doc_name="Правила технической эксплуатации",
                        point_number="65",
                    ),
                ),
            ),
        ),
    )
    filters, warnings = apply_dialog_followup_filters("Дай текст п. 65", {}, context)
    assert filters["doc_id"] == "doc_pte"
    assert filters["point_number"] == "65"
    assert "dialog:resolved_document_from_prior_citation" in warnings


def test_apply_dialog_followup_ambiguous_prior_point_sources() -> None:
    context = DialogContext(
        session_key="1:2",
        turns=(
            DialogTurn(
                user_text="вопрос",
                answer="ответ",
                source_references=(
                    DialogSourceReference(doc_id="doc_a", doc_name="A", point_number="65"),
                    DialogSourceReference(doc_id="doc_b", doc_name="B", point_number="65"),
                ),
            ),
        ),
    )
    filters, warnings = apply_dialog_followup_filters("Дай текст п. 65", {}, context)
    assert "doc_id" not in filters
    assert filters["point_number"] == "65"
    assert "dialog:ambiguous_prior_point_sources" in warnings


def test_apply_dialog_followup_requires_unambiguous_prior_doc() -> None:
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
    filters, warnings = apply_dialog_followup_filters("как часто?", {}, context)
    assert filters["doc_id"] == "doc_1"
    assert "dialog:inherited_document_context" in warnings


def test_apply_dialog_followup_skips_when_new_doc_mentioned() -> None:
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
    filters, warnings = apply_dialog_followup_filters("а в ПУЭ?", {}, context)
    assert "doc_id" not in filters
    assert warnings == []


def test_is_follow_up_query() -> None:
    assert is_follow_up_query("как часто?") is True
    assert is_follow_up_query("а в ПУЭ?") is True
    assert is_follow_up_query("Дай текст п. 65") is True
    assert query_has_explicit_point("пункт 24 правил работы с персоналом") is True
    assert query_has_explicit_document("а в ПУЭ?") is True


def test_extract_source_references_from_citations() -> None:
    item = RetrievedItem(
        chunk_id="chunk_65",
        doc_id="doc_pte",
        doc_name="Правила технической эксплуатации",
        point_number="65",
    )
    refs = extract_source_references_from_citations(
        citations=(Citation(chunk_id="chunk_65", doc_name="Правила технической эксплуатации", point_number="65"),),
        evidence_by_chunk_id={"chunk_65": item},
    )
    assert len(refs) == 1
    assert refs[0].doc_id == "doc_pte"
    assert refs[0].point_number == "65"


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
