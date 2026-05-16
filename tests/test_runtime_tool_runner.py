from __future__ import annotations

import json

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.tool_runner import run_runtime, run_runtime_batch, save_runtime_report


def make_tool_result(tool_name: str, chunk_ids: list[str], request: ToolRequest, config: IndexingConfig) -> ToolResult:
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
        warnings=[],
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
