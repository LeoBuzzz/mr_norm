from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.chunker import load_chunks

ORDER_NUMBER_PATTERNS = (
    re.compile(r"(?:приказ|постановление|распоряжение).*?(?:№|n\.?|номер)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:№|n\.?)\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bn[_\s]*(\d+)\b", re.IGNORECASE),
)
POINT_NUMBER_PATTERN = re.compile(
    r"(?:п\.?|пункт|п\.?\s*)\s*(\d+(?:\.\d+)*)|(?:^|\s)(\d+\.\d+(?:\.\d+)*)\s",
    re.IGNORECASE,
)
KNOWN_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "пуэ": ("правила устройства электроустановок", "электроустановок"),
    "птэ": ("правила технической эксплуатации", "технической эксплуатации"),
    "озп": ("отопительный сезон", "готовности"),
}


def _token_roots(text: str) -> list[str]:
    roots: list[str] = []
    for token in normalize_catalog_text(text).split():
        if len(token) <= 3:
            continue
        roots.append(token[: max(4, len(token) - 2)])
    return roots


def _alias_overlap_score(query_norm: str, alias_norm: str) -> float:
    alias_roots = _token_roots(alias_norm)
    if len(alias_roots) < 2:
        return 0.0
    hits = sum(1 for root in alias_roots if root in query_norm)
    return hits / len(alias_roots)


def normalize_catalog_text(value: str) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def extract_order_numbers(*texts: str) -> list[str]:
    numbers: list[str] = []
    for text in texts:
        for pattern in ORDER_NUMBER_PATTERNS:
            for match in pattern.finditer(text or ""):
                number = match.group(1).strip()
                if number and number not in numbers:
                    numbers.append(number)
    return numbers


def extract_point_number_hint(query: str) -> str:
    match = POINT_NUMBER_PATTERN.search(query or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def _acronym_from_doc_name(doc_name: str) -> str:
    words = [word for word in re.findall(r"[A-Za-zА-Яа-я0-9]+", doc_name) if len(word) > 2]
    if len(words) < 2:
        return ""
    return "".join(word[0] for word in words[:6]).lower()


@dataclass(frozen=True)
class DocumentCatalogEntry:
    catalog_id: str
    doc_name: str
    filename: str = ""
    doc_id: str = ""
    aliases: tuple[str, ...] = ()
    order_numbers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentCatalog:
    entries: list[DocumentCatalogEntry] = field(default_factory=list)
    source_path: str = ""

    def by_id(self) -> dict[str, DocumentCatalogEntry]:
        return {entry.catalog_id: entry for entry in self.entries}

    def by_doc_name(self) -> dict[str, DocumentCatalogEntry]:
        return {entry.doc_name: entry for entry in self.entries}


@dataclass(frozen=True)
class DocumentCandidate:
    catalog_id: str
    doc_name: str
    score: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "doc_name": self.doc_name,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
        }


def _build_entry_aliases(doc_name: str, filename: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in (doc_name, filename):
        normalized = normalize_catalog_text(value)
        if normalized and normalized not in aliases:
            aliases.append(normalized)
    acronym = _acronym_from_doc_name(doc_name)
    if acronym and acronym not in aliases:
        aliases.append(acronym)
    for number in extract_order_numbers(doc_name, filename):
        token = f"приказ {number}"
        if token not in aliases:
            aliases.append(token)
        if number not in aliases:
            aliases.append(number)
    words = normalize_catalog_text(doc_name).split()
    if len(words) >= 4:
        short = " ".join(words[:4])
        if short not in aliases:
            aliases.append(short)
    if len(words) >= 6:
        short = " ".join(words[:6])
        if short not in aliases:
            aliases.append(short)
    return tuple(aliases)


def build_catalog_from_chunks(chunks_path: Path) -> DocumentCatalog:
    if not chunks_path.is_file():
        return DocumentCatalog(entries=[], source_path=str(chunks_path))

    by_doc_name: dict[str, DocumentCatalogEntry] = {}
    for chunk in load_chunks(chunks_path):
        payload = chunk.get("payload") or chunk
        doc_name = str(payload.get("doc_name") or "").strip()
        if not doc_name:
            continue
        filename = str(payload.get("filename") or "").strip()
        doc_id = str(payload.get("doc_id") or "").strip()
        if doc_name in by_doc_name:
            continue
        catalog_id = doc_id or f"doc_{len(by_doc_name) + 1}"
        order_numbers = tuple(extract_order_numbers(doc_name, filename))
        by_doc_name[doc_name] = DocumentCatalogEntry(
            catalog_id=catalog_id,
            doc_name=doc_name,
            filename=filename,
            doc_id=doc_id,
            aliases=_build_entry_aliases(doc_name, filename),
            order_numbers=order_numbers,
        )
    entries = sorted(by_doc_name.values(), key=lambda item: item.doc_name)
    return DocumentCatalog(entries=entries, source_path=str(chunks_path))


def save_catalog_snapshot(catalog: DocumentCatalog, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "mr_document_catalog_v1",
        "source_path": catalog.source_path,
        "entries": [entry.to_dict() for entry in catalog.entries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_catalog_snapshot(path: Path) -> DocumentCatalog:
    if not path.is_file():
        return DocumentCatalog(entries=[], source_path=str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = [
        DocumentCatalogEntry(
            catalog_id=str(item.get("catalog_id") or ""),
            doc_name=str(item.get("doc_name") or ""),
            filename=str(item.get("filename") or ""),
            doc_id=str(item.get("doc_id") or ""),
            aliases=tuple(item.get("aliases") or ()),
            order_numbers=tuple(str(number) for number in item.get("order_numbers") or ()),
        )
        for item in payload.get("entries") or []
        if item.get("doc_name")
    ]
    return DocumentCatalog(entries=entries, source_path=str(payload.get("source_path") or path))


def load_document_catalog(
    *,
    chunks_path: Path,
    snapshot_path: Path | None = None,
    refresh: bool = False,
) -> DocumentCatalog:
    snapshot = snapshot_path or chunks_path.parent / "document_catalog.json"
    if snapshot.is_file() and not refresh:
        catalog = load_catalog_snapshot(snapshot)
        if catalog.entries:
            return catalog
    catalog = build_catalog_from_chunks(chunks_path)
    if catalog.entries:
        save_catalog_snapshot(catalog, snapshot)
    return catalog


def _score_entry(query_norm: str, entry: DocumentCatalogEntry) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    doc_norm = normalize_catalog_text(entry.doc_name)

    if query_norm and query_norm in doc_norm:
        score += 0.45
        reasons.append("doc_name_contains_query")
    if doc_norm and doc_norm in query_norm:
        score += 0.35
        reasons.append("query_contains_doc_name")

    ratio = SequenceMatcher(None, query_norm, doc_norm).ratio() if query_norm and doc_norm else 0.0
    if ratio >= 0.55:
        score += ratio * 0.4
        reasons.append(f"fuzzy_doc_name:{ratio:.2f}")

    for alias in entry.aliases:
        alias_norm = normalize_catalog_text(alias)
        if not alias_norm:
            continue
        if alias_norm in query_norm or query_norm in alias_norm:
            score += 0.35
            reasons.append(f"alias_match:{alias}")
            break
        overlap = _alias_overlap_score(query_norm, alias_norm)
        if overlap >= 0.7:
            score += overlap * 0.6
            reasons.append(f"alias_token_overlap:{alias}")
            break
        alias_ratio = SequenceMatcher(None, query_norm, alias_norm).ratio()
        if alias_ratio >= 0.72:
            score += alias_ratio * 0.25
            reasons.append(f"fuzzy_alias:{alias}")

    query_numbers = extract_order_numbers(query_norm)
    for number in query_numbers:
        if number in entry.order_numbers:
            score += 0.5
            reasons.append(f"order_number:{number}")

    for alias_key, phrases in KNOWN_QUERY_ALIASES.items():
        if alias_key in query_norm and any(phrase in doc_norm for phrase in phrases):
            score += 0.55
            reasons.append(f"known_alias:{alias_key}")

    return score, reasons


def find_catalog_candidates(
    query: str,
    catalog: DocumentCatalog,
    *,
    explicit_doc_name: str = "",
    limit: int = 8,
) -> list[DocumentCandidate]:
    if explicit_doc_name.strip():
        explicit = explicit_doc_name.strip()
        for entry in catalog.entries:
            if entry.doc_name == explicit or explicit.upper() in entry.doc_name.upper():
                return [
                    DocumentCandidate(
                        catalog_id=entry.catalog_id,
                        doc_name=entry.doc_name,
                        score=1.0,
                        reasons=("explicit_doc_name",),
                    )
                ]
        return [
            DocumentCandidate(
                catalog_id="explicit_unverified",
                doc_name=explicit,
                score=0.2,
                reasons=("explicit_doc_name_unverified",),
            )
        ]

    query_norm = normalize_catalog_text(query)
    ranked: list[DocumentCandidate] = []
    for entry in catalog.entries:
        score, reasons = _score_entry(query_norm, entry)
        if score <= 0:
            continue
        ranked.append(
            DocumentCandidate(
                catalog_id=entry.catalog_id,
                doc_name=entry.doc_name,
                score=score,
                reasons=tuple(reasons),
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def load_default_document_catalog(paths: ProjectPaths | None = None) -> DocumentCatalog:
    project_paths = paths or ProjectPaths.from_root(None)
    return load_document_catalog(
        chunks_path=project_paths.chunks_json,
        snapshot_path=project_paths.output_dir / "document_catalog.json",
    )
