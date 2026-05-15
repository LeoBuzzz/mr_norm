from __future__ import annotations

import pytest

from mr_norm.tools.chunker import (
    ChunkBuilder,
    MetadataExtractionError,
    PUE_SEVENTH_APPROVING_ACT,
    PUE_SEVENTH_AUTHORITY,
    PUE_SEVENTH_DOC_DATE,
    REQUIRED_RAG_NORM_PAYLOAD_KEYS,
    missing_payload_keys,
)
from mr_norm.tools.rtf_processor import extract_point_number, make_paragraph, marked_text_from_document
from mr_norm.tools.schema import StructuredDocument


def make_structured_document() -> StructuredDocument:
    raw = [
        make_paragraph(0, "ПОСТАНОВЛЕНИЕ"),
        make_paragraph(1, "от 01.01.2024 N 1"),
        make_paragraph(2, "ОБ УТВЕРЖДЕНИИ ТРЕБОВАНИЙ К ЭЛЕКТРИЧЕСКИМ СЕТЯМ"),
        make_paragraph(3, "Раздел I. Общие положения", outline_level=1, style_name="Heading 1"),
        make_paragraph(4, "Глава 1. Основные требования", outline_level=2, style_name="Heading 2"),
        make_paragraph(5, "1. Первый нормативный пункт устанавливает требования к объекту."),
        make_paragraph(6, "2. Второй нормативный пункт устанавливает порядок проверки."),
    ]
    paragraphs = [paragraph for paragraph in raw if paragraph is not None]
    return StructuredDocument(source_file="synthetic.rtf", filename="synthetic.txt", paragraphs=paragraphs)


def make_approved_order_document() -> StructuredDocument:
    raw = [
        make_paragraph(0, "ПРАВИЛА ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ"),
        make_paragraph(
            1,
            "Утверждены Приказом Министерства энергетики Российской Федерации от 12 августа 2022 г. N 811",
        ),
        make_paragraph(2, "Раздел I. Общие положения", outline_level=1, style_name="Heading 1"),
        make_paragraph(3, "1. Настоящие Правила устанавливают требования к эксплуатации."),
    ]
    paragraphs = [paragraph for paragraph in raw if paragraph is not None]
    return StructuredDocument(
        source_file="order.rtf",
        filename="Приказ от 12_08_2022 N 811 Об утверждении Правил технической эксплуатации.txt",
        paragraphs=paragraphs,
    )


def test_marking_preserves_heading_levels_for_payload() -> None:
    document = make_structured_document()

    marked = marked_text_from_document(document)
    chunks = ChunkBuilder(paths=None).build_document_chunks(document)  # type: ignore[arg-type]

    assert "# Раздел I. Общие положения #" in marked
    assert "## Глава 1. Основные требования ##" in marked
    assert len(chunks) >= 2
    for chunk in chunks[-2:]:
        payload = chunk["payload"]
        assert payload["headings"] == ["Раздел I. Общие положения", "Глава 1. Основные требования"]
        assert payload["nearest_heading"] == "Глава 1. Основные требования"
        assert payload["heading_path_text"] == "Раздел I. Общие положения > Глава 1. Основные требования"


def test_payload_is_rag_norm_compatible() -> None:
    chunks = ChunkBuilder(paths=None).build_document_chunks(make_structured_document())  # type: ignore[arg-type]
    point_chunks = [chunk for chunk in chunks if chunk["payload"]["point_number"] in {"1", "2"}]

    assert len(point_chunks) == 2
    for chunk in point_chunks:
        payload = chunk["payload"]
        assert REQUIRED_RAG_NORM_PAYLOAD_KEYS <= set(payload)
        assert not missing_payload_keys(chunk)
        assert payload["doc_name"] == "ОБ УТВЕРЖДЕНИИ ТРЕБОВАНИЙ К ЭЛЕКТРИЧЕСКИМ СЕТЯМ"
        assert payload["doc_reg"] == "Постановление от 01.01.2024 N 1"
        assert payload["doc_kind"] == "постановление"
        assert payload["metadata_confidence"] == "high"
        assert payload["point_identity_key"].startswith(f"{payload['point_number']}::")
        assert payload["point_scope"] == "Раздел I. Общие положения"
        assert "//" not in chunk["text"]
        assert "***" not in chunk["text"]
        assert not chunk["text"].strip().endswith("\\")


def test_document_metadata_payload_quality_fields() -> None:
    chunks = ChunkBuilder(paths=None).build_document_chunks(make_approved_order_document())  # type: ignore[arg-type]
    payload = chunks[0]["payload"]

    assert payload["doc_kind"] == "приказ"
    assert payload["doc_number"] == "811"
    assert payload["doc_date"] == "12 августа 2022"
    assert payload["authority"] == "Министерство энергетики Российской Федерации"
    assert payload["approving_act"].startswith("Приказом Министерства энергетики Российской Федерации")
    assert payload["doc_title_full"] == "ПРАВИЛА ТЕХНИЧЕСКОЙ ЭКСПЛУАТАЦИИ"


def test_federal_law_signature_tail_sets_doc_reg() -> None:
    raw = [
        make_paragraph(0, "ФЕДЕРАЛЬНЫЙ ЗАКОН", outline_level=3, style_name="Heading 3"),
        make_paragraph(1, "О внесении изменений в отдельные законодательные акты", outline_level=3, style_name="Heading 3"),
        make_paragraph(2, "Статья 1", outline_level=1, style_name="Heading 1"),
        make_paragraph(3, "1. Настоящий Федеральный закон устанавливает особенности."),
    ]
    for i in range(4, 12):
        raw.append(make_paragraph(i, f"Текст абзаца {i} с пояснениями."))
    raw.extend(
        [
            make_paragraph(12, "Москва, Кремль"),
            make_paragraph(13, "4 ноября 2007 года"),
            make_paragraph(14, "N 250-ФЗ"),
        ]
    )
    paragraphs = [p for p in raw if p is not None]
    document = StructuredDocument(source_file="law250.rtf", filename="law.txt", paragraphs=paragraphs)
    payload = ChunkBuilder(paths=None).build_document_chunks(document)[0]["payload"]  # type: ignore[arg-type]
    assert "250-ФЗ" in payload["doc_reg"]
    assert payload["doc_reg"].startswith("Федеральный закон от")
    assert payload["doc_kind"] == "федеральный закон"
    assert "Федеральное Собрание" in payload["authority"] or "Президент" in payload["authority"]


def test_gost_approval_line_with_numero_sign_not_latin_n() -> None:
    raw = [
        make_paragraph(0, "ГОСТ Р 71331-2024"),
        make_paragraph(1, "НАЦИОНАЛЬНЫЙ СТАНДАРТ РОССИЙСКОЙ ФЕДЕРАЦИИ", outline_level=1, style_name="Heading 1"),
        make_paragraph(2, "ИНТЕЛЛЕКТУАЛЬНЫЕ СИСТЕМЫ УЧЕТА", outline_level=1, style_name="Heading 1"),
        make_paragraph(
            3,
            "3 УТВЕРЖДЕН И ВВЕДЕН В ДЕЙСТВИЕ Приказом Федерального агентства по техническому регулированию "
            "и метрологии от 9 апреля 2024 г. № 432-ст",
        ),
        make_paragraph(4, "1 Область применения", outline_level=1, style_name="Heading 1"),
        make_paragraph(5, "1.1 Настоящий стандарт устанавливает требования."),
    ]
    document = StructuredDocument(
        source_file="gost71331.rtf",
        filename="ГОСТ Р 71331-2024.txt",
        paragraphs=[p for p in raw if p is not None],
    )
    payload = ChunkBuilder(paths=None).build_document_chunks(document)[0]["payload"]  # type: ignore[arg-type]
    assert "432-ст" in payload["doc_reg"] or "432-ст" in payload["approving_act"]
    assert payload["doc_number"] == "71331-2024"


def test_gost_kind_uses_document_identity_not_approval_order() -> None:
    raw = [
        make_paragraph(0, "ГОСТ 32144-2013"),
        make_paragraph(
            1,
            "Приказом Федерального агентства по техническому регулированию и метрологии от 22 июля 2013 г. N 400-ст",
        ),
        make_paragraph(2, "1. Область применения", outline_level=1, style_name="Heading 1"),
        make_paragraph(3, "1.1 Настоящий стандарт устанавливает требования."),
    ]
    document = StructuredDocument(
        source_file="gost.rtf",
        filename="ГОСТ 32144-2013 Качество электроэнергии.txt",
        paragraphs=[paragraph for paragraph in raw if paragraph is not None],
    )

    payload = ChunkBuilder(paths=None).build_document_chunks(document)[0]["payload"]  # type: ignore[arg-type]

    assert payload["doc_kind"] == "ГОСТ"
    assert payload["doc_number"] == "32144-2013"
    assert payload["authority"] == "Федеральное агентство по техническому регулированию и метрологии (Росстандарт)"
    assert payload["doc_date"] == "22 июля 2013"


def test_metadata_is_not_filled_from_filename_when_content_lacks_it() -> None:
    raw = [
        make_paragraph(0, "Технический текст без реквизитов документа."),
        make_paragraph(1, "1. Нормативный пункт без заголовка и утверждающего акта."),
    ]
    document = StructuredDocument(
        source_file="unknown.rtf",
        filename="Приказ от 01_01_2020 N 1 Об утверждении чего-то.txt",
        paragraphs=[paragraph for paragraph in raw if paragraph is not None],
    )

    with pytest.raises(MetadataExtractionError):
        ChunkBuilder(paths=None).build_document_chunks(document)  # type: ignore[arg-type]


def test_point_number_formats() -> None:
    assert extract_point_number("{3} Требование") == "3"
    assert extract_point_number("3. Требование") == "3"
    assert extract_point_number("1.1 Настоящий стандарт устанавливает требования") == "1.1"
    assert extract_point_number("пункт 3 должен выполняться") == "3"


def test_service_navigation_paragraph_is_skipped() -> None:
    assert make_paragraph(0, "Переход к Содержанию документа осуществляется по ссылке") is None


def test_standard_designation_heading_is_inferred() -> None:
    paragraph = make_paragraph(0, "СО 34.20.185-94 Инструкция по эксплуатации")

    assert paragraph is not None
    assert paragraph.is_heading
    assert paragraph.heading_level == 1


def test_pue_fragment_without_body_act_uses_seventh_edition_canonical() -> None:
    raw = [
        make_paragraph(0, "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", outline_level=1, style_name="Heading 1"),
        make_paragraph(1, "Глава 1.1", outline_level=1, style_name="Heading 1"),
        make_paragraph(2, "1.1.1 Текст нормы."),
    ]
    document = StructuredDocument(
        source_file="pue.rtf",
        filename="ПУЭ глава.txt",
        paragraphs=[p for p in raw if p is not None],
    )
    payload = ChunkBuilder(paths=None).build_document_chunks(document)[0]["payload"]  # type: ignore[arg-type]
    assert payload["doc_kind"] == "приказ"
    assert payload["approving_act"] == PUE_SEVENTH_APPROVING_ACT
    assert payload["doc_date"] == PUE_SEVENTH_DOC_DATE
    assert payload["authority"] == PUE_SEVENTH_AUTHORITY


def test_build_all_writes_metadata_manifest(tmp_path) -> None:
    import json

    from mr_norm.config.paths import ProjectPaths

    root = tmp_path / "proj"
    root.mkdir(parents=True)
    (root / "planning").mkdir()
    (root / "input" / "All_raw_docks").mkdir(parents=True)
    marked = root / "output" / "marked_docs"
    marked.mkdir(parents=True)
    raw = [
        make_paragraph(0, "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", outline_level=1, style_name="Heading 1"),
        make_paragraph(1, "1.1.1 Текст."),
    ]
    doc = StructuredDocument(
        source_file=str(root / "input" / "All_raw_docks" / "pue.rtf"),
        filename="ПУЭ.txt",
        paragraphs=[p for p in raw if p is not None],
    )
    (marked / "pue.structured.json").write_text(json.dumps(doc.to_dict(), ensure_ascii=False), encoding="utf-8")
    paths = ProjectPaths.from_root(root)
    builder = ChunkBuilder(paths)
    chunks = builder.build_all()
    assert len(chunks) >= 1
    assert paths.metadata_manifest_md.is_file()
    md = paths.metadata_manifest_md.read_text(encoding="utf-8")
    assert "канон" in md.lower() or "ПУЭ" in md
    assert len(builder.manifest_pue) >= 1


def test_chunk_ids_are_stable_for_same_structured_document() -> None:
    document = make_structured_document()
    first = ChunkBuilder(paths=None).build_document_chunks(document)  # type: ignore[arg-type]
    second = ChunkBuilder(paths=None).build_document_chunks(document)  # type: ignore[arg-type]

    assert [chunk["chunk_id"] for chunk in first] == [chunk["chunk_id"] for chunk in second]


def test_same_document_metadata_from_different_sources_has_distinct_ids() -> None:
    first = make_structured_document()
    second = make_structured_document()
    second.source_file = "synthetic_copy.rtf"
    second.filename = "synthetic_copy.txt"

    first_chunk = ChunkBuilder(paths=None).build_document_chunks(first)[0]  # type: ignore[arg-type]
    second_chunk = ChunkBuilder(paths=None).build_document_chunks(second)[0]  # type: ignore[arg-type]

    assert first_chunk["payload"]["doc_id"] != second_chunk["payload"]["doc_id"]
    assert first_chunk["chunk_id"] != second_chunk["chunk_id"]
