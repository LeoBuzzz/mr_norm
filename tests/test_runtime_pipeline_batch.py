from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import (
    Citation,
    FinalAnswerResult,
    PipelineResult,
    PipelineTrace,
    PlannerPlan,
    RerankResult,
    RuntimeMetrics,
    RuntimeResult,
    RuntimeTrace,
)
from mr_norm.runtime.pipeline import (
    PipelineBatchDefaults,
    render_pipeline_markdown,
    run_pipeline_batch,
    save_pipeline_report,
)
from mr_norm.runtime.pipeline_eval import evaluate_pipeline_result, is_fallback_warning


def make_runtime_result() -> RuntimeResult:
    item = RetrievedItem(
        chunk_id="chunk_1",
        doc_name="ПУЭ",
        point_number="1.7.1",
        text="Требования к заземлению.",
        score=0.8,
        source_tool="payload",
    )
    return RuntimeResult(
        items=[item],
        tool_results={},
        plan=[],
        trace=RuntimeTrace(trace_id="trace_1", profile="balanced", selected_tools=["payload"]),
        metrics=RuntimeMetrics(elapsed_sec=0.01, tools_planned=1, tools_succeeded=1, items_returned=1),
    )


def make_pipeline_result() -> PipelineResult:
    runtime = make_runtime_result()
    return PipelineResult(
        runtime=runtime,
        planner=PlannerPlan(selected_tools=["payload"]),
        rerank=RerankResult(items=runtime.items),
        final_answer=FinalAnswerResult(
            answer="Evidence summary",
            citations=[Citation(chunk_id="chunk_1", doc_name="ПУЭ", point_number="1.7.1")],
        ),
        trace=PipelineTrace(
            planner_backend="deterministic",
            reranker_backend="passthrough",
            final_answer_backend="evidence",
        ),
    )


def test_evaluate_pipeline_result_counts_metrics() -> None:
    evaluation = evaluate_pipeline_result(make_pipeline_result().to_dict())

    assert evaluation["items_returned"] == 1
    assert evaluation["citations_count"] == 1
    assert evaluation["empty_answer"] is False
    assert evaluation["backend_trace"]["planner_backend"] == "deterministic"


def test_is_fallback_warning_detects_prompt_fallbacks() -> None:
    assert is_fallback_warning("prompt final answer failed: ValueError: bad json")
    assert not is_fallback_warning("citation[0]: unknown chunk_id 'chunk_x'")


def test_run_pipeline_batch_aggregates_questions() -> None:
    questions = [
        {"id": "q1", "query": "заземление", "limit": 1},
        {"id": "q2", "query": "изоляция", "limit": 1, "profile": "deep"},
    ]

    with patch("mr_norm.runtime.pipeline.run_pipeline", return_value=make_pipeline_result()):
        report = run_pipeline_batch(
            questions,
            IndexingConfig(collection_name="test_collection"),
            defaults=PipelineBatchDefaults(profile="balanced", limit=5),
        )

    assert report["schema_version"] == "mr_pipeline_batch_v1"
    assert report["questions_total"] == 2
    assert report["metrics"]["questions_total"] == 2
    assert report["questions"][0]["evaluation"]["items_returned"] == 1
    assert report["questions"][1]["request"]["profile"] == "deep"


def test_render_pipeline_batch_markdown_includes_metrics() -> None:
    report = {
        "schema_version": "mr_pipeline_batch_v1",
        "metrics": {"questions_total": 1, "warnings_total": 0, "fallback_total": 0, "empty_answer_rate": 0.0},
        "questions": [
            {
                "id": "q1",
                "evaluation": {
                    "backend_trace": {"planner_backend": "deterministic"},
                    "items_returned": 1,
                    "citations_count": 1,
                    "warnings_count": 0,
                    "fallback_count": 0,
                },
                "result": {"final_answer": {"answer": "ok"}},
            }
        ],
    }

    markdown = render_pipeline_markdown(report)

    assert "RAG Pipeline Batch Report" in markdown
    assert "q1" in markdown


def test_save_pipeline_batch_report_writes_files(tmp_path: Path) -> None:
    report = {"schema_version": "mr_pipeline_batch_v1", "metrics": {}, "questions": []}
    saved = save_pipeline_report(report, tmp_path, prefix="rag_pipeline_batch")

    assert Path(saved["report_path"]).is_file()
    assert Path(saved["markdown_report_path"]).is_file()


def test_rag_pipeline_batch_cli_routes(monkeypatch, tmp_path, capsys) -> None:
    questions_path = tmp_path / "retrieval_questions.json"
    questions_path.write_text(
        json.dumps([{"id": "grounding", "query": "заземление"}], ensure_ascii=False),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_run_pipeline_batch(questions, config, *, defaults, tool_runners=None):
        seen["questions"] = questions
        seen["defaults"] = defaults
        return {"schema_version": "mr_pipeline_batch_v1", "questions": [], "metrics": {}}

    monkeypatch.setattr(app_main, "run_pipeline_batch", fake_run_pipeline_batch)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-pipeline-batch",
            "--questions",
            str(questions_path),
            "--collection-name",
            "test_collection",
            "--profile",
            "balanced",
            "--planner",
            "deterministic",
            "--reranker",
            "score",
            "--final-answer",
            "evidence",
            "--llm-provider",
            "none",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["schema_version"] == "mr_pipeline_batch_v1"
    defaults = seen["defaults"]
    assert defaults.planner_backend == "deterministic"
    assert defaults.reranker_backend == "score"
    assert defaults.llm_provider == "none"
