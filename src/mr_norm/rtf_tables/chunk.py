"""Turn a parsed RTF into point chunks and one chunk per logical table row.

The vector text is the caption, the column header and the cell values, with the
left-hand labels repeated on every row they cover. Search tools read this text.
The payload keeps the same row split into inherited context and the cells that
start on this row, plus document links found in the cells.

A point stays one chunk while it fits in 4000 tokens. A line-break hyphen is
joined back into the word. A number and its unit stay on the same side of a cut.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from mr_norm.rtf_tables.parse import (
    PhysicalTable,
    column_headers,
    header_row_count,
    logical_grid,
    parse_flow,
    plain_text,
)

_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "normative_documents_registry.json"
SCHEMA_VERSION = "mr_chunks_v2"
_SPACE = re.compile(r"\s+")
_TABLE_LABEL = re.compile(r"таблиц\w*\s+(?:№\s*|N\s*)?(\d+(?:[._]\d+)*)", re.IGNORECASE)
_LEADING_LABEL = re.compile(r"^\s*(\d+(?:[._]\d+)*)")
_TABLE_MENTION = re.compile(r"таблиц[а-яё]*\s+(?:№\s*|N\s*)?(\d+(?:[._]\d+)*)", re.IGNORECASE)
_ORDER = re.compile(
    r"приказ\w*\s+(?:Минэнерго\s+России\s+)?от\s+(\d{2}\.\d{2}\.\d{4})\s+№\s*(\d+)",
    re.IGNORECASE,
)
_POINT = re.compile(r"^(\d+(?:\.\d+)*)(?:\.\s+|\s+)")
_PUE_CHAPTER = re.compile(r"\bглава\s+(\d+)\.(\d+)\b", re.IGNORECASE)
_DASHES = set("-–—")
# A line-break hyphen sits tight on the left fragment and has whitespace before
# the lowercase continuation: «Предостав- ляют», «бал- льной».
_HYPHEN_BREAK = re.compile(r"(?<![А-ЯЁа-яё])([А-ЯЁа-яё]{2,})-[ \t]+([а-яё][А-ЯЁа-яё]*)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«(])")
# cl100k is about 2.2 characters per token on this Russian corpus, close to the
# 2-characters-per-token calibration. o200k counts the same text almost twice
# as cheaply and would let a point grow past the embedder window.
MAX_POINT_TOKENS = 4000
_CONTENT_POS = frozenset({"NOUN", "ADJF", "ADJS", "ADVB", "VERB", "INFN", "PRTF", "PRTS", "GRND", "NUMR"})
# Prefixes that form one word, not a hyphenated compound: электроэнергетика.
_CLOSED_PREFIX = frozenset(
    {
        "авто", "авиа", "агро", "аэро", "био", "видео", "взаимо", "гео", "гидро",
        "двух", "еже", "инфра", "квази", "кино", "макро", "магнито", "меж",
        "микро", "много", "мульти", "низко", "одно", "паро", "пневмо", "полу",
        "противо", "прямо", "псевдо", "радио", "само", "сверх", "теле", "тепло",
        "трех", "трёх", "турбо", "ультра", "фото", "четырех", "четырёх",
        "электро", "энерго", "высоко",
    }
)
_NUMBER = r"[<>≤≥±]?\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?"
_GLUED_UNIT = r"мм2|см2|м2|м3|км2|%"
_SPACED_UNIT = (
    r"кВт·ч|кВт\*ч|кВ·А|кВА|МВА|Мвар|квар|МВт|кВт|ГВт|"
    r"мкА|мА|кА|кВ|МПа|кПа|ГПа|кОм|МОм|кГц|МГц|ГГц|Гц|об/мин|"
    r"мм|см|км|кг|мг|мин|сут|шт|°[CС]|Ом|[АВВтгсчм]"
)
_ATOM = re.compile(
    rf"(?:[A-Za-zА-Яа-яЁё]\s*=\s*{_NUMBER})"
    rf"|(?:{_NUMBER}\s*(?:{_GLUED_UNIT}))"
    rf"|(?:{_NUMBER}\s+(?:{_SPACED_UNIT})\b)"
    rf"|(?:{_NUMBER})"
)

_documents: list[dict[str, Any]] | None = None
_analyzer = None
_encoder = None


def chunk_rtf(
    source: str,
    filename: str,
    *,
    documents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    documents = _load_documents() if documents is None else documents
    document = _document_record(filename, documents)
    doc_id = _document_id(filename, document)
    doc_name = str(document.get("title") or document.get("short_title") or Path(filename).stem)
    flow, tables = parse_flow(source)
    labels = [_table_label(table.caption) for table in tables]
    prose_slots = [item for item in flow if item[0] == "prose"]
    chunks: list[dict[str, Any]] = []
    for point_ref, text, slot in _prose_units(prose_slots):
        parts = _split_point(text)
        for part_index, part in enumerate(parts):
            mentions = _mentions(part)
            table_refs = _resolve_mentions(mentions, labels, flow, slot, doc_id)
            chunk_id = f"{doc_id}:point:{point_ref or 'x'}:{slot}"
            if len(parts) > 1:
                chunk_id = f"{chunk_id}:{part_index}"
            chunks.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "chunk_id": chunk_id,
                    "text": part,
                    "payload": {
                        "kind": "point",
                        "doc_id": doc_id,
                        "doc_name": doc_name,
                        "filename": filename,
                        "point_ref": point_ref,
                        "point_number": point_ref,
                        "table_refs": table_refs,
                    },
                }
            )
    for index, table in enumerate(tables):
        chunks.extend(
            _table_chunks(
                table,
                doc_id=doc_id,
                doc_name=doc_name,
                filename=filename,
                table_index=index,
                documents=documents,
            )
        )
    return chunks


def document_id(filename: str, documents: list[dict[str, Any]] | None = None) -> str:
    documents = _load_documents() if documents is None else documents
    return _document_id(filename, _document_record(filename, documents))


def _document_record(filename: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    for document in documents:
        if document.get("filename") == filename:
            return document
    chapter = _PUE_CHAPTER.search(filename)
    if "пуэ" in filename.casefold() and chapter:
        return {
            "id": f"pue_{chapter.group(1)}_{chapter.group(2)}",
            "registry_key": f"pue_{chapter.group(1)}_{chapter.group(2)}",
            "filename": filename,
            "title": Path(filename).stem,
        }
    return {}


def _document_id(filename: str, document: dict[str, Any]) -> str:
    if document:
        return str(document.get("id") or document.get("registry_key") or "")
    stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", Path(filename).stem).strip("_")
    return stem.lower() or "document"


def load_documents(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the mr_norm registry in the exact-filename shape used by the RTF chunker."""
    registry_path = path or _REGISTRY
    raw = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    documents: list[dict[str, Any]] = []
    for raw_document in raw.get("documents") or []:
        document = dict(raw_document)
        document["id"] = str(document.get("id") or document.get("registry_key") or "")
        filenames = []
        if document.get("filename"):
            filenames.append(str(document["filename"]))
        filenames.extend(f"{stem}.rtf" for stem in document.get("match_stems") or [])
        for filename in dict.fromkeys(filenames):
            documents.append({**document, "filename": filename})
    return documents


def _load_documents() -> list[dict[str, Any]]:
    global _documents
    if _documents is None:
        _documents = load_documents()
    return _documents


def _prose_units(slots: list[tuple[str, Any]]) -> list[tuple[str, str, int]]:
    units: list[tuple[str, list[str], int]] = []
    for slot, (_, paragraph) in enumerate(slots):
        ref = _point_ref(paragraph)
        if ref and units:
            units.append((ref, [paragraph], slot))
        elif ref:
            units.append((ref, [paragraph], slot))
        elif units:
            units[-1][1].append(paragraph)
        else:
            units.append(("", [paragraph], slot))
    return [(ref, _squash(parts), slot) for ref, parts, slot in units if _squash(parts)]


def _point_ref(paragraph: str) -> str:
    match = _POINT.match(paragraph.strip())
    if not match:
        return ""
    ref = match.group(1)
    parts = ref.split(".")
    if len(parts) == 3 and parts[2].isdigit() and int(parts[2]) >= 1000 and int(parts[1]) <= 12:
        return ""
    return ref


def _table_chunks(
    table: PhysicalTable,
    *,
    doc_id: str,
    doc_name: str,
    filename: str,
    table_index: int,
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    caption = _squash([table.caption])
    label = _table_label(caption)
    base = {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "filename": filename,
        "table_id": f"{doc_id}:table:{table_index}",
        "table_index": table_index,
        "table_label": label,
        "caption": caption,
        "grid_ref": f"{doc_id}:table:{table_index}",
    }
    if _picture_only(table):
        return [
            {
                "schema_version": SCHEMA_VERSION,
                "chunk_id": f"{doc_id}:table:{table_index}:picture",
                "text": caption or "Схема",
                "payload": {**base, "kind": "table_row", "picture": True, "row_index": 0, "row_through": 0, "context": {}, "cells": {}, "refs": []},
            }
        ]
    grid = logical_grid(table)
    if not grid:
        return []
    header_rows = header_row_count(grid)
    headers = column_headers(grid, header_rows) if header_rows else [""] * len(grid[0])
    headers = [_dehyphenate(header) for header in headers]
    chunks = []
    for row_index in range(header_rows, len(grid)):
        placed = _place_row(grid, row_index, headers)
        if not placed["own"]:
            continue
        ordered = sorted(placed["inherited"] + placed["own"], key=lambda item: item["column"])
        text = " — ".join(part for part in [caption, *[piece for item in ordered for piece in item["pair"]]] if part)
        row_through = row_index
        for item in placed["own"]:
            row_through = max(row_through, row_index + item["rowspan"] - 1)
        chunks.append(
            {
                "schema_version": SCHEMA_VERSION,
                "chunk_id": f"{doc_id}:table:{table_index}:row:{row_index}",
                "text": text,
                "payload": {
                    **base,
                    "kind": "table_row",
                    "picture": False,
                    "row_index": row_index,
                    "row_through": row_through,
                    "context": _unique_fields(placed["inherited"]),
                    "cells": _unique_fields(placed["own"]),
                    "refs": _order_refs(" ".join(item["value"] for item in ordered), documents),
                },
            }
        )
    return chunks


def _place_row(grid, row_index: int, headers: list[str]) -> dict[str, list[dict[str, Any]]]:
    inherited: list[dict[str, Any]] = []
    own: list[dict[str, Any]] = []
    seen: set[int] = set()
    for column in range(len(grid[row_index])):
        located = _locate(grid, row_index, column)
        if located is None:
            continue
        origin_row, _origin_col, cell = located
        if id(cell) in seen:
            continue
        seen.add(id(cell))
        value = _squash([plain_text(cell.parts)])
        if not value or set(value) <= _DASHES:
            continue
        key = _short_header(headers[column] if column < len(headers) else "")
        item = {
            "column": column,
            "key": key or f"col{column}",
            "value": value,
            "rowspan": cell.rowspan,
            "pair": (f"{_header_path(headers[column])} — {value}" if _header_path(headers[column]) else value,),
        }
        if origin_row == row_index:
            own.append(item)
        else:
            inherited.append(item)
    return {"inherited": inherited, "own": own}


def _locate(grid, row_index: int, column: int):
    cell = grid[row_index][column]
    if not cell.covered:
        return row_index, column, cell
    for probe in range(row_index, -1, -1):
        for left in range(column, -1, -1):
            candidate = grid[probe][left]
            if candidate.covered:
                continue
            if left + candidate.colspan > column and probe + candidate.rowspan > row_index:
                return probe, left, candidate
            break
    return None


def _picture_only(table: PhysicalTable) -> bool:
    cells = [cell for row in table.rows for cell in row.cells if not cell.synthetic]
    if len(cells) != 1 or not cells[0].pictures:
        return False
    return not _squash([cells[0].text])


def _unique_fields(items: list[dict[str, Any]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in items:
        key = item["key"]
        if key not in fields:
            fields[key] = item["value"]
            continue
        suffix = 2
        while f"{key} ({suffix})" in fields:
            suffix += 1
        fields[f"{key} ({suffix})"] = item["value"]
    return fields


def _header_path(path: str) -> str:
    return re.sub(r",\s*\d+$", "", path).strip()


def _short_header(path: str) -> str:
    path = _header_path(path)
    if ", " in path:
        return path.split(", ")[-1].strip()
    return path


def _table_label(caption: str) -> str:
    match = _TABLE_LABEL.search(caption)
    if match:
        return match.group(1)
    match = _LEADING_LABEL.match(caption)
    return match.group(1) if match else ""


def _mentions(text: str) -> list[str]:
    found = []
    for match in _TABLE_MENTION.finditer(text):
        label = match.group(1)
        if label not in found:
            found.append(label)
    return found


def _resolve_mentions(
    mentions: list[str],
    labels: list[str],
    flow: list[tuple[str, Any]],
    prose_slot: int,
    doc_id: str,
) -> list[dict[str, str]]:
    prose_at = _flow_index(flow, "prose", prose_slot)
    table_at = {index: _flow_index(flow, "table", index) for index in range(len(labels))}
    refs = []
    for label in mentions:
        candidates = [index for index, item in enumerate(labels) if item == label]
        if not candidates:
            continue
        chosen = min(candidates, key=lambda index: abs(table_at[index] - prose_at))
        refs.append({"table_id": f"{doc_id}:table:{chosen}", "label": label})
    return refs


def _flow_index(flow: list[tuple[str, Any]], kind: str, slot: int) -> int:
    seen = 0
    for index, item in enumerate(flow):
        if item[0] != kind:
            continue
        if seen == slot:
            return index
        seen += 1
    return 0


def _order_refs(text: str, documents: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    documents = _load_documents() if documents is None else documents
    refs = []
    seen: set[tuple[str, str]] = set()
    for match in _ORDER.finditer(text):
        date, number = match.group(1), match.group(2)
        if (date, number) in seen:
            continue
        seen.add((date, number))
        iso = f"{date[6:10]}-{date[3:5]}-{date[0:2]}"
        doc_id = ""
        for document in documents:
            if document.get("doc_number") == number and document.get("adoption_date") == iso:
                doc_id = str(document["id"])
                break
        refs.append({"doc_id": doc_id, "mention": f"приказ от {date} № {number}"})
    return refs


def _squash(parts: list[str]) -> str:
    return _dehyphenate(_SPACE.sub(" ", " ".join(parts)).strip())


def _dehyphenate(text: str) -> str:
    """Join a hyphen that only exists because the line broke inside a word.

    «Предостав- ляют» and «бал- льной» become one word. A lexical hyphen stays:
    «информационно-телекоммуникационные», «начало-окончание», «взрыво- и».
    """
    if "-" not in text:
        return text
    previous = None
    while previous != text:
        previous = text
        match = _last_hyphen_break(text)
        if match is None:
            break
        # The rightmost break first, so «житель- ность» joins before «Продол- житель».
        text = text[: match.start()] + _resolve_hyphen(match.group(1), match.group(2)) + text[match.end() :]
    return text


def _last_hyphen_break(text: str) -> re.Match[str] | None:
    last = None
    start = 0
    while True:
        match = _HYPHEN_BREAK.search(text, start)
        if match is None:
            return last
        last = match
        start = match.start() + 1


def _resolve_hyphen(left: str, right: str) -> str:
    if right.lower() in {"и", "или"}:
        return f"{left}- {right}"
    if left.lower() in _CLOSED_PREFIX:
        return left + right
    joined = left + right
    if _keeps_lexical_hyphen(left, right, joined):
        return f"{left}-{right}"
    return joined


def _keeps_lexical_hyphen(left: str, right: str, joined: str) -> bool:
    both_words = _content_word(left) and _content_word(right)
    if both_words and not _is_known(joined):
        return True
    if _compound_stem(left):
        # «условного» is one word: the piece after the hyphen is only an ending.
        if _is_known(joined) and not _content_word(right):
            return False
        return True
    if len(left) >= 6 and left.lower().endswith(("но", "ско", "ски", "ико")) and (
        _content_word(right) or len(right) >= 7
    ):
        # «волоконно-оптический» is also accepted by the dictionary as one word.
        # The hyphen stays when the part after it is a word of its own.
        # «наименование» still joins: the closed form is known and «вание» is not a word.
        if _is_known(joined) and not _content_word(right):
            return False
        return True
    return False


def _compound_stem(word: str) -> bool:
    low = word.lower()
    if len(low) < 6 or not low.endswith(("но", "ско", "ски")):
        return False
    return any(parse.is_known and parse.tag.POS in {"ADVB", "ADJS"} for parse in _parsed(word))


def _content_word(word: str) -> bool:
    if len(word) < 4:
        return False
    return any(parse.is_known and parse.tag.POS in _CONTENT_POS for parse in _parsed(word))


def _is_known(word: str) -> bool:
    return any(parse.is_known for parse in _parsed(word))


@lru_cache(maxsize=20000)
def _parsed(word: str):
    return _morph().parse(word)


def _morph():
    global _analyzer
    if _analyzer is None:
        import pymorphy3

        _analyzer = pymorphy3.MorphAnalyzer()
    return _analyzer


def token_count(text: str) -> int:
    """Tokens by tiktoken cl100k. About two characters per token on this corpus."""
    if not text:
        return 0
    return len(_token_encoder().encode(text))


def _token_encoder():
    global _encoder
    if _encoder is None:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _split_point(text: str, max_tokens: int = MAX_POINT_TOKENS) -> list[str]:
    """Keep a point whole up to max_tokens. Never cut a number from its unit."""
    text = text.strip()
    if not text:
        return []
    if token_count(text) <= max_tokens:
        return [text]
    return _pack([piece.strip() for piece in _SENTENCE.split(text) if piece.strip()], max_tokens)


def _pack(pieces: list[str], max_tokens: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if token_count(piece) > max_tokens:
            if current:
                parts.append(current)
                current = ""
            parts.extend(_split_long(piece, max_tokens))
            continue
        candidate = f"{current} {piece}".strip()
        if current and token_count(candidate) > max_tokens:
            parts.append(current)
            current = piece
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _split_long(text: str, max_tokens: int) -> list[str]:
    parts: list[str] = []
    current = ""
    current_tokens = 0
    for piece in _atomic_pieces(text):
        if not piece or piece.isspace():
            if current:
                current += piece
            continue
        extra = token_count(piece if not current else " " + piece)
        if current and current_tokens + extra > max_tokens:
            parts.append(current.strip())
            current = ""
            current_tokens = 0
            extra = token_count(piece)
        current += piece
        current_tokens += extra
    if current.strip():
        parts.append(current.strip())
    return parts


def _atomic_pieces(text: str) -> list[str]:
    """Words, with a number and the unit after it kept as one piece."""
    pieces: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if index == 0 or not (text[index - 1].isalnum() or text[index - 1] in ",."):
            match = _ATOM.match(text, index)
            if match and match.end() > index:
                pieces.append(match.group())
                index = match.end()
                continue
        if text[index].isspace():
            end = index + 1
            while end < length and text[end].isspace():
                end += 1
            pieces.append(text[index:end])
            index = end
            continue
        end = index + 1
        while end < length and not text[end].isspace():
            end += 1
        pieces.append(text[index:end])
        index = end
    return pieces
