from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeMetrics, RuntimeRequest, RuntimeResult, RuntimeTrace
from mr_norm.runtime.final_answer import EvidenceOnlyFinalAnswer
from mr_norm.runtime.pipeline import render_pipeline_markdown, run_pipeline, save_pipeline_report
from mr_norm.runtime.planner import DeterministicPlanner
from mr_norm.runtime.reranker import PassthroughReranker, ScoreReranker


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


def test_run_pipeline_wires_runtime_planner_reranker_and_final_answer() -> None:
    request = RuntimeRequest(query="заземление", profile="balanced", limit=1)

    with patch("mr_norm.runtime.pipeline.run_runtime", return_value=make_runtime_result()):
        result = run_pipeline(
            request,
            IndexingConfig(collection_name="test_collection"),
            planner=DeterministicPlanner(),
            reranker=ScoreReranker(),
            final_answer=EvidenceOnlyFinalAnswer(),
        )

    payload = result.to_dict()
    assert payload["trace"]["planner_backend"] == "deterministic"
    assert payload["trace"]["reranker_backend"] == "score"
    assert payload["trace"]["final_answer_backend"] == "evidence"
    assert payload["planner"]["selected_tools"] == ["payload", "vector"]
    assert payload["rerank"]["items"][0]["chunk_id"] == "chunk_1"
    assert payload["final_answer"]["citations"][0]["chunk_id"] == "chunk_1"


def test_render_pipeline_markdown_includes_answer_and_citations() -> None:
    with patch("mr_norm.runtime.pipeline.run_runtime", return_value=make_runtime_result()):
        pipeline = run_pipeline(
            RuntimeRequest(query="заземление", profile="balanced", limit=1),
            IndexingConfig(collection_name="test_collection"),
            planner=DeterministicPlanner(),
            reranker=PassthroughReranker(),
            final_answer=EvidenceOnlyFinalAnswer(),
        )

    markdown = render_pipeline_markdown(pipeline.to_dict())

    assert "RAG Pipeline Report" in markdown
    assert "заземление" in markdown
    assert "chunk_1" in markdown


def test_save_pipeline_report_writes_files(tmp_path: Path) -> None:
    report = {
        "trace": {"planner_backend": "deterministic"},
        "runtime": {"items": [], "trace": {"profile": "balanced"}},
        "final_answer": {"answer": "test", "citations": []},
    }
    saved = save_pipeline_report(report, tmp_path)

    assert Path(saved["report_path"]).is_file()
    assert Path(saved["markdown_report_path"]).is_file()


def test_rag_pipeline_cli_passes_llm_provider_to_factory(monkeypatch, tmp_path, capsys) -> None:
    seen: dict[str, object] = {}

    def fake_build_pipeline_llm_providers(llm_provider, **kwargs):
        seen["llm_provider"] = llm_provider
        seen["kwargs"] = kwargs
        from mr_norm.runtime.llm_providers import PipelineLLMProviders

        return PipelineLLMProviders()

    monkeypatch.setattr(app_main, "build_pipeline_llm_providers", fake_build_pipeline_llm_providers)
    monkeypatch.setattr(
        app_main,
        "run_pipeline",
        lambda *args, **kwargs: type(
            "Pipeline",
            (),
            {
                "to_dict": lambda self: {
                    "trace": {"planner_backend": "deterministic"},
                    "runtime": {"items": [], "trace": {}},
                    "final_answer": {"answer": "", "citations": []},
                }
            },
        )(),
    )

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-pipeline",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--llm-provider",
            "ollama",
            "--planner",
            "prompt",
            "--planner-model",
            "qwen3:30b",
        ]
    )

    assert exit_code == 0
    assert seen["llm_provider"] == "ollama"
    assert seen["kwargs"]["planner_model"] == "qwen3:30b"
    assert seen["kwargs"]["planner_backend"] == "prompt"


def test_rag_pipeline_cli_routes_to_pipeline(monkeypatch, tmp_path, capsys) -> None:
    seen: dict[str, object] = {}

    def fake_run_pipeline(request, config, *, planner, reranker, final_answer):
        seen["request"] = request
        seen["config"] = config
        seen["planner"] = planner.backend_name
        seen["reranker"] = reranker.backend_name
        seen["final_answer"] = final_answer.backend_name
        runtime = make_runtime_result()
        from mr_norm.runtime.contracts import FinalAnswerResult, PipelineResult, PipelineTrace, PlannerPlan, RerankResult

        return PipelineResult(
            runtime=runtime,
            planner=PlannerPlan(selected_tools=["payload"]),
            rerank=RerankResult(items=runtime.items),
            final_answer=FinalAnswerResult(answer="ok", citations=[]),
            trace=PipelineTrace(
                planner_backend=planner.backend_name,
                reranker_backend=reranker.backend_name,
                final_answer_backend=final_answer.backend_name,
            ),
        )

    monkeypatch.setattr(app_main, "run_pipeline", fake_run_pipeline)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-pipeline",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--profile",
            "balanced",
            "--planner",
            "deterministic",
            "--reranker",
            "score",
            "--final-answer",
            "evidence",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["request"].query == "заземление"
    assert seen["planner"] == "deterministic"
    assert seen["reranker"] == "score"
    assert seen["final_answer"] == "evidence"
    assert output["trace"]["planner_backend"] == "deterministic"
