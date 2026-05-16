from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mr_norm.apps.human_cli import (
    HumanCliOptions,
    build_norm_lookup_request,
    render_query_understanding,
)
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.document_catalog import load_catalog_snapshot
from mr_norm.runtime.contracts import (
    PreparedQueryPlan,
    PreparedToolQuery,
    QueryPlannerTrace,
    QueryUnderstandingResult,
    QueryUnderstandingTrace,
)
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
from tests.test_skills_norm_lookup import make_pipeline_result


def load_sample_catalog():
    path = Path(__file__).parent / "fixtures" / "document_catalog_sample.json"
    return load_catalog_snapshot(path)


def test_build_norm_lookup_request_sets_understand_mode_for_ollama() -> None:
    request = build_norm_lookup_request(
        HumanCliOptions(query="вопрос", mode_preset="ollama")
    )

    assert request.understand_query_mode == "llm"
    assert request.llm_provider == "ollama"


def test_run_norm_lookup_applies_understanding_before_pipeline(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_plan_query(query, **kwargs):
        plan = PreparedQueryPlan(
            original_query=query,
            resolved_doc_names=("ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК",),
            confidence=0.9,
            selected_tools=("payload", "vector"),
            tool_queries=(
                PreparedToolQuery(tool_name="payload", queries=("заземление",)),
                PreparedToolQuery(tool_name="vector", queries=("заземление",)),
            ),
            trace=QueryPlannerTrace(mode=kwargs.get("mode", "auto"), resolver="deterministic"),
        )
        return plan

    def fake_run_pipeline(runtime_request, config, **kwargs):
        seen["runtime_request"] = runtime_request
        return make_pipeline_result()

    monkeypatch.setattr("mr_norm.skills.norm_lookup.plan_query", fake_plan_query)
    monkeypatch.setattr("mr_norm.skills.norm_lookup.run_pipeline", fake_run_pipeline)

    result = run_norm_lookup(
        NormLookupRequest(
            query="расскажи про ПУЭ",
            understand_query_mode="auto",
        ),
        IndexingConfig(collection_name="test_collection"),
    )

    runtime_request = seen["runtime_request"]
    assert runtime_request.filters["doc_name"] == "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"
    assert runtime_request.query == "расскажи про ПУЭ"
    assert runtime_request.prepared_plan is not None
    assert result.understanding is not None


def test_render_query_understanding_shows_resolved_document() -> None:
    rendered = render_query_understanding(
        QueryUnderstandingResult(
            original_query="расскажи про ПУЭ",
            search_query="заземление",
            resolved_doc_names=["ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
            confidence=0.91,
            candidates=[{"doc_name": "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", "score": 0.91, "catalog_id": "doc_pue"}],
        )
    )

    assert "ПОНИМАНИЕ ЗАПРОСА" in rendered
    assert "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК" in rendered
