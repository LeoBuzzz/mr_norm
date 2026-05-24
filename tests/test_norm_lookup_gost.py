from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.gost_definitions import GostSnippet
from mr_norm.runtime.contracts import PreparedQueryPlan, QueryPlannerTrace, DocumentResolution
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
from tests.test_skills_norm_lookup import make_pipeline_result


def test_run_norm_lookup_prefetches_and_merges_gost() -> None:
    gost = GostSnippet(
        keyword="оперативный персонал",
        text="107 оперативный персонал: тест",
        doc_name="ГОСТ Р 57114-2022",
        point_number="107",
        chunk_id="chunk_gost",
        doc_id="doc_4745dec28ca589e1",
        score=0.9,
    )
    plan = PreparedQueryPlan(
        original_query="дай определение оперативного персонала",
        gost_snippets=(gost.to_dict(),),
        trace=QueryPlannerTrace(mode="llm", resolver="llm"),
    )

    with (
        patch("mr_norm.skills.norm_lookup.prefetch_gost_snippets", return_value=[gost]),
        patch("mr_norm.skills.norm_lookup.plan_query", return_value=plan),
        patch("mr_norm.skills.norm_lookup.run_pipeline", return_value=make_pipeline_result()),
    ):
        result = run_norm_lookup(
            NormLookupRequest(
                query="дай определение оперативного персонала",
                understand_query_mode="llm",
                llm_provider="polza",
            ),
            IndexingConfig(collection_name="test_collection"),
        )

    assert result.trace.gost_prefetch_count == 1
    assert result.evidence[0].source_tool == "gost_definition"
    assert result.gost_snippets
