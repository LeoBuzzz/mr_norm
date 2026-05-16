from __future__ import annotations

from unittest.mock import patch

from mr_norm.apps import main as app_main
from mr_norm.apps.human_cli import (
    HumanCliOptions,
    apply_mode_preset,
    build_norm_lookup_request,
    collect_interactive_options,
    render_human_norm_lookup_result,
)
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
from mr_norm.skills.norm_lookup import NormLookupResult, NormLookupTrace


def make_norm_lookup_result() -> NormLookupResult:
    item = RetrievedItem(
        chunk_id="chunk_1",
        doc_name="ПУЭ",
        point_number="1.7.1",
        text="Требования к заземлению.",
        score=0.8,
        source_tool="vector",
    )
    runtime = RuntimeResult(
        items=[item],
        tool_results={},
        plan=[],
        trace=RuntimeTrace(
            trace_id="trace_1",
            profile="balanced",
            selected_tools=["payload", "vector"],
            fusion="hybrid_rrf",
        ),
        metrics=RuntimeMetrics(elapsed_sec=0.5, tools_planned=2, tools_succeeded=1, items_returned=1),
    )
    pipeline = PipelineResult(
        runtime=runtime,
        planner=PlannerPlan(selected_tools=["payload", "vector"]),
        rerank=RerankResult(items=[item]),
        final_answer=FinalAnswerResult(
            answer="Краткий ответ по норме.",
            citations=[Citation(chunk_id="chunk_1", doc_name="ПУЭ", point_number="1.7.1")],
        ),
        trace=PipelineTrace(
            planner_backend="deterministic",
            reranker_backend="score",
            final_answer_backend="evidence",
        ),
        warnings=["sample warning"],
    )
    return NormLookupResult(
        answer=pipeline.final_answer.answer,
        citations=list(pipeline.final_answer.citations),
        evidence=list(pipeline.rerank.items),
        trace=NormLookupTrace(
            planner_backend="deterministic",
            reranker_backend="score",
            final_answer_backend="evidence",
            runtime_profile="balanced",
            runtime_fusion="hybrid_rrf",
            trace_id="trace_1",
            selected_tools=("payload", "vector"),
        ),
        warnings=["sample warning"],
        pipeline=pipeline,
    )


def test_apply_mode_preset_deterministic() -> None:
    preset = apply_mode_preset("deterministic")

    assert preset["llm_provider"] == "none"
    assert preset["final_answer_backend"] == "evidence"


def test_apply_mode_preset_ollama_defaults_model() -> None:
    preset = apply_mode_preset("ollama")

    assert preset["llm_provider"] == "ollama"
    assert preset["final_answer_backend"] == "prompt"
    assert preset["final_answer_model"] == "qwen3:30b"


def test_build_norm_lookup_request_applies_doc_filter() -> None:
    request = build_norm_lookup_request(
        HumanCliOptions(
            query="заземление",
            mode_preset="deterministic",
            doc_name="ПУЭ",
            limit=3,
        )
    )

    assert request.query == "заземление"
    assert request.filters["doc_name"] == "ПУЭ"
    assert request.limit == 3


def test_render_human_norm_lookup_result_shows_answer_and_sources() -> None:
    rendered = render_human_norm_lookup_result(make_norm_lookup_result())

    assert "ОТВЕТ" in rendered
    assert "Краткий ответ по норме." in rendered
    assert "ИСТОЧНИКИ" in rendered
    assert "chunk_1" in rendered
    assert "ОБРАБОТКА" in rendered
    assert "sample warning" in rendered
    assert '"runtime"' not in rendered


def test_collect_interactive_options_uses_injected_input() -> None:
    answers = iter(["2", "оперативный персонал", "", "5", "balanced"])

    options = collect_interactive_options(
        input_fn=lambda _prompt: next(answers),
        print_fn=lambda _text: None,
    )

    assert options.mode_preset == "ollama"
    assert options.query == "оперативный персонал"
    assert options.limit == 5


def test_norm_lookup_cli_one_shot_prints_human_output(monkeypatch, tmp_path, capsys) -> None:
    def fake_run_human_norm_lookup(options, config, *, keys_path=None, project_paths=None, print_fn=print):
        assert options.query == "заземление"
        assert options.mode_preset == "deterministic"
        print_fn("Параметры запроса:")
        print_fn(render_human_norm_lookup_result(make_norm_lookup_result()))
        return make_norm_lookup_result()

    monkeypatch.setattr(app_main, "run_human_norm_lookup", fake_run_human_norm_lookup)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "norm-lookup",
            "--query",
            "заземление",
            "--mode-preset",
            "deterministic",
            "--collection-name",
            "test_collection",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "ОТВЕТ" in output
    assert "Краткий ответ по норме." in output
    assert '"final_answer"' not in output
