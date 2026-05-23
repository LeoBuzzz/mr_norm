"""Сборка индекса знаний о документах (начала → LLM-аннотации → document_knowledge_index.json)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.paths import ProjectPaths
from mr_norm.runtime.llm_payloads import parse_llm_payload
from mr_norm.tools.chunker import load_chunks
from mr_norm.tools.document_kind import (
    OPENING_RECORDS_BY_KIND,
    document_kind,
    document_priority,
    opening_record_limit,
)
from mr_norm.tools.rtf_processor import atomic_write_json

KNOWLEDGE_SCHEMA_VERSION = "mr_document_knowledge_v2"
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_ANNOTATION_MODEL", "qwen3:30b").strip() or "qwen3:30b"
DEFAULT_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip() or "http://localhost:11434"
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_ANNOTATION_NUM_PREDICT", "1400"))

TOPIC_ALIASES: list[dict[str, Any]] = [
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


def knowledge_paths(paths: ProjectPaths) -> tuple[Path, Path, Path]:
    knowledge_dir = paths.output_dir / "knowledge"
    openings = knowledge_dir / "document_openings.json"
    annotations = knowledge_dir / "document_annotations.json"
    index_path = Path(__file__).resolve().parents[1] / "config" / "knowledge" / "document_knowledge_index.json"
    return openings, annotations, index_path


def _int_field(payload: dict[str, Any], key: str, default: int = 0) -> int:
    value = payload.get(key, default)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _chunk_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    payload = item.get("payload") or {}
    return (
        _int_field(payload, "chunk_index", 0),
        _int_field(payload, "part_index", 0),
        _int_field(payload, "chunk_start", 0),
    )


def group_chunks_by_doc_id(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        payload = item.get("payload") or {}
        doc_id = str(payload.get("doc_id") or "").strip()
        if not doc_id:
            continue
        grouped.setdefault(doc_id, []).append(item)
    return grouped


def build_opening_record(doc_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_items = sorted(items, key=_chunk_sort_key)
    first_payload = (sorted_items[0].get("payload") or {}) if sorted_items else {}
    doc_name = str(first_payload.get("doc_name") or "").strip()
    registry_key = str(first_payload.get("registry_key") or "").strip()
    kind = document_kind(doc_name) if doc_name else "other"
    limit = opening_record_limit(doc_name) if doc_name else OPENING_RECORDS_BY_KIND["other"]
    chosen = sorted_items[:limit]
    pieces: list[dict[str, Any]] = []
    for item in chosen:
        payload = item.get("payload") or {}
        pieces.append(
            {
                "chunk_index": _int_field(payload, "chunk_index", 0),
                "part_index": _int_field(payload, "part_index", 0),
                "chunk_start": _int_field(payload, "chunk_start", 0),
                "text": (item.get("text") or "").strip(),
            }
        )
    opening_text = "\n\n---\n\n".join(part["text"] for part in pieces if part["text"])
    return {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "registry_key": registry_key,
        "kind": kind,
        "priority": document_priority(doc_name) if doc_name else 5,
        "records_requested": limit,
        "records_returned": len(pieces),
        "opening_text": opening_text,
        "chunks": pieces,
    }


def extract_document_openings(
    chunks_path: Path,
    *,
    output_path: Path,
) -> dict[str, Any]:
    items = load_chunks(chunks_path)
    grouped = group_chunks_by_doc_id(items)
    documents = [
        build_opening_record(doc_id, grouped[doc_id]) for doc_id in sorted(grouped.keys())
    ]
    kind_counts = Counter(doc["kind"] for doc in documents)
    payload = {
        "meta": {
            "schema_version": "mr_document_openings_v1",
            "source_chunks": str(chunks_path.resolve()),
            "documents": len(documents),
            "opening_limits_by_kind": dict(OPENING_RECORDS_BY_KIND),
            "doc_id_note_ru": (
                "doc_id совпадает с payload чанков (resolve_document_id / registry_key). "
                "Группировка только по doc_id, не по filename."
            ),
            "kind_counts": dict(kind_counts),
        },
        "documents": documents,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, payload)
    return payload


def build_annotation_prompt(*, doc_name: str, opening_excerpt: str) -> str:
    return f"""Составь краткую аннотацию нормативного документа для справочника (её прочитает LLM перед ответом пользователю).

Содержание поля annotation: тема, цель и область регулирования — только если это явно следует из фрагмента. Нейтральный стиль, без оценок. Не перечисляй номера статей и пунктов.

Важно: не дублируй в тексте полное название документа, шифр ГОСТ, номер закона/приказа — сразу формулируй суть; идентификаторы уже заданы отдельно.

Объём: 2–4 сжатых предложения. Последнее предложение должно быть полным.

Ответ строго один JSON-объект без текста до или после:
{{"annotation": "<текст аннотации>"}}

Название ниже — только чтобы связать фрагмент с документом; не повторяй его в annotation.

Название: {doc_name}

Фрагмент начала документа:
{opening_excerpt}
""".strip()


def ollama_chat(
    base_url: str,
    model: str,
    user_message: str,
    *,
    timeout_sec: int = 300,
    temperature: float = 0.1,
    num_predict: int = OLLAMA_NUM_PREDICT,
) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                'Отвечай только JSON-объектом с ровно одним ключом "annotation" (строка). '
                "В annotation не повторяй название документа. "
                "Текст annotation обязан заканчиваться завершённым предложением. "
                "Без пояснений до или после JSON."
            ),
        },
        {"role": "user", "content": user_message},
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": temperature, "num_predict": int(num_predict)},
    }
    url = f"{base_url.rstrip('/')}/api/chat"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama недоступна по {url!r}: {exc.reason or exc}") from exc
    message = body.get("message") or {}
    return str(message.get("content") or "").strip()


def annotation_from_model_reply(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        obj = parse_llm_payload(raw)
        text = obj.get("annotation")
        if isinstance(text, str):
            return text.strip()
    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        pass
    return raw


def _load_openings(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "documents" not in data:
        raise ValueError("Ожидается JSON с ключом documents")
    return data


def _slim_annotations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id:
            continue
        item: dict[str, Any] = {
            "doc_id": doc_id,
            "annotation": str(row.get("annotation") or "").strip(),
        }
        if row.get("error"):
            item["error"] = row["error"]
        out.append(item)
    return out


@dataclass
class AnnotationBuildResult:
    annotations_path: Path
    documents_total: int
    new_calls: int
    errors: int
    partial: bool = False


def build_document_annotations(
    openings_path: Path,
    *,
    output_path: Path,
    model: str = DEFAULT_OLLAMA_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_sec: int = 300,
    max_input_chars: int = 14000,
    limit: int = 0,
    resume: bool = False,
    delay_sec: float = 0.0,
    chat_fn: Callable[..., str] | None = None,
) -> AnnotationBuildResult:
    data = _load_openings(openings_path)
    documents = [doc for doc in data.get("documents") or [] if isinstance(doc, dict) and doc.get("doc_id")]
    if limit > 0:
        documents = documents[:limit]

    existing_by_id: dict[str, dict[str, Any]] = {}
    if resume and output_path.is_file():
        prev = json.loads(output_path.read_text(encoding="utf-8"))
        for row in prev.get("annotations") or []:
            if isinstance(row, dict) and row.get("doc_id"):
                existing_by_id[str(row["doc_id"])] = row

    chat = chat_fn or ollama_chat
    working: list[dict[str, Any]] = []
    new_calls = 0
    errors = 0
    order = {str(doc.get("doc_id")): idx for idx, doc in enumerate(documents)}

    for index, doc in enumerate(documents):
        doc_id = str(doc.get("doc_id") or "").strip()
        doc_name = str(doc.get("doc_name") or "").strip()
        if not doc_id or not doc_name:
            continue

        if resume and doc_id in existing_by_id:
            row = dict(existing_by_id[doc_id])
            row.setdefault("doc_name", doc_name)
            working.append(row)
            continue

        opening = str(doc.get("opening_text") or "")
        if max_input_chars > 0 and len(opening) > max_input_chars:
            opening = opening[:max_input_chars] + "\n…"

        prompt = build_annotation_prompt(doc_name=doc_name, opening_excerpt=opening)
        err_msg = ""
        annotation = ""
        try:
            raw = chat(base_url, model, prompt, timeout_sec=timeout_sec)
            annotation = annotation_from_model_reply(raw)
        except Exception as exc:
            err_msg = str(exc)[:2000]
            errors += 1

        row = {"doc_id": doc_id, "doc_name": doc_name, "annotation": annotation}
        if err_msg:
            row["error"] = err_msg
        working.append(row)
        new_calls += 1

        if delay_sec > 0 and index + 1 < len(documents):
            time.sleep(delay_sec)

        if new_calls % 5 == 0:
            _write_annotations_artifact(
                output_path,
                working,
                documents,
                meta={
                    "source_openings": str(openings_path.resolve()),
                    "ollama_model": model,
                    "ollama_base_url": base_url,
                    "partial": True,
                },
            )

    _write_annotations_artifact(
        output_path,
        working,
        documents,
        meta={
            "source_openings": str(openings_path.resolve()),
            "ollama_model": model,
            "ollama_base_url": base_url,
            "documents_processed": new_calls,
            "errors": errors,
        },
    )
    return AnnotationBuildResult(
        annotations_path=output_path,
        documents_total=len(_slim_annotations(working)),
        new_calls=new_calls,
        errors=errors,
    )


def _write_annotations_artifact(
    output_path: Path,
    working: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
) -> None:
    allowed_ids = {str(doc.get("doc_id") or "") for doc in documents}
    rows = [row for row in working if str(row.get("doc_id") or "") in allowed_ids]
    order = {str(doc.get("doc_id")): idx for idx, doc in enumerate(documents)}
    rows.sort(key=lambda row: order.get(str(row.get("doc_id")), 10**9))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output_path,
        {"meta": meta, "annotations": _slim_annotations(rows)},
    )


def _truncate(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def assemble_knowledge_index(
    openings_path: Path,
    annotations_path: Path,
    *,
    output_path: Path,
    include_topic_aliases: bool = True,
    abbreviations: list[dict[str, str]] | None = None,
    terms: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    openings = _load_openings(openings_path)
    meta_by_id = {
        str(doc.get("doc_id") or ""): doc
        for doc in openings.get("documents") or []
        if doc.get("doc_id")
    }
    annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    documents: list[dict[str, Any]] = []
    for entry in annotations_payload.get("annotations") or []:
        doc_id = str(entry.get("doc_id") or "").strip()
        if not doc_id:
            continue
        opening = meta_by_id.get(doc_id) or {}
        doc_name = str(opening.get("doc_name") or "").strip()
        documents.append(
            {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "registry_key": str(opening.get("registry_key") or "").strip(),
                "annotation": _truncate(str(entry.get("annotation") or ""), 500),
            }
        )
    bundle = {
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "source": "mr_norm_corpus",
        "documents": documents,
        "abbreviations": list(abbreviations or []),
        "terms": list(terms or []),
        "topic_aliases": list(TOPIC_ALIASES) if include_topic_aliases else [],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def run_knowledge_build(
    paths: ProjectPaths,
    *,
    skip_openings: bool = False,
    skip_annotations: bool = False,
    skip_index: bool = False,
    resume_annotations: bool = False,
    limit: int = 0,
    model: str = DEFAULT_OLLAMA_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_sec: int = 300,
    max_input_chars: int = 14000,
    delay_sec: float = 0.0,
    chat_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    openings_path, annotations_path, index_path = knowledge_paths(paths)
    report: dict[str, Any] = {
        "command": "knowledge-build",
        "chunks_path": str(paths.chunks_json),
        "openings_path": str(openings_path),
        "annotations_path": str(annotations_path),
        "index_path": str(index_path),
    }

    if not skip_openings:
        if not paths.chunks_json.is_file():
            raise FileNotFoundError(f"Нет файла чанков: {paths.chunks_json}")
        openings_payload = extract_document_openings(paths.chunks_json, output_path=openings_path)
        report["openings_documents"] = len(openings_payload.get("documents") or [])

    if not skip_annotations:
        if not openings_path.is_file():
            raise FileNotFoundError(f"Сначала создайте {openings_path}")
        ann_result = build_document_annotations(
            openings_path,
            output_path=annotations_path,
            model=model,
            base_url=base_url,
            timeout_sec=timeout_sec,
            max_input_chars=max_input_chars,
            limit=limit,
            resume=resume_annotations,
            delay_sec=delay_sec,
            chat_fn=chat_fn,
        )
        report["annotations_documents"] = ann_result.documents_total
        report["annotations_new_calls"] = ann_result.new_calls
        report["annotations_errors"] = ann_result.errors

    if not skip_index:
        if not openings_path.is_file() or not annotations_path.is_file():
            raise FileNotFoundError("Нужны document_openings.json и document_annotations.json")
        bundle = assemble_knowledge_index(openings_path, annotations_path, output_path=index_path)
        report["index_documents"] = len(bundle.get("documents") or [])
        report["index_schema"] = bundle.get("schema_version")

    return report
