from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.config.pue_aliases import active_known_query_aliases
from mr_norm.tools.chunker import load_chunks

ORDER_NUMBER_PATTERNS = (
    re.compile(r"(?:приказ|постановление|распоряжение).*?(?:№|n\.?|номер)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:№|n\.?)\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bn[_\s]*(\d+)\b", re.IGNORECASE),
)
POINT_HINT_PATTERNS = (
    re.compile(
        r"(?:подпункт\w*|пункт\w*|п\.?\s*)\s*(\d+(?:[._]\d+)*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:п\.?\s*|пункт\s+)(\d+)\b",
        re.IGNORECASE,
    ),
)
KNOWN_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "пуэ": ("правила устройства электроустановок", "электроустановок"),
    "птэ": ("правила технической эксплуатации", "технической эксплуатации"),
    "озп": ("отопительный сезон", "готовности"),
}
PARTIAL_ORDER_HINT_PATTERNS = (
    re.compile(r"минэнерг\w*\s*(?:№|n\s*)?(\d+)", re.IGNORECASE),
    re.compile(r"приказ\w*(?:\s+\w+){0,6}(\d+)", re.IGNORECASE),
)
ENERGY_SECTOR_QUERY_MARKERS = (
    "минэнерго",
    "минэнерг",
    "электроэнергет",
    "электроустанов",
    "птэ",
    "пуэ",
    "диспетчер",
    "лэп",
    "подстанц",
    "генерирующ",
    "сетев",
    "потребител",
    "энергосистем",
)


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


def is_generic_tech_reg_doc_name(doc_name: str) -> bool:
    norm = normalize_catalog_text(doc_name)
    if norm == "о техническом регулировании":
        return True
    return "техническ" in norm and "регулирован" in norm and len(norm.split()) <= 5


def query_suggests_energy_sector(query: str) -> bool:
    norm = normalize_catalog_text(query)
    return any(marker in norm for marker in ENERGY_SECTOR_QUERY_MARKERS)


def extract_partial_order_hints(query: str) -> list[str]:
    norm = normalize_catalog_text(query)
    if not re.search(r"минэнерг", norm):
        return []
    numbers: list[str] = []
    for pattern in PARTIAL_ORDER_HINT_PATTERNS:
        for match in pattern.finditer(query or ""):
            value = (match.group(1) or "").strip()
            if value and value not in numbers:
                numbers.append(value)
    return numbers


def resolve_by_partial_order_hint(
    query: str,
    catalog: DocumentCatalog,
) -> tuple[list[str], str, float, bool, list[str]] | None:
    hints = extract_partial_order_hints(query)
    if not hints:
        return None

    matched: dict[str, DocumentCatalogEntry] = {}
    for number in hints:
        for entry in catalog.entries:
            if number in entry.order_numbers:
                matched[entry.catalog_id] = entry

    if not matched:
        return None

    unique = list(matched.values())
    if len(unique) == 1:
        entry = unique[0]
        return (
            [entry.doc_name],
            entry.catalog_id,
            0.88,
            False,
            [f"partial_order:{hints[0]}"],
        )

    norm = normalize_catalog_text(query)
    if re.search(r"минэнерг", norm):
        ministry = [
            entry
            for entry in unique
            if "минэнерг" in normalize_catalog_text(entry.doc_name)
            or "утвержден" in normalize_catalog_text(entry.doc_name)
        ]
        if len(ministry) == 1:
            entry = ministry[0]
            return (
                [entry.doc_name],
                entry.catalog_id,
                0.85,
                False,
                [f"partial_order:{hints[0]}"],
            )
    return None


def extract_order_numbers(*texts: str) -> list[str]:
    numbers: list[str] = []
    for text in texts:
        for pattern in ORDER_NUMBER_PATTERNS:
            for match in pattern.finditer(text or ""):
                number = match.group(1).strip()
                if number and number not in numbers:
                    numbers.append(number)
    return numbers


def _looks_like_calendar_date(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 3:
        return False
    try:
        day, month, year = (int(part) for part in parts)
    except ValueError:
        return False
    return 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2099


def extract_point_number_hint(query: str) -> str:
    text = query or ""
    for pattern in POINT_HINT_PATTERNS:
        for match in pattern.finditer(text):
            value = (match.group(1) or "").strip()
            if value and not _looks_like_calendar_date(value):
                return value
    return ""


def _acronym_from_doc_name(doc_name: str) -> str:
    words = [word for word in re.findall(r"[A-Za-zА-Яа-я0-9]+", doc_name) if len(word) > 2]
    if len(words) < 2:
        return ""
    return "".join(word[0] for word in words[:6]).lower()


CATALOG_SCHEMA_VERSION = "mr_document_catalog_v2"


@dataclass(frozen=True)
class DocumentCatalogEntry:
    catalog_id: str
    doc_name: str
    doc_id: str = ""
    aliases: tuple[str, ...] = ()
    order_numbers: tuple[str, ...] = ()
    registry_key: str = ""

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

    def by_doc_id(self) -> dict[str, DocumentCatalogEntry]:
        return {entry.doc_id: entry for entry in self.entries if entry.doc_id}


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


def _build_entry_aliases(doc_name: str) -> tuple[str, ...]:
    aliases: list[str] = []
    normalized = normalize_catalog_text(doc_name)
    if normalized:
        aliases.append(normalized)
    acronym = _acronym_from_doc_name(doc_name)
    if acronym and acronym not in aliases:
        aliases.append(acronym)
    for number in extract_order_numbers(doc_name):
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

    by_doc_id: dict[str, DocumentCatalogEntry] = {}
    for chunk in load_chunks(chunks_path):
        payload = chunk.get("payload") or chunk
        doc_id = str(payload.get("doc_id") or "").strip()
        doc_name = str(payload.get("doc_name") or "").strip()
        if not doc_id or not doc_name:
            continue
        if doc_id in by_doc_id:
            continue
        registry_key = str(payload.get("registry_key") or "").strip()
        order_numbers = tuple(extract_order_numbers(doc_name))
        by_doc_id[doc_id] = DocumentCatalogEntry(
            catalog_id=doc_id,
            doc_name=doc_name,
            doc_id=doc_id,
            aliases=_build_entry_aliases(doc_name),
            order_numbers=order_numbers,
            registry_key=registry_key,
        )
    entries = sorted(by_doc_id.values(), key=lambda item: item.doc_name)
    return DocumentCatalog(entries=entries, source_path=str(chunks_path))


def save_catalog_snapshot(catalog: DocumentCatalog, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
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
            catalog_id=str(item.get("catalog_id") or item.get("doc_id") or ""),
            doc_name=str(item.get("doc_name") or ""),
            doc_id=str(item.get("doc_id") or item.get("catalog_id") or ""),
            aliases=tuple(item.get("aliases") or ()),
            order_numbers=tuple(str(number) for number in item.get("order_numbers") or ()),
            registry_key=str(item.get("registry_key") or ""),
        )
        for item in payload.get("entries") or []
        if item.get("doc_name")
    ]
    return DocumentCatalog(entries=entries, source_path=str(payload.get("source_path") or path))


def _catalog_snapshot_is_current(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("schema_version") == CATALOG_SCHEMA_VERSION


def load_document_catalog(
    *,
    chunks_path: Path,
    snapshot_path: Path | None = None,
    refresh: bool = False,
) -> DocumentCatalog:
    snapshot = snapshot_path or chunks_path.parent / "document_catalog.json"
    if snapshot.is_file() and not refresh and _catalog_snapshot_is_current(snapshot):
        catalog = load_catalog_snapshot(snapshot)
        if catalog.entries:
            return catalog
    catalog = build_catalog_from_chunks(chunks_path)
    if catalog.entries:
        save_catalog_snapshot(catalog, snapshot)
    return catalog


def _score_entry(
    query_norm: str,
    entry: DocumentCatalogEntry,
    *,
    enable_pue_aliases: bool = False,
) -> tuple[float, list[str]]:
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

    for alias_key, phrases in active_known_query_aliases(enable_pue_aliases=enable_pue_aliases).items():
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
    enable_pue_aliases: bool = False,
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
        score, reasons = _score_entry(query_norm, entry, enable_pue_aliases=enable_pue_aliases)
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
