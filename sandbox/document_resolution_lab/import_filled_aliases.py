from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORM = Path(__file__).with_name("uncovered_documents_alias_form.md")
REGISTRY = ROOT / "src" / "mr_norm" / "config" / "document_alias_registry.json"
REPORT = Path(__file__).with_name("imported_aliases_20260913.md")


def main() -> None:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entries = {row["doc_id"]: row for row in data["entries"] if row.get("doc_id")}
    imported: list[tuple[str, str, list[str]]] = []
    skipped = 0

    for line in FORM.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "Имя файла" in line:
            continue
        fields = line.split("|", 8)
        if len(fields) < 8:
            continue
        doc_id = fields[5].strip().strip("`")
        supplied = fields[8].strip().rstrip("|").strip()
        if not doc_id or supplied == "новый" or not supplied:
            skipped += 1
            continue
        aliases = [item.strip() for item in supplied.split(",") if item.strip()]
        entry = entries.get(doc_id)
        if entry is None:
            entry = {"doc_id": doc_id, "aliases": [], "act_numbers": [], "act_kinds": []}
            data["entries"].append(entry)
            entries[doc_id] = entry
        current = list(entry.get("aliases") or [])
        for alias in aliases:
            if alias not in current:
                current.append(alias)
        entry["aliases"] = current
        imported.append((doc_id, fields[3].strip(), aliases))

    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# Импорт коротких названий", "", f"Импортировано документов: **{len(imported)}**", f"Пропущено строк: **{skipped}**", ""]
    report.append("| doc_id | Документ | Добавленные названия |")
    report.append("|---|---|---|")
    report.extend(f"| `{doc_id}` | {name} | {', '.join(aliases)} |" for doc_id, name, aliases in imported)
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"imported={len(imported)} skipped={skipped}")


if __name__ == "__main__":
    main()
