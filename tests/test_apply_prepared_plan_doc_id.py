from __future__ import annotations

from mr_norm.runtime.contracts import DocumentResolution, PreparedQueryPlan
from mr_norm.runtime.query_planner import apply_prepared_plan


def test_apply_prepared_plan_prefers_doc_id_filter() -> None:
    plan = PreparedQueryPlan(
        original_query="Какой закон об электроэнергетике?",
        resolved_doc_names=("Об электроэнергетике",),
        document_resolution=DocumentResolution(
            catalog_id="doc_7892b1f4e4994568",
            doc_name="Об электроэнергетике",
            confidence=0.88,
            ambiguous=False,
        ),
        ambiguous=False,
    )
    _, filters = apply_prepared_plan("вопрос", {}, plan)
    assert filters.get("doc_id") == "doc_7892b1f4e4994568"
    assert "doc_name" not in filters


def test_apply_prepared_plan_skips_point_number_without_doc_scope() -> None:
    plan = PreparedQueryPlan(
        original_query="Что в пункте 34?",
        point_number_hints=("34",),
        ambiguous=True,
    )
    _, filters = apply_prepared_plan("вопрос", {}, plan)
    assert "point_number" not in filters


def test_apply_prepared_plan_adds_point_number_with_doc_scope() -> None:
    plan = PreparedQueryPlan(
        original_query="Что в пункте 34?",
        resolved_doc_names=("О розничных рынках",),
        point_number_hints=("34",),
        document_resolution=DocumentResolution(
            catalog_id="doc_retail",
            doc_name="О розничных рынках",
            confidence=0.9,
            ambiguous=False,
        ),
        ambiguous=False,
    )
    _, filters = apply_prepared_plan("вопрос", {}, plan)
    assert filters.get("doc_id") == "doc_retail"
    assert filters.get("point_number") == "34"
