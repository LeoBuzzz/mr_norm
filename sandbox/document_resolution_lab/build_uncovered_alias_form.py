from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "output" / "document_catalog.json"
REGISTRY = ROOT / "src" / "mr_norm" / "data" / "normative_documents_registry.json"
CANONICAL = ROOT / "src" / "mr_norm" / "config" / "document_alias_registry.json"
OUT = Path(__file__).with_name("uncovered_documents_alias_form.md")


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["entries"]
    registry = {row["registry_key"]: row for row in json.loads(REGISTRY.read_text(encoding="utf-8"))["documents"]}
    canonical_entries = json.loads(CANONICAL.read_text(encoding="utf-8"))["entries"]
    covered = {row["doc_id"] for row in canonical_entries if row.get("doc_id")}

    # Preserve manually entered values from the previous form. The last column
    # is intentionally treated as the agreed short name, per user convention.
    manual: dict[str, str] = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|") or line.startswith("|---"):
                continue
            fields = line.split("|", 8)
            if len(fields) >= 9:
                doc_id = fields[5].strip().strip("`")
                value = fields[8].strip().rstrip("|").strip()
                if doc_id and value and value not in {"новый", "РЅРѕРІС‹Р№"}:
                    manual[doc_id] = value

    rows: list[str] = []
    for entry in catalog:
        reg = registry.get(entry.get("registry_key"), {})
        stems = reg.get("match_stems") or []
        filename = " / ".join(stems) if isinstance(stems, list) else str(stems)
        act = ""
        if reg.get("doc_type") or reg.get("doc_number"):
            act = f"{reg.get('doc_type', '')} № {reg.get('doc_number', '')}".strip()
        canonical = next((row for row in canonical_entries if row.get("doc_id") == entry["doc_id"]), {})
        known = ", ".join(str(alias) for alias in canonical.get("aliases", []))
        supplied = manual.get(entry["doc_id"], "")
        short = supplied or known
        status = "заполнено" if supplied else ("уже задано" if entry["doc_id"] in covered else "новый")
        rows.append(f"| {len(rows) + 1} | `{filename}` | {entry['doc_name']} | {act} | `{entry['doc_id']}` | {short} |  | {status} |")

    text = """# Полная форма коротких названий документов

В форме перечислены все документы каталога. Уже известные aliases заполнены автоматически. Новое короткое название можно вписывать в последнюю колонку вместо `новый`. Если документ не нужно обозначать сокращённо, оставьте `новый`.

| № | Имя файла / stem | Полное название документа | Вид и номер акта | doc_id | Известные / согласованные короткие названия | Варианты написания | Статус |
|---:|---|---|---|---|---|---|---|
""" + "\n".join(rows) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} documents)")


if __name__ == "__main__":
    main()
