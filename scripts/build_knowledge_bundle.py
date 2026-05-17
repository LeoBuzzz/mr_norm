"""Build compact static knowledge bundle for mr_norm query planner from rag_norm sources."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAG_NORM = ROOT.parent / "rag_norm"
OUT_DIR = ROOT / "src" / "mr_norm" / "config" / "knowledge"
OUT_PATH = OUT_DIR / "document_knowledge_index.json"

TOPIC_ALIASES = [
    {
        "phrase": "наведенное напряжение",
        "doc_name_substrings": [
            "правила устройства электроустановок",
            "правила переключений",
            "правила по охране труда",
            "технической эксплуатации",
        ],
        "search_terms": ["наведенное напряжение", "наведенным напряжением"],
    },
    {
        "phrase": "инструкции по ликвидации аварий",
        "doc_name_substrings": [
            "правила предотвращения развития и ликвидации нарушений",
            "ликвидации нарушений нормального режима",
        ],
        "negative_doc_name_substrings": [
            "расследования причин аварий",
            "форма акта",
        ],
        "search_terms": ["ликвидация нарушений", "ликвидации нарушений нормального режима"],
    },
    {
        "phrase": "пуэ",
        "doc_name_substrings": ["правила устройства электроустановок"],
        "search_terms": ["пуэ", "электроустановок"],
    },
    {
        "phrase": "птэ",
        "doc_name_substrings": ["правила технической эксплуатации"],
        "search_terms": ["птэ", "технической эксплуатации"],
    },
]


def _truncate(text: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def build_documents() -> list[dict]:
    annotations_path = RAG_NORM / "handlers" / "document_annotations.json"
    openings_path = RAG_NORM / "handlers" / "document_openings.json"
    if not annotations_path.is_file() or not openings_path.is_file():
        raise FileNotFoundError("rag_norm document_annotations.json or document_openings.json not found")

    openings_payload = _load_json(openings_path)
    doc_name_by_id = {
        str(item.get("doc_id") or ""): str(item.get("doc_name") or "").strip()
        for item in openings_payload.get("documents") or []
        if item.get("doc_id")
    }

    documents: list[dict] = []
    for entry in _load_json(annotations_path).get("annotations") or []:
        doc_id = str(entry.get("doc_id") or "").strip()
        if not doc_id:
            continue
        doc_name = doc_name_by_id.get(doc_id, "")
        annotation = _truncate(str(entry.get("annotation") or ""), 500)
        documents.append(
            {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "annotation": annotation,
            }
        )
    return documents


def build_abbreviations(limit: int = 2500) -> list[dict]:
    abbr_path = RAG_NORM / "abbr.json"
    if not abbr_path.is_file():
        return []
    entries: list[dict] = []
    for item in _load_json(abbr_path):
        if not isinstance(item, dict):
            continue
        abbreviation = str(item.get("abbreviation") or "").strip()
        if not abbreviation or len(abbreviation) > 12:
            continue
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9.\-]+", abbreviation):
            continue
        values = item.get("values") or []
        if not isinstance(values, list) or not values:
            continue
        expansion = _truncate(str(values[0]), 180)
        if len(expansion) < 8:
            continue
        entries.append({"abbreviation": abbreviation, "expansion": expansion})
        if len(entries) >= limit:
            break
    return entries


def build_terms(limit: int = 4000) -> list[dict]:
    snapshot_path = RAG_NORM / "knowledge_graph_snapshot.json"
    if not snapshot_path.is_file():
        return []
    payload = _load_json(snapshot_path)
    terms: list[dict] = []
    for item in payload.get("terms") or []:
        label = _truncate(str(item.get("label") or ""), 120)
        if len(label) < 4 or len(label) > 120:
            continue
        if label[0].isdigit() or "Таблице" in label or "Стандарт [" in label:
            continue
        terms.append({"id": str(item.get("id") or ""), "label": label})
        if len(terms) >= limit:
            break
    return terms


def main() -> int:
    if not RAG_NORM.is_dir():
        print(f"rag_norm not found at {RAG_NORM}", file=sys.stderr)
        return 1

    bundle = {
        "schema_version": "mr_document_knowledge_v1",
        "source": "rag_norm",
        "documents": build_documents(),
        "abbreviations": build_abbreviations(),
        "terms": build_terms(),
        "topic_aliases": TOPIC_ALIASES,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Wrote {OUT_PATH} "
        f"({len(bundle['documents'])} docs, "
        f"{len(bundle['abbreviations'])} abbr, "
        f"{len(bundle['terms'])} terms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
