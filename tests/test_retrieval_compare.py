from __future__ import annotations

import json

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.compare import (
    compute_pipeline_match_metrics,
    reciprocal_rank_fusion,
    run_retrieval_compare,
    run_retrieval_compare_batch,
    save_retrieval_compare_batch_report,
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


def test_compute_pipeline_match_metrics_reports_top_matches() -> None:
    items = [
        {"chunk_id": "chunk_a", "doc_name": "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", "point_number": "1.7.1"},
        {"chunk_id": "chunk_b", "doc_name": "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", "point_number": "1.9.28"},
    ]
    expected = {
        "doc_name": "Правила устройства электроустановок",
        "point_number": "1.7.1",
        "chunk_id": "chunk_a",
    }

    metrics = compute_pipeline_match_metrics(items, expected)

    assert metrics["has_expected"] is True
    assert metrics["top1_doc_match"] is True
    assert metrics["top5_doc_match"] is True
    assert metrics["top1_point_match"] is True
    assert metrics["top5_chunk_match"] is True


def test_retrieval_compare_batch_runs_question_set_with_expected_placeholders() -> None:
    config = IndexingConfig(collection_name="test_collection")
    questions = [
        {
            "id": "grounding",
            "query": "заземление",
            "filters": {"doc_name": "ПУЭ"},
            "expected": {"doc_name": "ПУЭ", "point_number": "1.7.1"},
        },
        {"id": "insulation", "query": "изоляция", "filters": {}, "limit": 2},
    ]
    def payload_runner(req: ToolRequest, cfg: IndexingConfig) -> ToolResult:
        return ToolResult(
            items=[
                RetrievedItem(
                    chunk_id=f"{req.trace_id}_chunk",
                    doc_name="ПУЭ",
                    point_number="1.7.1",
                    source_tool="payload",
                )
            ],
            trace=ToolTrace(
                tool_name="payload",
                collection_name=cfg.collection_name,
                vector_name=cfg.vector_name,
                query=req.query,
                normalized_filters=req.filters,
                limit=req.limit,
                profile=req.profile,
            ),
            metrics=ToolMetrics(elapsed_sec=0.001, candidates_returned=1, qdrant_calls=1),
            warnings=[],
        )

    runners = {"payload": payload_runner}

    report = run_retrieval_compare_batch(questions, config, pipelines="payload,hybrid", limit=4, tool_runners=runners)

    assert report["schema_version"] == "mr_retrieval_compare_batch_v1"
    assert report["questions_total"] == 2
    assert report["questions"][0]["id"] == "grounding"
    assert report["questions"][0]["expected"]["point_number"] == "1.7.1"
    assert report["questions"][0]["comparison"]["results"]["payload"]["items"][0]["chunk_id"] == "grounding_chunk"
    assert report["questions"][0]["eval"]["expected_fields"]["point_number"] == "1.7.1"
    assert report["questions"][0]["eval"]["pipelines"]["payload"]["top1_point_match"] is True
    assert report["eval_summary"]["questions_with_expected"] == 1
    assert report["questions"][1]["comparison"]["request"]["limit"] == 2


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


def test_save_retrieval_compare_batch_report_writes_json_and_markdown(tmp_path) -> None:
    report = run_retrieval_compare_batch(
        [{"id": "grounding", "query": "заземление"}],
        IndexingConfig(collection_name="test_collection"),
        pipelines=[],
        tool_runners={},
    )

    saved = save_retrieval_compare_batch_report(report, tmp_path)

    assert saved["report_path"].endswith(".json")
    assert saved["markdown_report_path"].endswith(".md")
    assert json.loads((tmp_path / saved["report_path"].split("\\")[-1]).read_text(encoding="utf-8"))[
        "schema_version"
    ] == "mr_retrieval_compare_batch_v1"


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


def test_retrieval_compare_batch_cli_loads_questions_and_can_save_report(monkeypatch, tmp_path, capsys) -> None:
    questions_path = tmp_path / "retrieval_questions.json"
    questions_path.write_text(
        json.dumps([{"id": "grounding", "query": "заземление", "filters": {"doc_name": "ПУЭ"}}], ensure_ascii=False),
        encoding="utf-8",
    )
    seen = {}

    def fake_run_retrieval_compare_batch(
        questions: list[dict],
        config: IndexingConfig,
        *,
        pipelines: str,
        limit: int,
    ) -> dict:
        seen["questions"] = questions
        seen["config"] = config
        seen["pipelines"] = pipelines
        seen["limit"] = limit
        return {
            "schema_version": "mr_retrieval_compare_batch_v1",
            "collection_name": config.collection_name,
            "vector_name": config.vector_name,
            "pipelines": ["payload"],
            "questions_total": len(questions),
            "questions": [],
            "metrics": {"elapsed_sec": 0.001, "questions_total": len(questions)},
            "warnings": [],
        }

    monkeypatch.setattr(app_main, "run_retrieval_compare_batch", fake_run_retrieval_compare_batch)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "retrieval-compare-batch",
            "--collection-name",
            "test_collection",
            "--questions",
            str(questions_path),
            "--pipelines",
            "payload",
            "--limit",
            "3",
            "--save-report",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["questions"][0]["query"] == "заземление"
    assert seen["config"].collection_name == "test_collection"
    assert seen["pipelines"] == "payload"
    assert seen["limit"] == 3
    assert output["report_path"].endswith(".json")
    assert output["markdown_report_path"].endswith(".md")
