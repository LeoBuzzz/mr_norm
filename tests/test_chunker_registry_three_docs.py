"""Сэмпл чанкера: приказ, постановление, фрагмент ПУЭ — JSON в tmp/ и поля payload."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mr_norm.config.paths import ProjectPaths, find_project_root
from mr_norm.tools.chunker import ChunkBuilder, PUE_SEVENTH_AUTHORITY
from mr_norm.tools.rtf_processor import make_paragraph
from mr_norm.tools.schema import StructuredDocument

TMP_OUT = find_project_root() / "tmp" / "chunker_registry_sample.json"

PAYLOAD_FIELDS = [
    "filename",
    "source_file",
    "registry_key",
    "short_title",
    "doc_kind",
    "doc_name",
    "doc_title_full",
    "doc_number",
    "doc_date",
    "authority",
    "approving_act",
    "doc_reg",
    "metadata_source",
    "chapter_number",
    "chapter_title",
    "nearest_heading",
    "point_number",
    "doc_id",
]


def _payload_view(payload: dict) -> dict[str, object]:
    return {k: payload.get(k, "") for k in PAYLOAD_FIELDS}


def _make_order_document() -> StructuredDocument:
    """Приказ: stem из реестра (pr_548)."""
    raw = [
        make_paragraph(0, "ПРАВИЛА ПРЕДОТВРАЩЕНИЯ РАЗВИТИЯ И ЛИКВИДАЦИИ НАРУШЕНИЙ"),
        make_paragraph(
            1,
            "Утверждены Приказом Министерства энергетики Российской Федерации от 12 июля 2018 г. N 548",
        ),
        make_paragraph(2, "Раздел 1. Общие положения", outline_level=1, style_name="Heading 1"),
        make_paragraph(3, "1.1. Настоящие Правила устанавливают порядок действий при авариях."),
    ]
    return StructuredDocument(
        source_file="order_548.rtf",
        filename="Замена! Приказ от 12_07_2018 N 548 Ликвидация аварий.txt",
        paragraphs=[p for p in raw if p is not None],
    )


def _make_decree_document() -> StructuredDocument:
    """Постановление: stem из реестра (pprf_543)."""
    raw = [
        make_paragraph(0, "ПОСТАНОВЛЕНИЕ"),
        make_paragraph(1, "от 10.05.2017 N 543"),
        make_paragraph(2, "О ПОРЯДКЕ ОЦЕНКИ ГОТОВНОСТИ СУБЪЕКТОВ ЭЛЕКТРОЭНЕРГЕТИКИ"),
        make_paragraph(3, "Раздел I. Общие положения", outline_level=1, style_name="Heading 1"),
        make_paragraph(4, "1. Настоящее Постановление устанавливает порядок оценки готовности."),
    ]
    return StructuredDocument(
        source_file="pprf_543.rtf",
        filename="Постановление от 10_05_2017 N 543 О порядке оценки готовности субъектов электроэнергетики к работе в ОЗП.txt",
        paragraphs=[p for p in raw if p is not None],
    )


def _make_pue_fragment_document() -> StructuredDocument:
    """ПУЭ: в имени файла «ПУЭ» — реестр не применяется."""
    raw = [
        make_paragraph(0, "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", outline_level=1, style_name="Heading 1"),
        make_paragraph(1, "Глава 1.1. Общие положения", outline_level=1, style_name="Heading 1"),
        make_paragraph(2, "1.1.1. Заземление электроустановок должно соответствовать требованиям настоящей главы."),
        make_paragraph(3, "1.1.2. Сопротивление заземляющего устройства проверяется по установленному порядку."),
    ]
    return StructuredDocument(
        source_file="pue_chapter.rtf",
        filename="ПУЭ глава 1.1 Общие положения.txt",
        paragraphs=[p for p in raw if p is not None],
    )


def run_chunker_registry_sample(*, write_json: Path = TMP_OUT, print_payloads: bool = True) -> dict:
    paths = ProjectPaths.from_root(find_project_root())
    builder = ChunkBuilder(paths, max_chars=1600, use_registry=True)
    samples = [
        ("order", "приказ (запись реестра pr_548)", _make_order_document()),
        ("decree", "постановление (запись реестра pprf_543)", _make_decree_document()),
        ("pue", "фрагмент ПУЭ (реквизиты акта из реестра pr_204)", _make_pue_fragment_document()),
    ]
    report_docs: list[dict] = []
    all_chunks: list[dict] = []

    for sample_id, description, document in samples:
        chunks = builder.build_document_chunks(document)
        all_chunks.extend(chunks)
        first_payload = chunks[0]["payload"] if chunks else {}
        report_docs.append(
            {
                "sample_id": sample_id,
                "description": description,
                "filename": document.filename,
                "chunks_count": len(chunks),
                "payload_first_chunk": _payload_view(first_payload),
                "payload_all_chunks": [_payload_view(c["payload"]) for c in chunks],
            }
        )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry_path": str(paths.normative_registry_json),
        "documents": report_docs,
        "chunks": all_chunks,
    }
    write_json.parent.mkdir(parents=True, exist_ok=True)
    write_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if print_payloads:
        print(f"\nWrote {write_json}\n")
        for doc in report_docs:
            print("=" * 72)
            print(f"[{doc['sample_id']}] {doc['description']}")
            print(f"  файл: {doc['filename']}")
            print(f"  чанков: {doc['chunks_count']}")
            print("  payload (первый чанк):")
            for key, value in doc["payload_first_chunk"].items():
                val = str(value)
                if len(val) > 200:
                    val = val[:197] + "..."
                print(f"    {key}: {val}")

    return report


def test_chunker_registry_three_doc_types_sample() -> None:
    report = run_chunker_registry_sample(write_json=TMP_OUT, print_payloads=False)

    by_id = {d["sample_id"]: d for d in report["documents"]}
    order_p = by_id["order"]["payload_first_chunk"]
    decree_p = by_id["decree"]["payload_first_chunk"]
    pue_p = by_id["pue"]["payload_first_chunk"]

    assert order_p.get("registry_key") == "pr_548_12072018"
    assert order_p.get("doc_kind") == "приказ"
    assert "registry:" in str(order_p.get("metadata_source", ""))
    assert order_p.get("short_title")

    assert decree_p.get("registry_key") == "pprf_543_10052017"
    assert decree_p.get("doc_kind") == "постановление"
    assert "registry:" in str(decree_p.get("metadata_source", ""))

    assert pue_p.get("registry_key") == "pr_204_08072002"
    assert pue_p.get("short_title") == "ПУЭ"
    assert "registry:" in str(pue_p.get("metadata_source", ""))
    assert pue_p.get("doc_kind") == "приказ"
    assert pue_p.get("doc_title_full") == "Правила устройства электроустановок"
    assert pue_p.get("authority") == PUE_SEVENTH_AUTHORITY
    assert pue_p.get("chapter_number") or pue_p.get("nearest_heading")

    assert TMP_OUT.is_file()
    assert len(report["chunks"]) >= 3


if __name__ == "__main__":
    run_chunker_registry_sample()
