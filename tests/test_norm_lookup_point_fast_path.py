from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.runtime.contracts import PreparedQueryPlan
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
from mr_norm.skills.point_lookup import PointLookupResult, PointLookupPart
from mr_norm.runtime.contracts import Citation


def test_run_norm_lookup_uses_point_lookup_fast_path() -> None:
    prepared = PreparedQueryPlan(
        original_query="пункт 24 правил работы с персоналом",
        question_type="point_lookup",
        point_number_hints=("24",),
    )
    point_result = PointLookupResult(
        found=True,
        doc_id="doc_796",
        doc_name="Об утверждении Правил работы с персоналом",
        point_number="24",
        text="24. Инструктаж проводится в установленном порядке.",
        answer="п. 24 — Об утверждении Правил работы с персоналом\n\n24. Инструктаж проводится в установленном порядке.",
        status="exact",
        parts=(PointLookupPart(chunk_id="chunk_24", part_index=0, text="24. Инструктаж..."),),
        citations=(Citation(chunk_id="chunk_24", doc_name="Правила", point_number="24"),),
    )

    with patch("mr_norm.skills.norm_lookup.plan_query", return_value=prepared):
        with patch("mr_norm.skills.norm_lookup.apply_prepared_plan", return_value=("query", {"doc_id": "doc_796", "point_number": "24"})):
            with patch("mr_norm.skills.norm_lookup.prefetch_gost_snippets", return_value=[]):
                with patch("mr_norm.skills.norm_lookup.lookup_point", return_value=point_result):
                    with patch("mr_norm.skills.norm_lookup.run_pipeline") as run_pipeline:
                        result = run_norm_lookup(
                            NormLookupRequest(
                                query="пункт 24 правил работы с персоналом",
                                understand_query_mode="auto",
                            ),
                            IndexingConfig(collection_name="test"),
                        )

    run_pipeline.assert_not_called()
    assert "24. Инструктаж проводится" in result.answer
    assert result.trace.final_answer_backend == "verbatim_point"
    assert "norm_lookup:point_lookup_fast_path" in result.warnings
