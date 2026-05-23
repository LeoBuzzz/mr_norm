"""Синхронизация input/*.rtf с output/marked_docs: удаление хвостов, пропуск уже обработанных."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mr_norm.data.normative_registry import normalize_match_stem
from mr_norm.tools.schema import StructuredDocument


@dataclass
class MarkedOutputGroup:
    key: str
    txt: Path | None = None
    structured: Path | None = None


@dataclass
class MarkedDocsSyncReport:
    input_rtf_count: int
    output_groups_before: int
    orphans_removed: list[str] = field(default_factory=list)
    already_complete_count: int = 0

    def to_dict(self) -> dict:
        return {
            "input_rtf_count": self.input_rtf_count,
            "output_groups_before": self.output_groups_before,
            "orphans_removed_count": len(self.orphans_removed),
            "orphans_removed": self.orphans_removed,
            "already_complete_count": self.already_complete_count,
        }


def collect_input_rtf_keys(input_dir: Path) -> dict[str, Path]:
    """normalized stem -> путь RTF."""
    out: dict[str, Path] = {}
    if not input_dir.is_dir():
        return out
    for path in sorted(input_dir.rglob("*.rtf")):
        if path.name.startswith("~$"):
            continue
        key = normalize_match_stem(path.name)
        if key:
            out.setdefault(key, path)
    return out


def _structured_path_to_key(path: Path) -> str:
    name = path.name
    if name.endswith(".structured.json"):
        stem = name[: -len(".structured.json")]
        return normalize_match_stem(stem + ".txt")
    return normalize_match_stem(name)


def collect_marked_output_groups(marked_docs_dir: Path) -> dict[str, MarkedOutputGroup]:
    """normalized stem (как у RTF) -> txt / structured в marked_docs."""
    groups: dict[str, MarkedOutputGroup] = {}
    if not marked_docs_dir.is_dir():
        return groups

    for path in sorted(marked_docs_dir.glob("*.structured.json")):
        key = _structured_path_to_key(path)
        if not key:
            continue
        group = groups.setdefault(key, MarkedOutputGroup(key=key))
        group.structured = path
        data: dict = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
            fn = str(data.get("filename") or "").strip()
            if fn:
                key_from_fn = normalize_match_stem(fn)
                if key_from_fn and key_from_fn != key:
                    old = groups.pop(key, group)
                    old.key = key_from_fn
                    group = groups.setdefault(key_from_fn, old)
                    group.structured = path
                    key = key_from_fn
        except (OSError, json.JSONDecodeError):
            pass
        txt_name = str(data.get("filename") or f"{path.name[: -len('.structured.json')]}.txt")
        txt_candidate = marked_docs_dir / Path(txt_name).name
        if txt_candidate.is_file():
            group.txt = txt_candidate

    for path in sorted(marked_docs_dir.glob("*.txt")):
        key = normalize_match_stem(path.name)
        if not key:
            continue
        group = groups.setdefault(key, MarkedOutputGroup(key=key))
        if group.txt is None:
            group.txt = path

    return groups


def is_marked_group_complete(group: MarkedOutputGroup) -> bool:
    if group.structured is None or not group.structured.is_file():
        return False
    if group.txt is None or not group.txt.is_file():
        return False
    try:
        doc = StructuredDocument.from_json_path(group.structured)
    except (OSError, json.JSONDecodeError):
        return False
    if doc.read_error or not doc.paragraphs:
        return False
    return True


def prune_orphan_marked_outputs(input_dir: Path, marked_docs_dir: Path) -> list[str]:
    """Удалить output без соответствующего RTF в input (по normalized stem)."""
    input_keys = set(collect_input_rtf_keys(input_dir))
    removed: list[str] = []
    for key, group in collect_marked_output_groups(marked_docs_dir).items():
        if key in input_keys:
            continue
        for path in (group.txt, group.structured):
            if path and path.is_file():
                path.unlink()
                removed.append(str(path))
    return removed


def sync_marked_docs_with_input(input_dir: Path, marked_docs_dir: Path) -> MarkedDocsSyncReport:
    input_map = collect_input_rtf_keys(input_dir)
    groups = collect_marked_output_groups(marked_docs_dir)
    removed = prune_orphan_marked_outputs(input_dir, marked_docs_dir)
    complete = sum(
        1 for key in input_map if key in groups and is_marked_group_complete(groups[key])
    )
    return MarkedDocsSyncReport(
        input_rtf_count=len(input_map),
        output_groups_before=len(groups),
        orphans_removed=removed,
        already_complete_count=complete,
    )


def find_complete_marked_group(rtf_path: Path, marked_docs_dir: Path) -> MarkedOutputGroup | None:
    key = normalize_match_stem(rtf_path.name)
    if not key:
        return None
    group = collect_marked_output_groups(marked_docs_dir).get(key)
    if group and is_marked_group_complete(group):
        return group
    return None
