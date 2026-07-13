from __future__ import annotations

from unittest.mock import patch

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolMetrics, ToolRequest, ToolResult, ToolTrace
from mr_norm.skills.document_resolve import DocumentResolveResult
from mr_norm.skills.point_lookup import PointLookupRequest, lookup_point


def _tool_result(items: list[RetrievedItem]) -> ToolResult:
    return ToolResult(
        items=items,
        trace=ToolTrace(tool_name="point"),
        metrics=ToolMetrics(elapsed_sec=0.0, candidates_returned=len(items)),
    )


def test_lookup_point_returns_verbatim_text_for_single_chunk() -> None:
    items = [
        RetrievedItem(
            chunk_id="chunk_24",
            doc_id="doc_796",
            doc_name="Об утверждении Правил работы с персоналом",
            point_number="24",
            text="24. Инструктаж проводится в установленном порядке.",
            source_tool="point",
        )
    ]

    def fake_run_point_tool(request: ToolRequest, config: IndexingConfig, client=None) -> ToolResult:
        assert request.filters["doc_id"] == "doc_796"
        assert request.filters["point_number"] == "24"
        return _tool_result(items)

    with patch("mr_norm.skills.point_lookup.run_point_tool", fake_run_point_tool):
        result = lookup_point(
            PointLookupRequest(
                query="пункт 24 правил работы с персоналом",
                doc_id="doc_796",
                point_number="24",
            ),
            config=IndexingConfig(collection_name="test"),
        )

    assert result.found is True
    assert result.status == "exact"
    assert "24. Инструктаж проводится" in result.text
    assert "п. 24 —" in result.answer
    assert "chunk_" not in result.answer
    assert result.citations[0].chunk_id == "chunk_24"


def test_lookup_point_assembles_split_parts() -> None:
    items = [
        RetrievedItem(
            chunk_id="chunk_b",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="98",
            text="конец.",
            part_index=1,
            total_parts=2,
            is_split=True,
        ),
        RetrievedItem(
            chunk_id="chunk_a",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="98",
            text="98. Начало ",
            part_index=0,
            total_parts=2,
            is_split=True,
        ),
    ]

    with patch(
        "mr_norm.skills.point_lookup.run_point_tool",
        lambda *args, **kwargs: _tool_result(items),
    ):
        result = lookup_point(
            PointLookupRequest(doc_id="doc_1", point_number="98"),
            config=IndexingConfig(collection_name="test"),
        )

    assert result.found is True
    assert result.text == "98. Начало конец."
    assert len(result.parts) == 2


def test_lookup_point_rejects_prefix_match_from_tool() -> None:
    items = [
        RetrievedItem(
            chunk_id="wrong",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="18.1",
            text="Подпункт.",
            source_tool="point",
        )
    ]

    with patch(
        "mr_norm.skills.point_lookup.run_point_tool",
        lambda *args, **kwargs: _tool_result(items),
    ):
        result = lookup_point(
            PointLookupRequest(doc_id="doc_1", point_number="18"),
            config=IndexingConfig(collection_name="test"),
        )

    assert result.found is False
    assert result.status == "not_found"


def test_lookup_point_not_found_without_doc() -> None:
    with patch(
        "mr_norm.skills.point_lookup.resolve_document",
        return_value=DocumentResolveResult(
            found=False,
            status="not_found",
            warnings=("document_resolve:not_found",),
        ),
    ):
        with patch(
            "mr_norm.skills.point_lookup.resolve_document_from_label",
            return_value=DocumentResolveResult(
                found=False,
                status="not_found",
            ),
        ):
            result = lookup_point(
                PointLookupRequest(query="пункт 24"),
                config=IndexingConfig(collection_name="test"),
            )

    assert result.found is False
    assert "point_lookup:document_not_found" in result.warnings


def test_lookup_point_resolves_trailing_label_and_multiple_points() -> None:
    items_by_point = {
        "3": [
            RetrievedItem(
                chunk_id="chunk_3",
                doc_id="doc_ptf",
                doc_name="Правила технологического функционирования",
                point_number="3",
                text="3. Первый пункт.",
                source_tool="point",
            )
        ],
        "99": [
            RetrievedItem(
                chunk_id="chunk_99",
                doc_id="doc_ptf",
                doc_name="Правила технологического функционирования",
                point_number="99",
                text="99. Последний пункт.",
                source_tool="point",
            )
        ],
    }

    def fake_run_point_tool(request: ToolRequest, config: IndexingConfig, client=None) -> ToolResult:
        point_number = str(request.filters.get("point_number") or "")
        return _tool_result(items_by_point[point_number])

    with patch("mr_norm.skills.point_lookup.run_point_tool", fake_run_point_tool):
        with patch(
            "mr_norm.skills.point_lookup.resolve_document",
            return_value=DocumentResolveResult(found=False, status="not_found"),
        ):
            with patch(
                "mr_norm.skills.point_lookup.resolve_document_from_label",
                return_value=DocumentResolveResult(
                    found=True,
                    doc_id="doc_ptf",
                    doc_name="Правила технологического функционирования",
                    status="exact",
                ),
            ):
                result = lookup_point(
                    PointLookupRequest(
                        query="Дай текст пунктов 3 и 99 ПТФ",
                        point_numbers=("3", "99"),
                        doc_id="doc_ptf",
                    ),
                    config=IndexingConfig(collection_name="test"),
                )

    assert result.found is True
    assert result.point_number == "3, 99"
    assert "3. Первый пункт." in result.answer
    assert "99. Последний пункт." in result.answer
