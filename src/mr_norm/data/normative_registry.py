"""Реестр нормативных документов — источник правды для payload чанков."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from mr_norm.data.normative_registry_key import (
    build_doc_key,
    format_adoption_date_display,
    format_adoption_date_iso,
    parse_date_cell,
)
from mr_norm.tools.schema import StructuredDocument

REGISTRY_FILENAME = "normative_documents_registry.json"
SCHEMA_VERSION = 1
PUE_MARKER = "пуэ"


class RegistryMissingMode(str, Enum):
    FAIL = "fail"
    INTERACTIVE = "interactive"
    SKIP = "skip"


@dataclass(frozen=True)
class RegistryEntry:
    registry_key: str
    doc_type: str
    authority: str
    doc_number: str
    adoption_date: str
    adoption_date_display: str
    title: str
    short_title: str
    match_stems: tuple[str, ...] = ()
    is_pue_canonical: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistryEntry:
        stems = data.get("match_stems") or []
        if isinstance(stems, str):
            stems = [stems]
        return cls(
            registry_key=str(data.get("registry_key") or "").strip(),
            doc_type=str(data.get("doc_type") or "").strip(),
            authority=str(data.get("authority") or "").strip(),
            doc_number=str(data.get("doc_number") or "").strip(),
            adoption_date=str(data.get("adoption_date") or "").strip(),
            adoption_date_display=str(data.get("adoption_date_display") or "").strip(),
            title=str(data.get("title") or "").strip(),
            short_title=str(data.get("short_title") or "").strip(),
            match_stems=tuple(str(s).strip() for s in stems if str(s).strip()),
            is_pue_canonical=bool(data.get("is_pue_canonical")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "registry_key": self.registry_key,
            "doc_type": self.doc_type,
            "authority": self.authority,
            "doc_number": self.doc_number,
            "adoption_date": self.adoption_date,
            "adoption_date_display": self.adoption_date_display,
            "title": self.title,
            "short_title": self.short_title,
            "match_stems": list(self.match_stems),
        }
        if self.is_pue_canonical:
            out["is_pue_canonical"] = True
        return out


def default_registry_path() -> Path:
    return Path(__file__).resolve().parent / REGISTRY_FILENAME


def normalize_match_stem(name: str) -> str:
    """Ключ сопоставления: stem .rtf/.txt или целая строка без «ложного» расширения после точки в названии."""
    s = str(name or "").strip()
    if not s:
        return ""
    lower = s.casefold()
    if lower.endswith(".txt") or lower.endswith(".rtf"):
        return Path(s).stem.casefold()
    return lower


def filename_has_pue_marker(document: StructuredDocument) -> bool:
    for part in (document.filename, document.source_file):
        if part and PUE_MARKER in str(part).casefold():
            return True
    return False


def format_approving_act(entry: RegistryEntry) -> str:
    """Строка утверждения для doc_reg / approving_act."""
    dtype = entry.doc_type.casefold()
    auth = entry.authority
    num = entry.doc_number
    date_s = entry.adoption_date_display or entry.adoption_date
    if "федеральный" in dtype and "закон" in dtype:
        if num and date_s:
            return f"Федеральный закон от {date_s} № {num}"
        return entry.title or "Федеральный закон"
    if dtype == "постановление":
        if date_s and num:
            return f"Постановлением Правительства Российской Федерации от {date_s} № {num}"
    if dtype == "распоряжение":
        if date_s and num:
            return f"Распоряжением Правительства Российской Федерации от {date_s} № {num}"
    if dtype == "приказ" and auth and date_s and num:
        return f"Приказом {auth} от {date_s} № {num}"
    if dtype == "гост" and date_s and num:
        return f"Приказом {auth or 'Росстандарта'} от {date_s} № {num}-ст"
    if entry.title:
        return entry.title
    return f"{entry.doc_type} {num} от {date_s}".strip()


def merge_registry_into_chunk_metadata(
    meta: dict[str, str],
    entry: RegistryEntry,
    *,
    registry_source: str,
) -> dict[str, str]:
    """Подставляет поля реестра в metadata чанкера (копия dict)."""
    out = dict(meta)
    approving = format_approving_act(entry)
    out["doc_title_full"] = entry.title
    out["doc_name"] = entry.short_title or entry.title
    out["doc_reg"] = approving
    out["approving_act"] = approving
    out["authority"] = entry.authority
    out["doc_date"] = entry.adoption_date_display or entry.adoption_date
    out["metadata_source"] = f"registry:{registry_source}"
    out["registry_key"] = entry.registry_key
    out["short_title"] = entry.short_title
    return out


@dataclass
class NormativeRegistry:
    path: Path
    schema_version: int
    documents: list[RegistryEntry]
    _stem_index: dict[str, RegistryEntry] = field(default_factory=dict, init=False, repr=False)
    _pue_canonical: RegistryEntry | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, RegistryEntry] = {}
        pue: RegistryEntry | None = None
        for doc in self.documents:
            if doc.is_pue_canonical:
                pue = doc
                continue
            for stem in doc.match_stems:
                key = normalize_match_stem(stem)
                if key:
                    index[key] = doc
        self._stem_index = index
        self._pue_canonical = pue

    def pue_canonical_entry(self) -> RegistryEntry | None:
        return self._pue_canonical

    def lookup_by_structured_doc(self, document: StructuredDocument) -> RegistryEntry | None:
        for candidate in (document.filename, Path(document.source_file or "").name):
            key = normalize_match_stem(candidate)
            if key and key in self._stem_index:
                return self._stem_index[key]
        return None

    def lookup_by_stem(self, stem: str) -> RegistryEntry | None:
        key = normalize_match_stem(stem)
        return self._stem_index.get(key) if key else None

    def has_registry_key(self, registry_key: str) -> bool:
        return any(d.registry_key == registry_key for d in self.documents)

    def append_entry(self, entry: RegistryEntry) -> None:
        if self.has_registry_key(entry.registry_key):
            self.documents = [d for d in self.documents if d.registry_key != entry.registry_key]
        self.documents.append(entry)
        self.documents.sort(key=lambda d: d.registry_key)
        self._stem_index = {}
        self._pue_canonical = None
        self.__post_init__()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "documents": [d.to_dict() for d in self.documents],
        }

    def save_atomic(self) -> None:
        save_registry_atomic(self.path, self.to_payload())


_registry_cache: dict[Path, NormativeRegistry] = {}


def load_registry(path: Path | None = None, *, reload: bool = False) -> NormativeRegistry:
    resolved = (path or default_registry_path()).resolve()
    if not reload and resolved in _registry_cache:
        return _registry_cache[resolved]
    if not resolved.is_file():
        raise FileNotFoundError(f"Normative registry not found: {resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    docs = [RegistryEntry.from_dict(d) for d in (data.get("documents") or [])]
    reg = NormativeRegistry(
        path=resolved,
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        documents=docs,
    )
    _registry_cache[resolved] = reg
    return reg


def save_registry_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    _registry_cache.pop(path, None)


def _prompt_field(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


def append_document_interactive(
    registry: NormativeRegistry,
    *,
    filename_hint: str = "",
    source_hint: str = "",
) -> RegistryEntry:
    print(f"\nДокумент не найден в реестре: {filename_hint or source_hint}")
    doc_type = _prompt_field("Тип документа")
    authority = _prompt_field("Принявший орган")
    doc_number = _prompt_field("Номер документа")
    adoption_raw = _prompt_field("Дата принятия документа (дд.мм.гггг)")
    title = _prompt_field("Название документа")
    short_title = _prompt_field("Краткое название", default=title[:80])
    parsed = parse_date_cell(adoption_raw)
    registry_key = build_doc_key(doc_type, doc_number, adoption_raw)
    stems: list[str] = []
    if filename_hint:
        stems.append(Path(filename_hint).stem)
    entry = RegistryEntry(
        registry_key=registry_key,
        doc_type=doc_type,
        authority=authority,
        doc_number=doc_number,
        adoption_date=format_adoption_date_iso(parsed),
        adoption_date_display=format_adoption_date_display(parsed, adoption_raw),
        title=title,
        short_title=short_title,
        match_stems=tuple(stems),
    )
    registry.append_entry(entry)
    registry.save_atomic()
    print(f"Добавлено в реестр: {registry_key}")
    return entry


def append_document_from_fields(
    registry: NormativeRegistry,
    *,
    doc_type: str,
    authority: str,
    doc_number: str,
    adoption_date: str,
    title: str,
    short_title: str,
    match_stems: list[str] | None = None,
) -> RegistryEntry:
    parsed = parse_date_cell(adoption_date)
    entry = RegistryEntry(
        registry_key=build_doc_key(doc_type, doc_number, adoption_date),
        doc_type=doc_type.strip(),
        authority=authority.strip(),
        doc_number=str(doc_number).strip(),
        adoption_date=format_adoption_date_iso(parsed) or adoption_date.strip(),
        adoption_date_display=format_adoption_date_display(parsed, adoption_date),
        title=title.strip(),
        short_title=short_title.strip(),
        match_stems=tuple(match_stems or []),
    )
    registry.append_entry(entry)
    registry.save_atomic()
    return entry


@dataclass(frozen=True)
class FileCoverageRow:
    stem: str
    source: str
    status: str
    registry_key: str = ""


@dataclass
class RegistryCoverageReport:
    registry_path: str
    registry_document_count: int
    pue_canonical_key: str
    files_checked: int
    matched: list[FileCoverageRow]
    pue_canonical: list[FileCoverageRow]
    missing: list[FileCoverageRow]
    orphan_registry_stems: list[str]
    duplicate_stems_in_registry: list[dict[str, str]]

    @property
    def ok_for_chunking(self) -> bool:
        return not self.missing and not self.duplicate_stems_in_registry

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_path": self.registry_path,
            "registry_document_count": self.registry_document_count,
            "pue_canonical_key": self.pue_canonical_key,
            "files_checked": self.files_checked,
            "matched_count": len(self.matched),
            "pue_canonical_count": len(self.pue_canonical),
            "missing_count": len(self.missing),
            "orphan_registry_stems_count": len(self.orphan_registry_stems),
            "ok_for_chunking": self.ok_for_chunking,
            "matched": [asdict(r) for r in self.matched],
            "pue_canonical": [asdict(r) for r in self.pue_canonical],
            "missing": [asdict(r) for r in self.missing],
            "orphan_registry_stems": self.orphan_registry_stems,
            "duplicate_stems_in_registry": self.duplicate_stems_in_registry,
        }


def _stem_is_pue(stem_or_name: str) -> bool:
    return PUE_MARKER in str(stem_or_name or "").casefold()


def collect_stems_from_structured_paths(structured_paths: list[Path]) -> dict[str, str]:
    """stem из поля filename в structured JSON (как при чанковании)."""
    out: dict[str, str] = {}
    for path in structured_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            key = normalize_match_stem(path.name)
            if key:
                out.setdefault(key, f"structured:{path.name}")
            continue
        fn = str(data.get("filename") or path.stem + ".txt")
        key = normalize_match_stem(fn)
        if key:
            out.setdefault(key, fn)
    return out


def collect_file_stems_from_input(input_dir: Path) -> dict[str, str]:
    """stem (normalized) -> путь RTF в input (источник правды для сверки с реестром)."""
    out: dict[str, str] = {}
    if not input_dir.is_dir():
        return out
    for path in sorted(input_dir.rglob("*.rtf")):
        if path.name.startswith("~$"):
            continue
        key = normalize_match_stem(path.name)
        if key:
            out.setdefault(key, f"rtf:{path.as_posix()}")
    return out


def audit_registry_coverage(
    registry: NormativeRegistry,
    file_stems: dict[str, str],
) -> RegistryCoverageReport:
    """Сверка stem файлов на диске с match_stems реестра (ПУЭ — через is_pue_canonical)."""
    stem_to_entry: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    for doc in registry.documents:
        if doc.is_pue_canonical:
            continue
        for stem in doc.match_stems:
            key = normalize_match_stem(stem)
            if not key:
                continue
            if key in stem_to_entry and stem_to_entry[key] != doc.registry_key:
                duplicates.append(
                    {
                        "stem": stem,
                        "registry_key_a": stem_to_entry[key],
                        "registry_key_b": doc.registry_key,
                    }
                )
            stem_to_entry[key] = doc.registry_key

    pue_key = registry.pue_canonical_entry().registry_key if registry.pue_canonical_entry() else ""

    matched: list[FileCoverageRow] = []
    pue_rows: list[FileCoverageRow] = []
    missing: list[FileCoverageRow] = []

    for stem, source in sorted(file_stems.items()):
        if _stem_is_pue(stem):
            pue_rows.append(
                FileCoverageRow(
                    stem=stem,
                    source=source,
                    status="pue_canonical",
                    registry_key=pue_key,
                )
            )
            continue
        reg_key = registry.lookup_by_stem(stem)
        if reg_key:
            matched.append(
                FileCoverageRow(
                    stem=stem,
                    source=source,
                    status="matched",
                    registry_key=reg_key.registry_key,
                )
            )
        else:
            missing.append(FileCoverageRow(stem=stem, source=source, status="missing"))

    file_stem_set = set(file_stems)
    orphan = sorted(
        stem
        for stem in stem_to_entry
        if stem not in file_stem_set and not _stem_is_pue(stem)
    )

    return RegistryCoverageReport(
        registry_path=str(registry.path),
        registry_document_count=len(registry.documents),
        pue_canonical_key=pue_key,
        files_checked=len(file_stems),
        matched=matched,
        pue_canonical=pue_rows,
        missing=missing,
        orphan_registry_stems=orphan,
        duplicate_stems_in_registry=duplicates,
    )


def check_registry_coverage(
    *,
    registry_path: Path | None = None,
    input_dir: Path | None = None,
    structured_paths: list[Path] | None = None,
) -> RegistryCoverageReport:
    from mr_norm.config.paths import ProjectPaths

    paths = ProjectPaths.from_root()
    reg = load_registry(registry_path or paths.normative_registry_json, reload=True)
    if structured_paths is not None:
        stems = collect_stems_from_structured_paths(structured_paths)
    else:
        stems = collect_file_stems_from_input(input_dir or paths.input_dir)
    return audit_registry_coverage(reg, stems)


class RegistryCoverageError(Exception):
    """Файлы на диске не покрыты match_stems реестра (до чанкования)."""


class RegistryLookupError(Exception):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "Документы отсутствуют в normative_documents_registry.json:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


def resolve_metadata_with_registry(
    document: StructuredDocument,
    meta: dict[str, str],
    registry: NormativeRegistry,
    *,
    missing_mode: RegistryMissingMode,
    on_missing_interactive: Callable[[StructuredDocument, dict[str, str]], RegistryEntry | None]
    | None = None,
) -> dict[str, str]:
    """
    ПУЭ в имени файла — реквизиты акта из записи is_pue_canonical в JSON (главы — свои doc_id/структура).
    Иначе lookup по stem; при hit — merge; при miss — fail / interactive / skip.
    """
    if filename_has_pue_marker(document):
        pue_entry = registry.pue_canonical_entry()
        if pue_entry is not None:
            return merge_registry_into_chunk_metadata(
                meta, pue_entry, registry_source=registry.path.name
            )
        if missing_mode == RegistryMissingMode.SKIP:
            return meta
        raise RegistryLookupError(
            [document.filename or document.source_file or "?", "нет is_pue_canonical в реестре"]
        )
    entry = registry.lookup_by_structured_doc(document)
    if entry is not None:
        return merge_registry_into_chunk_metadata(
            meta, entry, registry_source=registry.path.name
        )
    if missing_mode == RegistryMissingMode.SKIP:
        return meta
    if missing_mode == RegistryMissingMode.INTERACTIVE:
        if on_missing_interactive is not None:
            added = on_missing_interactive(document, meta)
        else:
            added = append_document_interactive(
                registry,
                filename_hint=document.filename,
                source_hint=document.source_file,
            )
        if added is not None:
            return merge_registry_into_chunk_metadata(
                meta, added, registry_source=registry.path.name
            )
    raise RegistryLookupError([document.filename or document.source_file or "?"])
