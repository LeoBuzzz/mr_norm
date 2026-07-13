from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.point_assemble import (
    assemble_point_parts,
    filter_items_by_exact_point,
    normalize_point_number,
    point_number_filter_variants,
    points_exact_match,
    select_point_identity_group,
)


def test_normalize_point_number_unifies_dot_and_underscore() -> None:
    assert normalize_point_number("4.14_1") == "4.14.1"
    assert normalize_point_number(" 4 . 14 ") == "4.14"


def test_points_exact_match_rejects_prefix_false_positive() -> None:
    assert points_exact_match("18", "18.1") is False
    assert points_exact_match("18.1", "18.1") is True
    assert points_exact_match("4.14_1", "4.14.1") is True


def test_filter_items_by_exact_point() -> None:
    items = [
        RetrievedItem(chunk_id="a", point_number="18.1", text="sub"),
        RetrievedItem(chunk_id="b", point_number="18", text="parent"),
    ]
    filtered = filter_items_by_exact_point(items, "18")
    assert [item.chunk_id for item in filtered] == ["b"]


def test_point_number_filter_variants() -> None:
    variants = point_number_filter_variants("4.14_1")
    assert "4.14_1" in variants
    assert "4.14.1" in variants


def test_assemble_point_parts_single_chunk() -> None:
    items = [
        RetrievedItem(
            chunk_id="chunk_1",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="24",
            text="24. Текст пункта.",
            part_index=0,
            total_parts=1,
            is_complete_point=True,
        )
    ]
    assembled = assemble_point_parts(items, point_number="24")
    assert assembled is not None
    assert assembled.text == "24. Текст пункта."
    assert assembled.is_complete is True
    assert len(assembled.parts) == 1


def test_assemble_point_parts_joins_split_chunks_in_order() -> None:
    items = [
        RetrievedItem(
            chunk_id="chunk_2",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="98",
            text="…продолжение.",
            part_index=1,
            total_parts=2,
            is_split=True,
            is_complete_point=False,
        ),
        RetrievedItem(
            chunk_id="chunk_1",
            doc_id="doc_1",
            doc_name="Правила",
            point_number="98",
            text="98. Начало пункта ",
            part_index=0,
            total_parts=2,
            is_split=True,
            is_complete_point=False,
        ),
    ]
    assembled = assemble_point_parts(items, point_number="98")
    assert assembled is not None
    assert assembled.text == "98. Начало пункта …продолжение."
    assert assembled.is_complete is True
    assert [part.chunk_id for part in assembled.parts] == ["chunk_1", "chunk_2"]


def test_select_point_identity_group_prefers_earliest_document_section() -> None:
    items = [
        RetrievedItem(
            chunk_id="appendix",
            point_number="3",
            text="3. Энергосистема Карелии.",
            point_identity_key="3::III. Объединенная энергосистема::891:246",
        ),
        RetrievedItem(
            chunk_id="main",
            point_number="3",
            text="3. Основной пункт документа.",
            point_identity_key="3::Об утверждении Правил::61:3",
        ),
    ]
    selected, collapsed = select_point_identity_group(items)
    assert collapsed is True
    assert [item.chunk_id for item in selected] == ["main"]
    assembled = assemble_point_parts(items, point_number="3")
    assert assembled is not None
    assert assembled.text == "3. Основной пункт документа."
