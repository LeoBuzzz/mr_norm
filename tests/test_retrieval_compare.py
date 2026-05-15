from __future__ import annotations

import json

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.compare import (
    reciprocal_rank_fusion,
    run_retrieval_compare,
    save_retrieval_compare_report,
)
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace


def make_result(tool_name: str, chunk_ids: list[str], request: ToolRequest, config: IndexingConfig) -> ToolResult:
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
        ),
        metrics=ToolMetrics(elapsed_sec=0.001, candidates_returned=len(chunk_ids), qdrant_calls=1),
        warnings=[],
    )


def test_retrieval_compare_runs_selected_pipelines_and_hybrid() -> None:
    config = IndexingConfig(collection_name="test_collection")
    request = ToolRequest(query="заземление", filters={"doc_name": "ПУЭ"}, limit=3)
    runners = {
        "point": lambda req, cfg: make_result("point", ["chunk_a", "chunk_b"], req, cfg),
        "payload": lambda req, cfg: make_result("payload", ["chunk_b", "chunk_c"], req, cfg),
    }

    report = run_retrieval_compare(request, config, pipelines="point,payload,hybrid", tool_runners=runners)

    assert report["schema_version"] == "mr_retrieval_compare_v1"
    assert report["request"]["query"] == "заземление"
    assert report["results"]["point"]["items"][0]["chunk_id"] == "chunk_a"
    assert report["results"]["payload"]["items"][0]["chunk_id"] == "chunk_b"
    assert report["results"]["hybrid"]["items"][0]["chunk_id"] == "chunk_b"
    assert report["results"]["hybrid"]["items"][0]["source_tool"] == "hybrid_rrf"


def test_reciprocal_rank_fusion_orders_shared_chunks_first() -> None:
    config = IndexingConfig(collection_name="test_collection")
    request = ToolRequest(limit=5)
    results = {
        "point": make_result("point", ["chunk_a", "chunk_b"], request, config),
        "payload": make_result("payload", ["chunk_b", "chunk_c"], request, config),
    }

    fused = reciprocal_rank_fusion(results, limit=3)

    assert [item.chunk_id for item in fused] == ["chunk_b", "chunk_a", "chunk_c"]
    assert fused[0].matched["source_ranks"] == {"point": 2, "payload": 1}


def test_retrieval_compare_preserves_pipeline_errors() -> None:
    config = IndexingConfig(collection_name="test_collection")
    request = ToolRequest(query="заземление")

    def failing_runner(_request: ToolRequest, _config: IndexingConfig) -> ToolResult:
        raise RuntimeError("model unavailable")

    report = run_retrieval_compare(
        request,
        config,
        pipelines=["vector"],
        tool_runners={"vector": failing_runner},
    )

    assert report["results"]["vector"]["trace"]["empty_reason"] == "pipeline_error"
    assert "vector failed: RuntimeError: model unavailable" in report["warnings"]


def test_save_retrieval_compare_report_writes_json_and_markdown(tmp_path) -> None:
    report = run_retrieval_compare(
        ToolRequest(query="заземление"),
        IndexingConfig(collection_name="test_collection"),
        pipelines=[],
        tool_runners={},
    )

    saved = save_retrieval_compare_report(report, tmp_path)

    assert saved["report_path"].endswith(".json")
    assert saved["markdown_report_path"].endswith(".md")
    assert json.loads((tmp_path / saved["report_path"].split("\\")[-1]).read_text(encoding="utf-8"))[
        "schema_version"
    ] == "mr_retrieval_compare_v1"


def test_retrieval_compare_cli_routes_and_can_save_report(monkeypatch, tmp_path, capsys) -> None:
    seen = {}

    def fake_run_retrieval_compare(request: ToolRequest, config: IndexingConfig, *, pipelines: str) -> dict:
        seen["request"] = request
        seen["config"] = config
        seen["pipelines"] = pipelines
        return {
            "schema_version": "mr_retrieval_compare_v1",
            "request": {"query": request.query, "filters": request.filters},
            "collection_name": config.collection_name,
            "vector_name": config.vector_name,
            "pipelines": ["point"],
            "results": {"point": make_result("point", ["chunk_1"], request, config).to_dict()},
            "metrics": {"elapsed_sec": 0.001, "pipelines_total": 1, "pipelines_succeeded": 1},
            "warnings": [],
        }

    monkeypatch.setattr(app_main, "run_retrieval_compare", fake_run_retrieval_compare)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "retrieval-compare",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--doc-name",
            "ПУЭ",
            "--pipelines",
            "point",
            "--save-report",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["request"].filters == {"doc_name": "ПУЭ"}
    assert seen["config"].collection_name == "test_collection"
    assert seen["pipelines"] == "point"
    assert output["report_path"].endswith(".json")
    assert output["markdown_report_path"].endswith(".md")
