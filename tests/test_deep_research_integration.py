from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import Citation
from mr_norm.runtime.deep_research_memory import PendingDeepResearchSession
from mr_norm.skills.deep_research import (
    DeepResearchRequest,
    ResearchAnalysis,
    ResearchDocumentCandidate,
    build_pending_session,
    run_deep_research_broad,
    run_deep_research_deep_dive,
)


def _sample_analysis() -> ResearchAnalysis:
    return ResearchAnalysis(
        normalized_question="Требования к хранению проектной документации",
        aspects=("хранение", "сроки"),
        terms=("ПТЭ", "архив"),
        search_queries=("требования хранение документации", "срок хранения проектной документации"),
    )


def test_run_deep_research_broad_awaiting_selection() -> None:
    analysis = _sample_analysis()
    evidence = [
        RetrievedItem(chunk_id="c1", doc_id="doc_pte", doc_name="ПТЭ", point_number="10", score=0.9, text="хранение"),
        RetrievedItem(chunk_id="c2", doc_id="doc_gost", doc_name="ГОСТ", point_number="3", score=0.7, text="архив"),
    ]

    with patch("mr_norm.skills.deep_research.analyze_research_question", return_value=(analysis, [])):
        with patch(
            "mr_norm.skills.deep_research.run_broad_research_search",
            return_value=(evidence, [], {"search_queries": list(analysis.search_queries)}),
        ):
            result = run_deep_research_broad(
                DeepResearchRequest(query="требования к хранению", llm_provider="polza"),
                IndexingConfig(collection_name="test"),
            )

    assert result.stage == "awaiting_selection"
    assert len(result.candidates) == 2
    pending = build_pending_session(result, session_key="1:2")
    assert pending is not None
    assert pending.original_query == "требования к хранению"


def test_run_deep_research_broad_no_evidence() -> None:
    analysis = _sample_analysis()
    with patch("mr_norm.skills.deep_research.analyze_research_question", return_value=(analysis, [])):
        with patch(
            "mr_norm.skills.deep_research.run_broad_research_search",
            return_value=([], [], {}),
        ):
            result = run_deep_research_broad(
                DeepResearchRequest(query="требования", llm_provider="polza"),
                IndexingConfig(collection_name="test"),
            )
    assert result.stage == "no_evidence"


def test_run_deep_research_deep_dive_completed() -> None:
    session = PendingDeepResearchSession(
        session_key="1:2",
        original_query="требования к хранению",
        analysis=_sample_analysis(),
        candidates=(
            ResearchDocumentCandidate(
                index=1,
                doc_id="doc_pte",
                doc_name="ПТЭ",
                point_numbers=("10",),
                fragment_count=2,
                reason="reason",
                max_score=1.0,
            ),
        ),
        broad_evidence=(
            RetrievedItem(chunk_id="c1", doc_id="doc_pte", doc_name="ПТЭ", point_number="10", score=0.9, text="хранение"),
        ),
        search_queries=_sample_analysis().search_queries,
    )

    memo_evidence = [
        RetrievedItem(chunk_id="c1", doc_id="doc_pte", doc_name="ПТЭ", point_number="10", score=0.9, text="хранение"),
    ]

    with patch("mr_norm.skills.deep_research._run_doc_scoped_search", return_value=(memo_evidence, [])):
        with patch(
            "mr_norm.skills.deep_research.generate_research_memo",
            return_value=(
                "РЕЗЮМЕ\nТребуется хранение.",
                [Citation(chunk_id="c1", doc_name="ПТЭ", point_number="10")],
                [],
            ),
        ):
            result = run_deep_research_deep_dive(
                session,
                (1,),
                IndexingConfig(collection_name="test"),
                llm_provider="polza",
                tool_runners={},
            )

    assert result.stage == "completed"
    assert "РЕЗЮМЕ" in result.answer
    assert len(result.citations) == 1


def test_run_deep_research_deep_dive_no_valid_citations() -> None:
    session = PendingDeepResearchSession(
        session_key="1:2",
        original_query="требования",
        analysis=_sample_analysis(),
        candidates=(
            ResearchDocumentCandidate(
                index=1,
                doc_id="doc_pte",
                doc_name="ПТЭ",
                point_numbers=("10",),
                fragment_count=1,
                reason="reason",
                max_score=1.0,
            ),
        ),
        broad_evidence=(
            RetrievedItem(chunk_id="c1", doc_id="doc_pte", doc_name="ПТЭ", point_number="10", text="хранение"),
        ),
        search_queries=_sample_analysis().search_queries,
    )

    with patch("mr_norm.skills.deep_research._run_doc_scoped_search", return_value=(list(session.broad_evidence), [])):
        with patch(
            "mr_norm.skills.deep_research.generate_research_memo",
            return_value=("Ответ без ссылок", [], ["deep_research:no_valid_citations"]),
        ):
            result = run_deep_research_deep_dive(
                session,
                (1,),
                IndexingConfig(collection_name="test"),
                llm_provider="polza",
            )

    assert result.stage == "error"
    assert "проверку ссылок" in result.answer


def test_analyze_research_question_uses_only_primary_model() -> None:
    from mr_norm.runtime.llm_profiles import resolve_role_models

    models = resolve_role_models("polza", "deep_research_analysis")
    assert models[0] == "anthropic/claude-sonnet-4.6"
    assert len(models) == 1
