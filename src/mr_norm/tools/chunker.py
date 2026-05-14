from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.chunker_document_menu import internal_doc_kind_to_payload_label, resolve_payload_authority
from mr_norm.tools.metadata_manifest import MetadataManifestEntry, write_metadata_manifest
from mr_norm.tools.rtf_processor import atomic_write_json, extract_point_number
from mr_norm.tools.schema import SCHEMA_VERSION, ParagraphRecord, StructuredDocument


class MetadataExtractionError(ValueError):
    """Document body lacks registration/title lines required for chunk payload (no filename guessing)."""


REQUIRED_RAG_NORM_PAYLOAD_KEYS = {
    "filename",
    "doc_name",
    "doc_reg",
    "doc_title_full",
    "approving_act",
    "metadata_source",
    "metadata_confidence",
    "headings",
    "nearest_heading",
    "heading_path_text",
    "chapter_heading",
    "chapter_number",
    "chapter_title",
    "section_heading",
    "section_number",
    "section_title",
    "article_heading",
    "article_number",
    "article_title",
    "retrieval_anchor_heading",
    "point_number",
    "point_scope",
    "point_anchor",
    "point_identity_key",
    "chunk_index",
    "chunk_start",
    "part_index",
    "total_parts",
    "is_split",
}


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    joined = "\n".join(str(part) for part in parts)
    digest = hashlib.sha1(joined.encode("utf-8", errors="ignore")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def clean_marker_text(text: str) -> str:
    text = re.sub(r"^\s*(#{1,9}|\*{3}|//)\s*", "", text or "")
    text = re.sub(r"\s*(#{1,9}|\*{3}|\\)\s*$", "", text)
    text = re.sub(r"\*{2,}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_structured_heading(heading_text: str, kind: str) -> dict[str, str]:
    kind_patterns = {
        "article": r"^\s*Статья\s+([0-9]+(?:[._][0-9]+)*)\.?\s*(.*)$",
        "chapter": r"^\s*Глава\s+([0-9]+(?:[._][0-9]+)*)\.?\s*(.*)$",
        "section": r"^\s*Раздел\s+([IVXLCDM0-9]+)\.?\s*(.*)$",
    }
    match = re.match(kind_patterns[kind], heading_text or "", re.IGNORECASE)
    if not match:
        return {"heading": "", "number": "", "title": ""}
    return {
        "heading": heading_text.strip(),
        "number": match.group(1).strip().replace("_", "."),
        "title": match.group(2).strip(" ."),
    }


def build_structured_heading_payload(current_headings: list[str], nearest_heading: str) -> dict[str, str]:
    article_info = {"heading": "", "number": "", "title": ""}
    chapter_info = {"heading": "", "number": "", "title": ""}
    section_info = {"heading": "", "number": "", "title": ""}
    for heading in current_headings:
        article = parse_structured_heading(heading, "article")
        chapter = parse_structured_heading(heading, "chapter")
        section = parse_structured_heading(heading, "section")
        if article["heading"]:
            article_info = article
        if chapter["heading"]:
            chapter_info = chapter
        if section["heading"]:
            section_info = section
    retrieval_anchor = (
        article_info["heading"] or section_info["heading"] or chapter_info["heading"] or nearest_heading
    )
    return {
        "heading_path_text": " > ".join(current_headings) if current_headings else "",
        "chapter_heading": chapter_info["heading"],
        "chapter_number": chapter_info["number"],
        "chapter_title": chapter_info["title"],
        "section_heading": section_info["heading"],
        "section_number": section_info["number"],
        "section_title": section_info["title"],
        "article_heading": article_info["heading"],
        "article_number": article_info["number"],
        "article_title": article_info["title"],
        "retrieval_anchor_heading": retrieval_anchor,
    }


def build_point_identity_payload(
    point_number: str,
    structured_heading_payload: dict[str, str],
    chunk_start: int,
    chunk_index: int,
) -> dict[str, str]:
    scope = (
        structured_heading_payload.get("retrieval_anchor_heading", "").strip()
        or structured_heading_payload.get("heading_path_text", "").strip()
        or "__no_heading__"
    )
    number = point_number.strip() if point_number else "__unnumbered__"
    anchor = f"{chunk_start}:{chunk_index}"
    return {
        "point_scope": scope,
        "point_anchor": anchor,
        "point_identity_key": f"{number}::{scope}::{anchor}",
    }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def doc_kind_from_name(doc_name: str) -> str:
    upper = (doc_name or "").upper()
    if "ФЕДЕРАЛЬНЫЙ ЗАКОН" in upper or re.search(r"\bЗАКОН\b", upper):
        return "law"
    if "ПОСТАНОВЛЕНИЕ" in upper:
        return "decree"
    if "РАСПОРЯЖЕНИЕ" in upper:
        return "decree"
    if "ПРИКАЗ" in upper:
        return "order"
    if "ГОСТ" in upper:
        return "gost"
    if "ПУЭ" in upper or "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК" in upper:
        return "pue"
    return "unknown"


def resolve_doc_kind(doc_name: str, doc_reg: str = "") -> str:
    for value in (doc_name, doc_reg):
        kind = doc_kind_from_name(value)
        if kind != "unknown":
            return kind
    return "unknown"


def extract_doc_number(text: str, doc_name: str = "", doc_kind: str = "") -> str:
    if doc_kind == "gost":
        standard = re.search(r"\bГОСТ\s+(?:Р\s+)?([A-Za-zА-Яа-я0-9.\-]+)", doc_name or text or "", re.IGNORECASE)
        if standard:
            return standard.group(1).strip()
    match = re.search(r"(?:[Nn№])\s*([A-Za-zА-Яа-я0-9\-/]+)", text or "")
    if match:
        return match.group(1).strip()
    standard = re.search(r"\bГОСТ\s+(?:Р\s+)?([A-Za-zА-Яа-я0-9.\-]+)", doc_name or text or "", re.IGNORECASE)
    if standard:
        return standard.group(1).strip()
    law = re.search(r"\b(\d+\s*-\s*ФЗ)\b", doc_name or text or "", re.IGNORECASE)
    if law:
        return re.sub(r"\s+", "", law.group(1)).upper()
    return ""


def extract_doc_date(text: str) -> str:
    match = re.search(
        r"от\s+(\d{1,2}[._\s]\d{1,2}[._\s]\d{4}|\d{1,2}\s+[А-Яа-я]+\s+\d{4})",
        text or "",
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


# ПУЭ 7-е издание: утверждено приказом Минэнерго России от 08.07.2002 №204; введение 01.01.2003
PUE_SEVENTH_APPROVING_ACT = (
    "Приказом Министерства энергетики Российской Федерации от 8 июля 2002 г. № 204"
)
PUE_SEVENTH_DOC_DATE = "1 января 2003"
PUE_SEVENTH_AUTHORITY = "Министерство энергетики Российской Федерации"


def extract_metadata(document: StructuredDocument) -> tuple[dict[str, str], str, str]:
    first_lines = [clean_marker_text(p.text) for p in document.paragraphs[:80] if p.text]
    doc_type = ""
    doc_reg = "Не указано"
    reg_source = "not_extracted"
    title_source = "not_extracted"

    for line in first_lines[:30]:
        upper = line.upper()
        if upper in {"ПРИКАЗ", "ПОСТАНОВЛЕНИЕ", "РАСПОРЯЖЕНИЕ", "УКАЗ"}:
            doc_type = upper.title()
            break

    for line in first_lines[:30]:
        if re.search(r"^от\s+.+?(?:[Nn№])\s*[A-Za-zА-Яа-я0-9\-/]+", line, re.IGNORECASE):
            doc_reg = f"{doc_type} {line}".strip() if doc_type else line
            reg_source = "initial_lines_header"
            break

    if doc_reg == "Не указано":
        approval = find_approval_act(first_lines)
        if not approval:
            all_lines = [clean_marker_text(p.text) for p in document.paragraphs if p.text]
            approval = find_approval_act(all_lines)
        if approval:
            doc_reg = approval
            reg_source = "initial_lines_approved_phrase"

    if doc_reg == "Не указано":
        early_joined = " ".join(clean_marker_text(p.text or "") for p in document.paragraphs[:50]).upper()
        law_like = "ФЕДЕРАЛЬНЫЙ ЗАКОН" in early_joined or bool(re.search(r"\bКОДЕКС\b", early_joined))
        if law_like:
            law_sig = find_federal_law_signature_reg(document.paragraphs)
            if law_sig:
                doc_reg = law_sig
                reg_source = "law_signature_tail"

    if doc_reg == "Не указано":
        body_upper = " ".join(clean_marker_text(p.text or "") for p in document.paragraphs).upper()
        pue_like = "ПУЭ" in body_upper or "ПРАВИЛА УСТРОЙСТВА" in body_upper
        if pue_like:
            doc_reg = PUE_SEVENTH_APPROVING_ACT
            reg_source = "pue_seventh_edition_canonical"

    candidates: list[tuple[int, int, str]] = []
    for idx, line in enumerate(first_lines[:45]):
        stripped = line.strip()
        upper = stripped.upper()
        max_len = 2000 if upper.startswith(("О ", "ОБ ")) else 280
        if len(stripped) < 8 or len(stripped) > max_len:
            continue
        score = 0
        if any(
            word in upper
            for word in [
                "ОБ УТВЕРЖДЕНИИ",
                "О ВНЕСЕНИИ",
                "ПРАВИЛ",
                "ТРЕБОВАН",
                "ГОСТ",
                "ФЕДЕРАЛЬНЫЙ ЗАКОН",
                "КОДЕКС",
                "УСТАВ",
            ]
        ):
            score += 4
        if stripped.endswith(".") and len(stripped) > 120:
            score -= 2
        if score > 0:
            candidates.append((score, -idx, stripped))

    if candidates:
        candidates.sort(reverse=True)
        doc_title_full = candidates[0][2]
        title_source = "initial_lines_title"
    else:
        doc_title_full = ""

    if not doc_title_full:
        for line in first_lines[:20]:
            stripped = line.strip()
            if len(stripped) >= 22 and stripped.startswith("[") and stripped.endswith("]"):
                inner = stripped[1:-1].strip()
                if len(inner) >= 15:
                    doc_title_full = inner
                    title_source = "preamble_bracketed_subject"
                    break

    if not doc_title_full:
        for line in first_lines[2:30]:
            stripped = line.strip()
            if len(stripped) < 30 or len(stripped) > 2000:
                continue
            if re.match(r"^от\s+\d", stripped, re.IGNORECASE):
                continue
            upper_st = stripped.upper()
            if upper_st.startswith("ОБ ") or (upper_st.startswith("О ") and not upper_st.startswith("ОТ ")):
                doc_title_full = stripped
                title_source = "preamble_o_subject"
                break

    confidence = "high" if reg_source.startswith("initial") and title_source.startswith("initial") else "medium"
    if reg_source == "not_extracted" and title_source == "not_extracted":
        confidence = "low"

    result: dict[str, str] = {
        "doc_name": doc_title_full,
        "doc_reg": doc_reg,
        "doc_title_full": doc_title_full,
        "approving_act": doc_reg,
        "metadata_source": f"reg:{reg_source};title:{title_source}",
        "metadata_confidence": confidence,
    }
    if reg_source == "pue_seventh_edition_canonical":
        result["doc_date"] = PUE_SEVENTH_DOC_DATE
        result["authority"] = PUE_SEVENTH_AUTHORITY

    manifest_kind = ""
    manifest_detail = ""
    if reg_source == "pue_seventh_edition_canonical":
        manifest_kind = "pue_canonical"
        manifest_detail = "Канон ПУЭ 7-е изд.: приказ Минэнерго от 08.07.2002 №204; введение 01.01.2003"
    elif title_source in ("preamble_o_subject", "preamble_bracketed_subject"):
        manifest_kind = "title_fallback"
        manifest_detail = f"title_source={title_source}"
    elif confidence == "low":
        manifest_kind = "title_fallback"
        manifest_detail = "metadata_confidence=low"

    return result, manifest_kind, manifest_detail


def validate_chunk_metadata(metadata: dict[str, str], source: str) -> None:
    title = (metadata.get("doc_title_full") or "").strip()
    if not title:
        raise MetadataExtractionError(
            f"{source}: missing doc_title_full — title must come from document preamble, not filename"
        )
    reg = (metadata.get("doc_reg") or "").strip()
    if not reg or reg == "Не указано":
        raise MetadataExtractionError(
            f"{source}: missing registration or approving-act line in opening paragraphs"
        )


def find_approval_act(lines: list[str]) -> str:
    cap = min(1500, len(lines))
    joined = " ".join(lines[:cap])
    pattern = re.compile(
        r"((?:Приказом|Постановлением|Распоряжением)[^.]{0,240}?от\s+"
        r"(?:\d{1,2}[._\s]\d{1,2}[._\s]\d{4}|\d{1,2}\s+[А-Яа-я]+\s+\d{4})"
        r"(?:\s*г\.?)?[^.]{0,120}?(?:[Nn№])\s*[A-Za-zА-Яа-я0-9\-/]+)",
        re.IGNORECASE,
    )
    match = pattern.search(joined)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    pattern_utv = re.compile(
        r"(Утвержден[оа]\s+.+?приказ\s+от\s+"
        r"(?:\d{1,2}[._\s]\d{1,2}[._\s]\d{4}|\d{1,2}\s+[а-яёА-ЯЁ]+\s+\d{4})"
        r"(?:\s*г\.?)?.*?(?:[Nn№])\s*\d+)",
        re.IGNORECASE | re.DOTALL,
    )
    match_utv = pattern_utv.search(joined)
    if match_utv:
        return re.sub(r"\s+", " ", match_utv.group(1)).strip()
    return ""


def find_federal_law_signature_reg(paragraphs: list[ParagraphRecord]) -> str:
    """Typical consolidated law: date line + short «N …-ФЗ» at the end (still document body, not filename)."""
    tail_len = min(len(paragraphs), 800)
    tail = paragraphs[-tail_len:]
    texts = [clean_marker_text(p.text) for p in tail]
    num_pat = re.compile(r"^(?:[Nn№])\s*(\d+\s*-\s*ФЗ)\.?\s*$", re.IGNORECASE)
    date_pat = re.compile(r"^\d{1,2}\s+[а-яёА-ЯЁ]+\s+\d{4}\s*года?\s*$", re.IGNORECASE)
    for i in range(len(texts) - 1, 0, -1):
        if not num_pat.match((texts[i] or "").strip()):
            continue
        prev = (texts[i - 1] or "").strip()
        if date_pat.match(prev):
            date_brief = prev.replace(" года", " г.").strip()
            return f"Федеральный закон от {date_brief} {(texts[i] or '').strip()}"
    return ""


@dataclass
class ChunkUnit:
    text: str
    paragraphs: list[ParagraphRecord]
    headings: list[str]
    point_number: str
    chunk_start: int
    char_start: int
    char_end: int


class ChunkBuilder:
    def __init__(self, paths: ProjectPaths, max_chars: int = 1600):
        self.paths = paths
        self.max_chars = max_chars
        self.manifest_pue: list[MetadataManifestEntry] = []
        self.manifest_other: list[MetadataManifestEntry] = []

    def build_all(self, structured_paths: list[Path] | None = None) -> list[dict[str, Any]]:
        self.manifest_pue = []
        self.manifest_other = []
        paths = structured_paths or sorted(self.paths.marked_docs_dir.glob("*.structured.json"))
        chunks: list[dict[str, Any]] = []
        for path in paths:
            document = StructuredDocument.from_json_path(path)
            if document.read_error:
                continue
            if document.paragraphs:
                meta, mk, mdetail = extract_metadata(document)
                validate_chunk_metadata(meta, document.source_file or document.filename)
                if mk:
                    entry = MetadataManifestEntry(
                        filename=document.filename,
                        source_file=document.source_file,
                        category=mk,
                        detail=mdetail,
                        doc_title_full=meta.get("doc_title_full", ""),
                        doc_reg=meta.get("doc_reg", ""),
                        approving_act=meta.get("approving_act", ""),
                        doc_date=(meta.get("doc_date") or "").strip()
                        or extract_doc_date(meta.get("doc_reg", "")),
                        authority=(meta.get("authority") or "").strip()
                        or extract_authority(meta.get("doc_reg", "")),
                    )
                    if mk == "pue_canonical":
                        self.manifest_pue.append(entry)
                    else:
                        self.manifest_other.append(entry)
                chunks.extend(self.build_document_chunks(document, payload_metadata=meta))
        if self.paths is not None:
            atomic_write_json(self.paths.chunks_json, chunks)
            write_metadata_manifest(
                self.paths.metadata_manifest_md,
                self.manifest_pue,
                self.manifest_other,
            )
        return chunks

    def build_document_chunks(
        self,
        document: StructuredDocument,
        payload_metadata: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if payload_metadata is not None:
            metadata = payload_metadata
        else:
            metadata, _, _ = extract_metadata(document)
            validate_chunk_metadata(metadata, document.source_file or document.filename)
        doc_identity = metadata["doc_name"] or document.source_file or document.filename
        doc_id = stable_id("doc", doc_identity, metadata["doc_reg"])
        units = self._make_units(document)
        chunks: list[dict[str, Any]] = []
        for chunk_index, unit in enumerate(units):
            chunks.extend(self._make_chunk_records(document, metadata, doc_id, unit, chunk_index))
        return chunks

    def _make_units(self, document: StructuredDocument) -> list[ChunkUnit]:
        heading_stack: list[tuple[int, str]] = []
        units: list[ChunkUnit] = []
        current: list[ParagraphRecord] = []
        current_headings: list[str] = []
        current_point = ""

        def flush() -> None:
            nonlocal current, current_headings, current_point
            if not current:
                return
            text = "\n".join(p.text for p in current).strip()
            if text:
                units.append(
                    ChunkUnit(
                        text=text,
                        paragraphs=current,
                        headings=current_headings,
                        point_number=current_point or extract_point_number(text),
                        chunk_start=current[0].paragraph_index,
                        char_start=current[0].char_start,
                        char_end=current[-1].char_end,
                    )
                )
            current = []
            current_headings = []
            current_point = ""

        for paragraph in document.paragraphs:
            if paragraph.is_heading:
                flush()
                level = int(paragraph.heading_level or 1)
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, clean_marker_text(paragraph.text)))
                continue

            point = paragraph.point_number or extract_point_number(paragraph.text)
            if current and point:
                flush()
            if not current:
                current_headings = [text for _, text in heading_stack]
                current_point = point
            elif point and point != current_point:
                flush()
                current_headings = [text for _, text in heading_stack]
                current_point = point
            current.append(paragraph)
        flush()
        return units

    def _make_chunk_records(
        self,
        document: StructuredDocument,
        metadata: dict[str, str],
        doc_id: str,
        unit: ChunkUnit,
        chunk_index: int,
    ) -> list[dict[str, Any]]:
        parts = split_text(unit.text, self.max_chars)
        records: list[dict[str, Any]] = []
        effective_headings = unit.headings
        nearest_heading = effective_headings[-1] if effective_headings else ""
        structured_heading_payload = build_structured_heading_payload(effective_headings, nearest_heading)
        point_payload = build_point_identity_payload(
            unit.point_number,
            structured_heading_payload,
            unit.chunk_start,
            chunk_index,
        )
        point_id = stable_id("point", doc_id, point_payload["point_identity_key"])
        for part_index, part in enumerate(parts):
            is_split = len(parts) > 1
            chunk_id = stable_id("chunk", doc_id, point_id, part_index, normalize_for_hash(part))
            internal_doc_kind = resolve_doc_kind(metadata["doc_name"], metadata["doc_reg"])
            payload_doc_kind = internal_doc_kind_to_payload_label(internal_doc_kind)
            payload = {
                "source_file": document.source_file,
                "filename": document.filename,
                "doc_id": doc_id,
                **metadata,
                "doc_kind": payload_doc_kind,
                "doc_number": extract_doc_number(
                    metadata["doc_reg"], metadata["doc_name"], internal_doc_kind
                ),
                "doc_date": (metadata.get("doc_date") or "").strip()
                or extract_doc_date(metadata["doc_reg"]),
                "authority": resolve_payload_authority(metadata, internal_doc_kind, extract_authority),
                "headings": list(effective_headings),
                "heading_path": list(effective_headings),
                "nearest_heading": nearest_heading,
                **structured_heading_payload,
                "point_number": unit.point_number,
                **point_payload,
                "point_id": point_id,
                "chunk_index": chunk_index,
                "chunk_start": unit.chunk_start,
                "char_start": unit.char_start,
                "char_end": unit.char_end,
                "token_estimate": estimate_tokens(part),
                "is_complete_point": not is_split,
                "split_reason": "max_chars" if is_split else "",
                "part_index": part_index,
                "total_parts": len(parts),
                "is_split": is_split,
            }
            records.append({"schema_version": SCHEMA_VERSION, "chunk_id": chunk_id, "text": part, "payload": payload})
        return records


def extract_authority(doc_reg: str) -> str:
    text = doc_reg or ""
    lowered = text.lower()
    if "правительств" in lowered:
        return "Правительство РФ"
    if "федерального агентства по техническому регулированию" in lowered or "росстандарт" in lowered:
        return "Росстандарт"
    if re.search(r"минэнерго(\s+россии)?\b", lowered):
        return "Министерство энергетики Российской Федерации"
    ministry = re.search(r"(Министерств[ао].{0,120}?)(?:\s+от\b|[,.;]|$)", text, re.IGNORECASE)
    if ministry:
        return ministry.group(1).strip()
    return ""


def normalize_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def split_text(text: str, max_chars: int) -> list[str]:
    text = clean_marker_text(text)
    if len(text) <= max_chars:
        return [text] if text else []
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            parts.append(current)
            current = ""
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            parts.extend(split_long_paragraph(paragraph, max_chars))
    if current:
        parts.append(current)
    return parts


def split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                parts.append(current)
            current = sentence[:max_chars]
            rest = sentence[max_chars:]
            while rest:
                parts.append(current)
                current = rest[:max_chars]
                rest = rest[max_chars:]
    if current:
        parts.append(current)
    return parts


def load_chunks(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("chunks"), list):
        return data["chunks"]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unsupported chunks JSON structure: {path}")


def missing_payload_keys(chunk: dict[str, Any]) -> set[str]:
    payload = chunk.get("payload") or {}
    return REQUIRED_RAG_NORM_PAYLOAD_KEYS - set(payload)


def duplicate_identity_count(chunks: list[dict[str, Any]]) -> int:
    seen: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        payload = chunk.get("payload") or {}
        text_hash = stable_id("text", normalize_for_hash(chunk.get("text", "")), length=12)
        key = f"{payload.get('doc_name', '')}|{payload.get('point_number', '')}|{text_hash}"
        seen[key] += 1
    return sum(count - 1 for count in seen.values() if count > 1)
