from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from mr_norm.apps.vk_bot import MRNormVKBot, parse_deep_research_query
from mr_norm.runtime.contracts import Citation, PipelineResult, PreparedQueryPlan
from mr_norm.runtime.deep_research_memory import DeepResearchSessionStore, PendingDeepResearchSession
from mr_norm.runtime.dialog_memory import DialogSessionStore, DialogSourceReference
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.skills.deep_research import DeepResearchResult, ResearchAnalysis, ResearchDocumentCandidate
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


def test_vk_answer_retries_connection_reset() -> None:
    message = MagicMock(peer_id=100, from_id=200)
    message.answer = AsyncMock(side_effect=[ConnectionResetError(10054, "connection reset"), "ok"])

    result = asyncio.run(MRNormVKBot._vk_answer(message, "ответ"))

    assert result == "ok"
    assert message.answer.await_count == 2


def test_process_dialog_message_starts_new_dialog_on_leading_dot() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._research_sessions = DeepResearchSessionStore()
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
    bot._research_sessions = DeepResearchSessionStore()
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
    bot._research_sessions = DeepResearchSessionStore()
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
    bot._research_sessions = DeepResearchSessionStore()
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
    bot._research_sessions = DeepResearchSessionStore()
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
    bot._research_sessions = DeepResearchSessionStore()
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


def _deep_research_broad_result() -> DeepResearchResult:
    return DeepResearchResult(
        stage="awaiting_selection",
        question="требования к хранению",
        analysis=ResearchAnalysis(
            normalized_question="Требования к хранению проектной документации",
            aspects=("хранение",),
            terms=("ПТЭ",),
            search_queries=("требования хранение",),
        ),
        candidates=(
            ResearchDocumentCandidate(
                index=1,
                doc_id="doc_pte",
                doc_name="ПТЭ",
                point_numbers=("10",),
                fragment_count=3,
                reason="релевантность",
                max_score=1.0,
            ),
            ResearchDocumentCandidate(
                index=2,
                doc_id="doc_gost",
                doc_name="ГОСТ",
                point_numbers=("3",),
                fragment_count=2,
                reason="релевантность",
                max_score=0.8,
            ),
        ),
    )


def test_dispatch_deep_research_prefix_starts_broad_stage() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._research_sessions = DeepResearchSessionStore()
    bot._vk_answer = AsyncMock()
    bot._send_long_text = AsyncMock()
    bot._run_deep_research_broad = AsyncMock(return_value=_deep_research_broad_result())

    message = MagicMock(peer_id=100, from_id=200, text="/ требования к хранению", out=False)
    asyncio.run(bot._process_deep_research_start(message, "требования к хранению"))

    bot._run_deep_research_broad.assert_awaited_once_with("требования к хранению")
    assert bot._research_sessions.has_pending("100:200")
    bot._send_long_text.assert_awaited()


def test_process_research_selection_completes_and_clears_pending() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._research_sessions = DeepResearchSessionStore()
    bot._dialog_sessions = DialogSessionStore()
    bot._vk_answer = AsyncMock()
    bot._send_long_text = AsyncMock()

    pending = PendingDeepResearchSession(
        session_key="100:200",
        original_query="требования",
        analysis=_deep_research_broad_result().analysis,  # type: ignore[arg-type]
        candidates=_deep_research_broad_result().candidates,
        broad_evidence=(),
        search_queries=("требования",),
    )
    bot._research_sessions.set(pending)

    completed = DeepResearchResult(
        stage="completed",
        question="требования",
        answer="Аналитическая записка готова",
        citations=(),
    )
    bot._run_deep_research_deep_dive = AsyncMock(return_value=completed)

    message = MagicMock(peer_id=100, from_id=200)
    asyncio.run(bot._process_research_selection(message, "1, 2"))

    bot._run_deep_research_deep_dive.assert_awaited_once()
    assert not bot._research_sessions.has_pending("100:200")
    bot._send_long_text.assert_awaited()


def test_reset_dot_clears_pending_research() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._dialog_sessions = DialogSessionStore()
    bot._research_sessions = DeepResearchSessionStore()
    bot._research_sessions.set(
        PendingDeepResearchSession(
            session_key="100:200",
            original_query="q",
            analysis=ResearchAnalysis("q", ("a",), ("t",), ("q",)),
            candidates=(),
            broad_evidence=(),
            search_queries=("q",),
        )
    )
    bot._vk_answer = AsyncMock()
    bot._process_search = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text=".")
    asyncio.run(bot._process_dialog_message(message, "."))

    assert not bot._research_sessions.has_pending("100:200")
    bot._process_search.assert_not_awaited()


def test_pending_research_intercepts_plain_message() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._research_sessions = DeepResearchSessionStore()
    bot._dialog_sessions = DialogSessionStore()
    bot._process_research_selection = AsyncMock()

    bot._research_sessions.set(
        PendingDeepResearchSession(
            session_key="100:200",
            original_query="q",
            analysis=ResearchAnalysis("q", ("a",), ("t",), ("q",)),
            candidates=(
                ResearchDocumentCandidate(1, "doc1", "ПТЭ", ("1",), 1, "r", 1.0),
            ),
            broad_evidence=(),
            search_queries=("q",),
        )
    )

    message = MagicMock(peer_id=100, from_id=200, text="1")
    asyncio.run(bot._process_dialog_message(message, "1"))

    bot._process_research_selection.assert_awaited_once()


def test_parse_deep_research_query_spaced_form() -> None:
    query, error = parse_deep_research_query("/ как оформляются диспетчерские заявки?")
    assert error == ""
    assert query == "как оформляются диспетчерские заявки?"


def test_parse_deep_research_query_compact_form() -> None:
    query, error = parse_deep_research_query("/как оформляются диспетчерские заявки?")
    assert error == ""
    assert query == "как оформляются диспетчерские заявки?"


def test_parse_deep_research_query_single_slash_returns_hint() -> None:
    query, error = parse_deep_research_query("/")
    assert query is None
    assert "Укажите вопрос" in error


def test_parse_deep_research_query_known_command_not_research() -> None:
    query, error = parse_deep_research_query("/help")
    assert query is None
    assert error == ""


def test_dispatch_compact_deep_research_prefix() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._research_sessions = DeepResearchSessionStore()
    bot._process_deep_research_start = AsyncMock()
    bot._cmd_welcome = AsyncMock()
    bot._cmd_status = AsyncMock()
    bot._cmd_settings = AsyncMock()
    bot._cmd_set_limit = AsyncMock()
    bot._cmd_set_mode = AsyncMock()
    bot._process_search = AsyncMock()
    bot._process_dialog_message = AsyncMock()
    bot._vk_answer = AsyncMock()

    message = MagicMock(
        peer_id=100,
        from_id=200,
        text="/как оформляются диспетчерские заявки?",
        out=False,
    )
    asyncio.run(bot._dispatch(message))

    bot._process_deep_research_start.assert_awaited_once_with(
        message,
        "как оформляются диспетчерские заявки?",
    )
    bot._vk_answer.assert_not_awaited()


def test_dispatch_spaced_deep_research_prefix() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._process_deep_research_start = AsyncMock()
    bot._cmd_welcome = AsyncMock()
    bot._cmd_status = AsyncMock()
    bot._cmd_settings = AsyncMock()
    bot._cmd_set_limit = AsyncMock()
    bot._cmd_set_mode = AsyncMock()
    bot._process_search = AsyncMock()
    bot._process_dialog_message = AsyncMock()
    bot._vk_answer = AsyncMock()

    message = MagicMock(
        peer_id=100,
        from_id=200,
        text="/ как оформляются диспетчерские заявки?",
        out=False,
    )
    asyncio.run(bot._dispatch(message))

    bot._process_deep_research_start.assert_awaited_once_with(
        message,
        "как оформляются диспетчерские заявки?",
    )


def test_dispatch_known_slash_command_not_deep_research() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._process_deep_research_start = AsyncMock()
    bot._cmd_welcome = AsyncMock()
    bot._cmd_status = AsyncMock()
    bot._cmd_settings = AsyncMock()
    bot._cmd_set_limit = AsyncMock()
    bot._cmd_set_mode = AsyncMock()
    bot._process_search = AsyncMock()
    bot._process_dialog_message = AsyncMock()
    bot._vk_answer = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text="/help", out=False)
    asyncio.run(bot._dispatch(message))

    bot._process_deep_research_start.assert_not_awaited()
    bot._cmd_welcome.assert_awaited_once()


def test_dispatch_single_slash_shows_hint() -> None:
    bot = MRNormVKBot.__new__(MRNormVKBot)
    bot._process_deep_research_start = AsyncMock()
    bot._cmd_welcome = AsyncMock()
    bot._cmd_status = AsyncMock()
    bot._cmd_settings = AsyncMock()
    bot._cmd_set_limit = AsyncMock()
    bot._cmd_set_mode = AsyncMock()
    bot._process_search = AsyncMock()
    bot._process_dialog_message = AsyncMock()
    bot._vk_answer = AsyncMock()

    message = MagicMock(peer_id=100, from_id=200, text="/", out=False)
    asyncio.run(bot._dispatch(message))

    bot._process_deep_research_start.assert_not_awaited()
    bot._vk_answer.assert_awaited_once()
    assert "Укажите вопрос" in str(bot._vk_answer.await_args)
