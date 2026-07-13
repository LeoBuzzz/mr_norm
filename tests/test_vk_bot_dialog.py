from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from mr_norm.apps.vk_bot import MRNormVKBot
from mr_norm.runtime.contracts import Citation, PipelineResult, PreparedQueryPlan
from mr_norm.runtime.dialog_memory import DialogSessionStore, DialogSourceReference
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.skills.norm_lookup import NormLookupResult, NormLookupTrace


def _norm_lookup_result(answer: str = "ответ") -> NormLookupResult:
    pipeline = PipelineResult(
        runtime=MagicMock(),
        planner=MagicMock(),
        rerank=MagicMock(),
        final_answer=MagicMock(answer=answer, citations=[], warnings=[]),
        trace=MagicMock(),
        warnings=[],
        diagnostics={},
    )
    return NormLookupResult(
        answer=answer,
        citations=[Citation(chunk_id="chunk_1", doc_name="Правила", point_number="24")],
        evidence=[],
        trace=NormLookupTrace(
            planner_backend="deterministic",
            reranker_backend="score",
            final_answer_backend="evidence",
            runtime_profile="balanced",
            runtime_fusion="",
            trace_id="test",
        ),
        warnings=[],
        pipeline=pipeline,
        prepared_plan=PreparedQueryPlan(original_query="вопрос"),
    )


def test_process_dialog_message_starts_new_dialog_on_leading_dot() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._vk_answer = AsyncMock()
    bot._process_search = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text=".новый вопрос")
    asyncio.run(bot._process_dialog_message(message, ".новый вопрос"))

    bot._process_search.assert_awaited_once()
    args, kwargs = bot._process_search.await_args
    assert args[1] == "новый вопрос"
    assert kwargs["dialog_context"] is None
    assert bot._dialog_sessions.get("100:200").turns == ()


def test_process_dialog_message_reset_only() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._dialog_sessions.append_turn("100:200", user_text="q", answer="a")
    bot._vk_answer = AsyncMock()
    bot._process_search = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text=".")
    asyncio.run(bot._process_dialog_message(message, "."))

    bot._process_search.assert_not_awaited()
    assert bot._dialog_sessions.get("100:200").turns == ()
    bot._vk_answer.assert_awaited()


def test_process_dialog_message_continues_existing_dialog() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._dialog_sessions.append_turn("100:200", user_text="первый", answer="a1")
    bot._vk_answer = AsyncMock()
    bot._process_search = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text="как часто?")
    asyncio.run(bot._process_dialog_message(message, "как часто?"))

    _, kwargs = bot._process_search.await_args
    assert kwargs["dialog_context"] is not None
    assert len(kwargs["dialog_context"].turns) == 1


def test_process_search_records_source_references() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._vk_answer = AsyncMock()
    result = _norm_lookup_result("готовый ответ")
    result = NormLookupResult(
        answer=result.answer,
        citations=[
            Citation(
                chunk_id="chunk_65",
                doc_name="Правила технической эксплуатации",
                point_number="65",
            )
        ],
        evidence=[
            RetrievedItem(
                chunk_id="chunk_65",
                doc_id="doc_pte",
                doc_name="Правила технической эксплуатации",
                point_number="65",
                text="65. Текст пункта.",
            )
        ],
        trace=result.trace,
        warnings=result.warnings,
        pipeline=result.pipeline,
        prepared_plan=result.prepared_plan,
    )
    bot._run_norm_lookup = AsyncMock(return_value=result)

    message = MagicMock(peer_id=100, from_id=200)
    asyncio.run(
        bot._process_search(
            message,
            "Дай текст п. 65",
            session_key="100:200",
            stored_user_text="Дай текст п. 65",
            retrieval_query="Дай текст п. 65",
        )
    )

    turn = bot._dialog_sessions.get("100:200").last_turn
    assert turn is not None
    assert len(turn.source_references) == 1
    assert turn.source_references[0].doc_id == "doc_pte"
    assert turn.source_references[0].point_number == "65"


def test_process_search_records_turn_after_answer() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._vk_answer = AsyncMock()
    bot._run_norm_lookup = AsyncMock(return_value=_norm_lookup_result("готовый ответ"))

    message = MagicMock(peer_id=100, from_id=200)
    asyncio.run(
        bot._process_search(
            message,
            "как часто?",
            session_key="100:200",
            stored_user_text="как часто?",
            retrieval_query="первый как часто?",
        )
    )

    context = bot._dialog_sessions.get("100:200")
    assert len(context.turns) == 1
    assert context.turns[0].user_text == "как часто?"
    assert context.turns[0].answer == "готовый ответ"


def test_users_in_same_peer_have_isolated_sessions() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._vk_answer = AsyncMock()
    bot._process_search = AsyncMock()

    async def run_both() -> None:
        await bot._process_dialog_message(MagicMock(peer_id=100, from_id=1), "первый")
        await bot._process_dialog_message(MagicMock(peer_id=100, from_id=2), "второй")

    asyncio.run(run_both())

    assert bot._process_search.await_count == 2
    first_kwargs = bot._process_search.await_args_list[0].kwargs
    second_kwargs = bot._process_search.await_args_list[1].kwargs
    assert first_kwargs["dialog_context"] is None
    assert second_kwargs["dialog_context"] is None
