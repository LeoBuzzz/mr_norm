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
