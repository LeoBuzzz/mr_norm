from __future__ import annotations

import json

from mr_norm.apps import main as app_main
from mr_norm.config.indexing import IndexingConfig
from mr_norm.indexing.qdrant_adapter import EXPECTED_PAYLOAD_INDEXES
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.retrieval.filters import doc_name_variants
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import run_point_tool, select_point_filters
from mr_norm.retrieval.tools.vector import run_vector_tool


class FakePayloadClient:
    def __init__(self) -> None:
        self.calls = 0
        self.filter_spec = {}
        self.limit = 0
        self.source_tool = ""

    def payload_search(self, filter_spec: dict, *, limit: int, source_tool: str) -> list[RetrievedItem]:
        self.calls += 1
        self.filter_spec = filter_spec
        self.limit = limit
        self.source_tool = source_tool
        return [
            RetrievedItem(
                chunk_id="chunk_1",
                doc_id="doc_1",
                doc_name="Правила устройства электроустановок",
                heading_path_text="Раздел 1 > Глава 1.9",
                point_number="1.9.28",
                text="1.9.28. Проверяемый текст.",
                source_tool=source_tool,
            )
        ]


class FakeVectorClient:
    def __init__(self) -> None:
        self.calls = 0
        self.vector: list[float] = []
        self.filter_spec = {}
        self.limit = 0

    def vector_search(
        self,
        vector: list[float],
        filter_spec: dict,
        *,
        limit: int,
        source_tool: str,
    ) -> list[RetrievedItem]:
        self.calls += 1
        self.vector = vector
        self.filter_spec = filter_spec
        self.limit = limit
        return [RetrievedItem(chunk_id="chunk_vector", score=0.91, source_tool=source_tool)]


class FakeEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        return [[0.1, 0.2, 0.3]]


def test_payload_tool_contract_builds_filters_results_and_trace() -> None:
    client = FakePayloadClient()
    request = ToolRequest(
        query="заземление",
        filters={"doc_name": " ПУЭ ", "point_number": "1.9.28"},
        limit=5,
        trace_id="trace-1",
    )

    result = run_payload_tool(request, IndexingConfig(collection_name="test_collection"), client=client)
    payload = result.to_dict()

    assert payload["items"][0]["chunk_id"] == "chunk_1"
    assert payload["trace"]["tool_name"] == "payload"
    assert payload["trace"]["trace_id"] == "trace-1"
    assert payload["trace"]["normalized_filters"] == {"doc_name": "ПУЭ", "point_number": "1.9.28"}
    assert {"field": "doc_name", "kind": "keyword", "value": "ПУЭ"} in payload["trace"]["qdrant_filter"]["must"]
    assert {"field": "text", "kind": "text", "value": "заземление"} in payload["trace"]["qdrant_filter"]["should"]
    assert payload["metrics"]["candidates_returned"] == 1


def test_payload_tool_expands_doc_name_variants_for_exact_filters() -> None:
    client = FakePayloadClient()
    request = ToolRequest(
        query="изоляция в районах загрязнения",
        filters={"doc_name": "Правила устройства электроустановок", "point_number": "1.9.28"},
        limit=3,
    )

    result = run_payload_tool(request, IndexingConfig(collection_name="test_collection"), client=client)
    payload = result.to_dict()

    assert payload["trace"]["normalized_filters"] == {
        "doc_name": ["Правила устройства электроустановок", "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
        "point_number": "1.9.28",
    }
    assert {
        "field": "doc_name",
        "kind": "keyword",
        "any": ["Правила устройства электроустановок", "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
    } in payload["trace"]["qdrant_filter"]["must"]


def test_point_tool_prefers_stable_identity_filters() -> None:
    assert select_point_filters({"chunk_id": "chunk_1", "point_identity_key": "point_key"}) == (
        {"chunk_id": "chunk_1"},
        [],
    )
    assert select_point_filters({"point_identity_key": "point_key", "doc_name": "ПУЭ"}) == (
        {"point_identity_key": "point_key"},
        [],
    )


def test_point_tool_contract_uses_doc_name_and_point_number() -> None:
    client = FakePayloadClient()
    request = ToolRequest(filters={"doc_name": "Правила устройства электроустановок", "point_number": "1.9.28"}, limit=3)

    result = run_point_tool(request, IndexingConfig(collection_name="test_collection"), client=client)
    payload = result.to_dict()

    assert client.source_tool == "point"
    assert client.limit == 3
    assert payload["trace"]["tool_name"] == "point"
    assert payload["trace"]["normalized_filters"] == {
        "doc_name": ["Правила устройства электроустановок", "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
        "point_number": "1.9.28",
    }
    assert {
        "field": "doc_name",
        "kind": "keyword",
        "any": ["Правила устройства электроустановок", "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК"],
    } in payload["trace"]["qdrant_filter"]["must"]
    assert payload["items"][0]["source_tool"] == "point"


def test_doc_name_variants_are_deterministic_and_deduplicated() -> None:
    assert doc_name_variants("Правила устройства электроустановок") == [
        "Правила устройства электроустановок",
        "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК",
    ]
    assert doc_name_variants("ПУЭ") == "ПУЭ"


def test_point_and_payload_tools_do_not_scan_without_inputs() -> None:
    point_client = FakePayloadClient()
    point_result = run_point_tool(ToolRequest(), IndexingConfig(collection_name="test_collection"), client=point_client)
    payload_client = FakePayloadClient()
    payload_result = run_payload_tool(ToolRequest(), IndexingConfig(collection_name="test_collection"), client=payload_client)

    assert point_client.calls == 0
    assert payload_client.calls == 0
    assert point_result.trace.empty_reason == "empty_query_and_filters"
    assert payload_result.trace.empty_reason == "empty_query_and_filters"


def test_vector_tool_contract_uses_embedder_vector_and_payload_filters() -> None:
    client = FakeVectorClient()
    embedder = FakeEmbedder()
    request = ToolRequest(query="требования к заземлению", filters={"doc_name": "ПУЭ"}, limit=7)

    result = run_vector_tool(
        request,
        IndexingConfig(collection_name="test_collection", vector_name="bge-m3"),
        client=client,
        embedder=embedder,
    )
    payload = result.to_dict()

    assert embedder.texts == ["требования к заземлению"]
    assert client.vector == [0.1, 0.2, 0.3]
    assert client.filter_spec == {"must": [{"field": "doc_name", "kind": "keyword", "value": "ПУЭ"}]}
    assert payload["trace"]["tool_name"] == "vector"
    assert payload["trace"]["vector_name"] == "bge-m3"
    assert payload["items"][0]["score"] == 0.91


def test_stage_four_expected_payload_indexes_include_point_and_heading_fields() -> None:
    assert EXPECTED_PAYLOAD_INDEXES["point_number"] == "keyword"
    assert EXPECTED_PAYLOAD_INDEXES["heading_path_text"] == "text"


def fake_tool_result(tool_name: str, request: ToolRequest, config: IndexingConfig) -> ToolResult:
    return ToolResult(
        items=[RetrievedItem(chunk_id=f"{tool_name}_chunk", source_tool=tool_name)],
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
        metrics=ToolMetrics(elapsed_sec=0.001, candidates_returned=1, qdrant_calls=1),
        warnings=[],
    )


def test_retrieval_payload_cli_routes_to_tool(monkeypatch, tmp_path, capsys) -> None:
    seen = {}

    def fake_run_payload_tool(request: ToolRequest, config: IndexingConfig) -> ToolResult:
        seen["request"] = request
        seen["config"] = config
        return fake_tool_result("payload", request, config)

    monkeypatch.setattr(app_main, "run_payload_tool", fake_run_payload_tool)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "retrieval-payload",
            "--collection-name",
            "test_collection",
            "--query",
            "заземление",
            "--doc-name",
            "ПУЭ",
            "--point-number",
            "1.9.28",
            "--limit",
            "4",
            "--trace-id",
            "trace-cli",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["config"].collection_name == "test_collection"
    assert seen["request"].filters == {"doc_name": "ПУЭ", "point_number": "1.9.28"}
    assert output["trace"]["tool_name"] == "payload"
    assert output["trace"]["trace_id"] == "trace-cli"


def test_retrieval_point_cli_routes_to_tool(monkeypatch, tmp_path, capsys) -> None:
    seen = {}

    def fake_run_point_tool(request: ToolRequest, config: IndexingConfig) -> ToolResult:
        seen["request"] = request
        seen["config"] = config
        return fake_tool_result("point", request, config)

    monkeypatch.setattr(app_main, "run_point_tool", fake_run_point_tool)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "retrieval-point",
            "--collection-name",
            "test_collection",
            "--chunk-id",
            "chunk_1",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["request"].filters == {"chunk_id": "chunk_1"}
    assert seen["config"].collection_name == "test_collection"
    assert output["items"][0]["source_tool"] == "point"


def test_retrieval_vector_cli_requires_query_and_routes_to_tool(monkeypatch, tmp_path, capsys) -> None:
    seen = {}

    def fake_run_vector_tool(request: ToolRequest, config: IndexingConfig) -> ToolResult:
        seen["request"] = request
        seen["config"] = config
        return fake_tool_result("vector", request, config)

    monkeypatch.setattr(app_main, "run_vector_tool", fake_run_vector_tool)

    exit_code = app_main.main(
        [
            "--root",
            str(tmp_path),
            "retrieval-vector",
            "--collection-name",
            "test_collection",
            "--query",
            "требования к заземлению",
            "--doc-name",
            "ПУЭ",
            "--limit",
            "2",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert seen["request"].query == "требования к заземлению"
    assert seen["request"].filters == {"doc_name": "ПУЭ"}
    assert output["trace"]["tool_name"] == "vector"
