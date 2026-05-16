from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.runtime.contracts import RuntimeRequest, ToolCallPlan
from mr_norm.runtime.tool_runner import (
    render_runtime_markdown,
    run_runtime,
    run_runtime_batch,
    runtime_request_from_question,
    save_runtime_report,
)


def make_tool_result(
    tool_name: str,
    chunk_ids: list[str],
    request: ToolRequest,
    config: IndexingConfig,
    *,
    warnings: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        items=[RetrievedItem(chunk_id=chunk_id, source_tool=tool_name) for chunk_id in chunk_ids],
        trace=ToolTrace(
            tool_name=tool_name,
            collection_name=config.collection_name,
            vector_name=config.vector_name,
            query=request.query,
            normalized_filters=request.filters,
            limit=request.limit,
            profile=request.profile,
            trace_id=request.trace_id,
        ),
        metrics=ToolMetrics(elapsed_sec=0.001, candidates_returned=len(chunk_ids), qdrant_calls=1),
        warnings=warnings or [],
    )


def test_run_runtime_fuses_multiple_tools_for_balanced_profile() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_b"], req, cfg)

    def vector_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("vector", ["chunk_a"], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="balanced", limit=3),
        config,
        tool_runners={"payload": payload_runner, "vector": vector_runner},
    )

    assert result.trace.fusion == "hybrid_rrf"
    assert {item.chunk_id for item in result.items} == {"chunk_a", "chunk_b"}
    assert result.metrics.tools_planned == 2


def test_run_runtime_returns_contract_fields() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_1"], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="fast", limit=2),
        config,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    )
    payload = result.to_dict()

    assert payload["items"][0]["chunk_id"] == "chunk_1"
    assert "payload" in payload["tool_results"]
    assert payload["trace"]["profile"] == "fast"
    assert payload["metrics"]["items_returned"] == 1


def test_run_runtime_batch_reuses_runners(monkeypatch) -> None:
    config = IndexingConfig(collection_name="test_collection")
    calls = {"count": 0}

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        calls["count"] += 1
        return make_tool_result("payload", [f"{req.trace_id}_chunk"], req, cfg)

    report = run_runtime_batch(
        [{"id": "q1", "query": "заземление"}, {"id": "q2", "query": "изоляция"}],
        config,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    )

    assert report["schema_version"] == "mr_runtime_batch_v1"
    assert report["questions_total"] == 2
    assert calls["count"] == 4


def test_save_runtime_report_writes_json_and_markdown(tmp_path) -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_1"], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="fast"),
        config,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    ).to_dict()

    saved = save_runtime_report(result, tmp_path)

    assert saved["report_path"].endswith(".json")
    assert saved["markdown_report_path"].endswith(".md")
    assert json.loads((tmp_path / saved["report_path"].split("\\")[-1]).read_text(encoding="utf-8"))["items"]


def test_rag_runtime_cli_routes_to_runner(monkeypatch, tmp_path, capsys) -> None:
    seen = {}

    def fake_run_runtime(request: RuntimeRequest, config: IndexingConfig) -> object:
        seen["request"] = request
        seen["config"] = config
        return type(
            "Result",
            (),
            {
                "to_dict": lambda self: {
                    "items": [],
                    "tool_results": {},
                    "plan": [],
                    "trace": {"profile": request.profile, "mode": request.mode, "selected_tools": []},
                    "metrics": {"elapsed_sec": 0.0, "tools_planned": 0, "tools_succeeded": 0, "items_returned": 0},
                    "warnings": [],
                }
            },
        )()

    monkeypatch.setattr(app_main, "run_runtime", fake_run_runtime)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-runtime",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--profile",
            "balanced",
            "--doc-name",
            "ПУЭ",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["request"].query == "заземление"
    assert seen["request"].filters == {"doc_name": "ПУЭ"}
    assert seen["request"].profile == "balanced"
    assert output["trace"]["profile"] == "balanced"


GOLDEN_QUESTIONS_PATH = Path(__file__).resolve().parent / "fixtures" / "retrieval_questions.json"


def test_run_runtime_batch_golden_questions_fixture() -> None:
    questions = json.loads(GOLDEN_QUESTIONS_PATH.read_text(encoding="utf-8"))
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", [f"{req.trace_id}_chunk"], req, cfg)

    def vector_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("vector", [f"{req.trace_id}_vector_chunk"], req, cfg)

    def point_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        point = str(req.filters.get("point_number") or "point")
        return make_tool_result("point", [f"{point}_chunk"], req, cfg)

    report = run_runtime_batch(
        questions,
        config,
        profile="balanced",
        limit=5,
        tool_runners={"point": point_runner, "payload": payload_runner, "vector": vector_runner},
    )

    assert report["schema_version"] == "mr_runtime_batch_v1"
    assert report["profile"] == "balanced"
    assert report["questions_total"] == len(questions)
    reported_ids = {entry["id"] for entry in report["questions"]}
    assert reported_ids == {question["id"] for question in questions}
    for entry in report["questions"]:
        result = entry["result"]
        assert result["trace"]["profile"] == "balanced"
        assert "tool_results" in result
        assert "metrics" in result
        assert "warnings" in result
        if entry["request"]["query"].strip():
            assert result["trace"]["fusion"] == "hybrid_rrf"
            assert result["items"]


def test_rag_runtime_batch_cli_loads_questions_and_can_save_report(monkeypatch, tmp_path, capsys) -> None:
    questions_path = tmp_path / "retrieval_questions.json"
    questions_path.write_text(
        json.dumps([{"id": "grounding", "query": "заземление", "filters": {"doc_name": "ПУЭ"}}], ensure_ascii=False),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_run_runtime_batch(
        questions: list[dict],
        config: IndexingConfig,
        *,
        profile: str,
        limit: int,
        tool_runners: object = None,
    ) -> dict:
        seen["questions"] = questions
        seen["config"] = config
        seen["profile"] = profile
        seen["limit"] = limit
        return {
            "schema_version": "mr_runtime_batch_v1",
            "collection_name": config.collection_name,
            "vector_name": config.vector_name,
            "profile": profile,
            "questions_total": len(questions),
            "questions": [],
            "metrics": {"elapsed_sec": 0.001, "questions_total": len(questions)},
            "warnings": [],
        }

    monkeypatch.setattr(app_main, "run_runtime_batch", fake_run_runtime_batch)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-runtime-batch",
            "--collection-name",
            "test_collection",
            "--questions",
            str(questions_path),
            "--profile",
            "deep",
            "--limit",
            "7",
            "--save-report",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["questions"][0]["query"] == "заземление"
    assert seen["config"].collection_name == "test_collection"
    assert seen["profile"] == "deep"
    assert seen["limit"] == 7
    assert output["report_path"].endswith(".json")
    assert output["markdown_report_path"].endswith(".md")


def test_run_runtime_warns_on_unknown_tool_in_plan() -> None:
    config = IndexingConfig(collection_name="test_collection")
    fake_plan = [
        ToolCallPlan(
            tool_name="unknown_tool",
            request=ToolRequest(query="заземление", profile="balanced"),
            reason="injected unknown tool",
            priority=0,
        )
    ]

    with patch("mr_norm.runtime.tool_runner.route_runtime", return_value=(fake_plan, [])):
        result = run_runtime(RuntimeRequest(query="заземление", profile="balanced"), config, tool_runners={})

    assert "unknown runtime tool: unknown_tool" in result.warnings
    assert result.items == []
    assert result.trace.empty_reason == "no_runtime_matches"


def test_run_runtime_continues_when_one_tool_raises() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def failing_payload(_req: ToolRequest, _cfg: IndexingConfig) -> ToolResult:
        raise RuntimeError("qdrant unavailable")

    def vector_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("vector", ["chunk_ok"], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="balanced", limit=3),
        config,
        tool_runners={"payload": failing_payload, "vector": vector_runner},
    )

    assert "payload failed: RuntimeError: qdrant unavailable" in result.warnings
    assert result.items[0].chunk_id == "chunk_ok"


def test_run_runtime_empty_tool_results_set_no_runtime_matches() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def empty_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", [], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="balanced"),
        config,
        tool_runners={"payload": empty_runner, "vector": empty_runner},
    )

    assert result.items == []
    assert result.trace.empty_reason == "no_runtime_matches"


def test_run_runtime_empty_tool_plan_sets_empty_tool_plan_reason() -> None:
    config = IndexingConfig(collection_name="test_collection")

    with patch("mr_norm.runtime.tool_runner.route_runtime", return_value=([], ["runtime requires a non-empty query"])):
        result = run_runtime(RuntimeRequest(), config)

    assert result.trace.empty_reason == "empty_tool_plan"
    assert result.metrics.tools_planned == 0


def test_run_runtime_propagates_source_tool_warnings() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_1"], req, cfg, warnings=["payload warning"])

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="fast"),
        config,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    )

    assert "payload warning" in result.warnings


def test_run_runtime_fast_does_not_add_hybrid_even_with_multiple_tools() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_b"], req, cfg)

    def vector_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("vector", ["chunk_a"], req, cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="fast", limit=3),
        config,
        tool_runners={"payload": payload_runner, "vector": vector_runner},
    )

    assert result.trace.fusion == ""
    assert "hybrid" not in result.tool_results
    assert result.items[0].chunk_id == "chunk_b"


def test_run_runtime_balanced_builds_hybrid_when_multiple_tools_are_invoked() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_only"], req, cfg)

    def empty_vector(_req: ToolRequest, _cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("vector", [], _req, _cfg)

    result = run_runtime(
        RuntimeRequest(query="заземление", profile="balanced"),
        config,
        tool_runners={"payload": payload_runner, "vector": empty_vector},
    )

    assert result.trace.fusion == "hybrid_rrf"
    assert "hybrid" in result.tool_results
    assert result.items[0].chunk_id == "chunk_only"


def test_run_runtime_without_hybrid_prefers_point_over_payload() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def point_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("point", ["chunk_point"], req, cfg)

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_payload"], req, cfg)

    result = run_runtime(
        RuntimeRequest(
            query="",
            filters={"doc_name": "ПУЭ", "point_number": "1.7.1"},
            profile="fast",
            limit=2,
        ),
        config,
        tool_runners={"point": point_runner, "payload": payload_runner},
    )

    assert result.items[0].chunk_id == "chunk_point"


def test_runtime_request_from_question_rejects_invalid_filters() -> None:
    with pytest.raises(ValueError, match="filters must be an object"):
        runtime_request_from_question({"filters": "bad"}, default_limit=5, ordinal=1)


def test_run_runtime_batch_question_profile_and_limit_override_batch_defaults() -> None:
    config = IndexingConfig(collection_name="test_collection")
    seen: dict[str, dict[str, list]] = {}

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        entry = seen.setdefault(req.trace_id, {"profiles": [], "limits": []})
        entry["profiles"].append(req.profile)
        entry["limits"].append(req.limit)
        return make_tool_result("payload", ["chunk_1"], req, cfg)

    run_runtime_batch(
        [
            {"id": "q1", "query": "заземление", "profile": "fast", "limit": 2},
            {"id": "q2", "query": "изоляция"},
        ],
        config,
        profile="balanced",
        limit=10,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    )

    assert seen["q1"]["profiles"] == ["fast", "fast"]
    assert seen["q2"]["profiles"] == ["balanced", "balanced"]
    assert seen["q1"]["limits"] == [2, 2]
    assert seen["q2"]["limits"] == [10, 10]


def test_run_runtime_batch_aggregates_question_warnings() -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_1"], req, cfg, warnings=[f"{req.trace_id}-warn"])

    report = run_runtime_batch(
        [{"id": "q1", "query": "заземление"}, {"id": "q2", "query": "изоляция"}],
        config,
        tool_runners={"payload": payload_runner, "vector": payload_runner},
    )

    assert "q1-warn" in report["warnings"]
    assert "q2-warn" in report["warnings"]


def test_render_runtime_single_markdown_includes_profile_tools_and_no_evidence() -> None:
    markdown = render_runtime_markdown(
        {
            "trace": {
                "profile": "balanced",
                "mode": "evidence",
                "fusion": "hybrid_rrf",
                "selected_tools": ["payload", "vector"],
            },
            "items": [],
        }
    )

    assert "Profile: `balanced`" in markdown
    assert "Fusion: `hybrid_rrf`" in markdown
    assert "payload, vector" in markdown
    assert "No evidence items" in markdown


def test_render_runtime_batch_markdown_includes_question_summary() -> None:
    markdown = render_runtime_markdown(
        {
            "schema_version": "mr_runtime_batch_v1",
            "profile": "deep",
            "questions_total": 1,
            "questions": [
                {
                    "id": "grounding",
                    "request": {"query": "заземление"},
                    "result": {
                        "trace": {"selected_tools": ["payload"], "fusion": ""},
                        "items": [{"chunk_id": "chunk_1", "doc_name": "ПУЭ", "point_number": "1.7.1"}],
                    },
                }
            ],
        }
    )

    assert "Profile: `deep`" in markdown
    assert "grounding" in markdown
    assert "заземление" in markdown
    assert "chunk_1" in markdown


def test_resolve_questions_path_defaults_to_project_fixture() -> None:
    paths = ProjectPaths.from_root(Path(__file__).resolve().parents[1])

    resolved = app_main.resolve_questions_path(paths, None)

    assert resolved.name == "retrieval_questions.json"
    assert resolved.parent.name == "fixtures"


def test_rag_runtime_cli_save_report_writes_files(monkeypatch, tmp_path, capsys) -> None:
    config = IndexingConfig(collection_name="test_collection")

    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return make_tool_result("payload", ["chunk_1"], req, cfg)

    monkeypatch.setattr("mr_norm.runtime.tool_runner.run_payload_tool", payload_runner)
    monkeypatch.setattr("mr_norm.runtime.tool_runner.run_vector_tool", payload_runner)
    monkeypatch.setattr("mr_norm.runtime.tool_runner.run_point_tool", payload_runner)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "rag-runtime",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--profile",
            "fast",
            "--save-report",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["report_path"].endswith(".json")
    assert output["markdown_report_path"].endswith(".md")
    assert Path(output["report_path"]).is_file()
    assert "RAG Runtime Report" in Path(output["markdown_report_path"]).read_text(encoding="utf-8")
