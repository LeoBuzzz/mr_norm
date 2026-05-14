from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetadataManifestEntry:
    """One row for the human-review manifest (not read back by the pipeline)."""

    filename: str
    source_file: str
    category: str  # "pue_canonical" | "title_fallback"
    detail: str
    doc_title_full: str
    doc_reg: str
    approving_act: str
    doc_date: str
    authority: str


def write_metadata_manifest(path: Path, pue_rows: list[MetadataManifestEntry], other_rows: list[MetadataManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# Манифест метаданных (черновик для проверки)",
        "",
        "Скрипт **не читает** этот файл обратно. Переименование и правки — **вне** пайплайна `mr_norm`.",
        "",
        "## ПУЭ — применён канон 7-го издания",
        "",
        "Правила устройства электроустановок (7-е изд.) утверждены приказом Министерства энергетики РФ от **8 июля 2002 г. № 204**; "
        "дата введения в действие — **1 января 2003 г.** Ниже — фрагменты, где реквизит в тексте не найден и подставлен канон.",
        "",
        _table(pue_rows),
        "",
        "## Прочие документы — заголовок / слабые эвристики",
        "",
        "Ниже файлы, где часть полей взята из заголовка (`О …`, строка в `[…]`) или низкая уверенность; чанки в `qdrant_chunks.json` уже собраны — проверьте вручную.",
        "",
        _table(other_rows),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _table(rows: list[MetadataManifestEntry]) -> str:
    if not rows:
        return "_нет записей_"
    out = [
        "| filename | category | detail | doc_title_full | doc_reg | doc_date | authority |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            "| "
            + " | ".join(
                [
                    _md_cell(r.filename),
                    _md_cell(r.category),
                    _md_cell(r.detail),
                    _md_cell(r.doc_title_full),
                    _md_cell(r.doc_reg),
                    _md_cell(r.doc_date),
                    _md_cell(r.authority),
                ]
            )
            + " |"
        )
    return "\n".join(out)


def _md_cell(s: str, max_len: int = 120) -> str:
    t = (s or "").replace("\n", " ").replace("|", "/")
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t
