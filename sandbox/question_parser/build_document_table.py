"""Собрать одну таблицу документов корпуса.

Формат строки повторяет RegistryEntry из mr_norm
(`src/mr_norm/data/normative_registry.py`, schema_version 1):
registry_key, doc_type, authority, doc_number, adoption_date,
adoption_date_display, title, short_title.

Один файл корпуса — одна строка. Совпавший stem берёт registry_key как id.
Каждая глава ПУЭ получает свой id `pue_<глава>` и общий утверждающий акт
канонической записи ПУЭ. Короткие имена — из document_alias_registry.json:
сначала номер акта, иначе полное название и имя файла.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymorphy3

STOP = {
    "о", "об", "обо", "в", "во", "на", "по", "при", "и", "от", "для", "или",
    "с", "со", "к", "ко", "из", "а", "не", "до", "за", "над", "под", "что", "это", "как",
}
WORD_RE = re.compile(r"[0-9a-zа-яё-]+", re.IGNORECASE)
CHAPTER_RE = re.compile(r"глава\s+(\d+)\.(\d+)", re.IGNORECASE)
MORPH = pymorphy3.MorphAnalyzer()


def fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", fold(text), flags=re.UNICODE)).strip()


def compact_number(value: str) -> str:
    return re.sub(r"[\s\-]", "", fold(value))


def number_matches(act: str, doc_number: str) -> bool:
    """«35» совпадает с «35-ФЗ», «354» с «35» не совпадает."""
    left = compact_number(act)
    right = compact_number(doc_number)
    if not left or not right:
        return False
    if left == right:
        return True
    if left.isdigit() and right.startswith(left) and not right[len(left):len(left) + 1].isdigit():
        return True
    if right.isdigit() and left.startswith(right) and not left[len(right):len(right) + 1].isdigit():
        return True
    return False


def lemmas(text: str) -> set[str]:
    found = set()
    for word in WORD_RE.findall(fold(text)):
        if word in STOP or word.isdigit():
            continue
        found.add(MORPH.parse(word)[0].normal_form.replace("ё", "е"))
    return found


def content_lemmas(text: str) -> list[str]:
    found = []
    for word in WORD_RE.findall(fold(text)):
        if word in STOP or len(word) < 3:
            continue
        found.append(MORPH.parse(word)[0].normal_form.replace("ё", "е"))
    return found


def kind_matches(kinds: list[str], doc_type: str) -> bool:
    if not kinds:
        return True
    dtype = fold(doc_type)
    for kind in kinds:
        token = fold(kind)
        if "постанов" in token and "постанов" in dtype:
            return True
        if "приказ" in token and "приказ" in dtype:
            return True
        if "распоряж" in token and "распоряж" in dtype:
            return True
        if ("фз" == token or "закон" in token) and "закон" in dtype:
            return True
        if "гост" in token and "гост" in dtype:
            return True
    return False


def chapter_id(filename: str) -> str | None:
    match = CHAPTER_RE.search(filename)
    if not match:
        return None
    return f"pue_{match.group(1)}_{match.group(2)}"


def short_if_distinct(short_title: str, title: str) -> str:
    if normalize(short_title) == normalize(title):
        return ""
    return short_title.strip()


def row_blob(row: dict) -> str:
    # Полный title часто цитирует чужой акт. Короткое имя цепляем к имени файла и short_title.
    return " ".join([row.get("short_title") or "", row.get("filename") or ""])


def alias_hits(alias: str, rows: list[dict]) -> list[dict]:
    phrase = normalize(alias)
    if phrase in {"пуэ"}:
        return [row for row in rows if "пуэ" in normalize(row_blob(row))]
    hits = []
    wanted = content_lemmas(alias)
    for row in rows:
        blob = normalize(row_blob(row))
        if phrase and len(phrase) >= 6 and phrase in blob:
            hits.append(row)
            continue
        if len(wanted) >= 2 and set(wanted) <= lemmas(row_blob(row)):
            hits.append(row)
    return hits


def attach_aliases(rows: list[dict], entries: list[dict]) -> list[str]:
    unmatched = []
    for entry in entries:
        aliases = [alias.strip() for alias in entry.get("aliases") or [] if alias.strip()]
        if not aliases:
            continue
        numbers = [compact_number(number) for number in entry.get("act_numbers") or [] if str(number).strip()]
        kinds = list(entry.get("act_kinds") or [])
        if numbers:
            pool = [
                row for row in rows
                if any(number_matches(number, row.get("doc_number") or "") for number in entry.get("act_numbers") or []) and kind_matches(kinds, row.get("doc_type") or "")
            ]
            if len(pool) > 1:
                narrowed = []
                for row in pool:
                    if any(alias_hits(alias, [row]) for alias in aliases):
                        narrowed.append(row)
                pool = narrowed or pool
        else:
            pool = []
            seen = set()
            for alias in aliases:
                for row in alias_hits(alias, rows):
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        pool.append(row)
            if len(pool) > 8 and not any(normalize(alias) == "пуэ" for alias in aliases):
                unmatched.append(", ".join(aliases))
                continue
        if not pool:
            unmatched.append(", ".join(aliases))
            continue
        for row in pool:
            have = {fold(item) for item in row["aliases"]}
            for alias in aliases:
                if fold(alias) not in have:
                    row["aliases"].append(alias)
                    have.add(fold(alias))
    return unmatched


def build_rows(registry_documents: list[dict], filenames: list[str]) -> list[dict]:
    by_stem = {}
    pue = None
    for document in registry_documents:
        if document.get("is_pue_canonical"):
            pue = document
            continue
        for stem in document.get("match_stems") or []:
            by_stem[fold(stem)] = document
    if pue is None:
        raise SystemExit("registry has no canonical PUE entry")
    rows = []
    for filename in sorted(filenames):
        stem = Path(filename).stem
        document = by_stem.get(fold(stem))
        if document is not None:
            rows.append(
                {
                    "id": document["registry_key"],
                    "registry_key": document["registry_key"],
                    "doc_type": document.get("doc_type") or "",
                    "authority": document.get("authority") or "",
                    "doc_number": document.get("doc_number") or "",
                    "adoption_date": document.get("adoption_date") or "",
                    "adoption_date_display": document.get("adoption_date_display") or "",
                    "short_title": short_if_distinct(document.get("short_title") or "", document.get("title") or ""),
                    "title": document.get("title") or stem,
                    "filename": filename,
                    "aliases": [],
                }
            )
            continue
        file_id = chapter_id(filename)
        if file_id is None:
            raise SystemExit(f"corpus file is outside the registry and is not a PUE chapter: {filename}")
        rows.append(
            {
                "id": file_id,
                "registry_key": pue["registry_key"],
                "doc_type": pue.get("doc_type") or "",
                "authority": pue.get("authority") or "",
                "doc_number": pue.get("doc_number") or "",
                "adoption_date": pue.get("adoption_date") or "",
                "adoption_date_display": pue.get("adoption_date_display") or "",
                "short_title": f"ПУЭ, глава {file_id.removeprefix('pue_').replace('_', '.')}",
                "title": stem,
                "filename": filename,
                "aliases": [],
            }
        )
    return rows


def build_table(registry: dict, filenames: list[str], alias_entries: list[dict]) -> dict:
    rows = build_rows(registry["documents"], filenames)
    unmatched = attach_aliases(rows, alias_entries)
    rows.sort(key=lambda row: row["id"])
    return {
        "schema": "normative_documents.v1",
        "mr_norm_registry_schema_version": registry.get("schema_version"),
        "documents": rows,
        "unmatched_aliases": unmatched,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    filenames = sorted(path.name for path in args.corpus.glob("*.rtf"))
    alias_entries = json.loads(args.aliases.read_text(encoding="utf-8"))["entries"]
    table = build_table(registry, filenames, alias_entries)
    args.out.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"documents {len(table['documents'])}")
    print(f"unmatched {len(table['unmatched_aliases'])}")


if __name__ == "__main__":
    main()
