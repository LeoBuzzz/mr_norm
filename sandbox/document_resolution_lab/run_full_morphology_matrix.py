from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mr_norm.retrieval.document_catalog import extract_point_number_hints
from mr_norm.skills.document_resolve import resolve_document_from_label
from mr_norm.skills.document_scope import resolve_document_scope


REPORT_DIR = Path(__file__).with_name("reports")
REPORT_JSON = REPORT_DIR / "full_morphology_matrix_20260913.json"
REPORT_MD = REPORT_DIR / "full_morphology_matrix_20260913.md"
GOST_DOC_ID = "doc_4745dec28ca589e1"


def _sentence_variants() -> list[str]:
    stems = (
        "дай",
        "выдай",
        "найди",
        "покажи",
        "приведи",
        "что указано",
        "что содержится",
        "найди сведения",
        "проверь",
    )
    document_forms = (
        "ГОСТ по терминам",
        "ГОСТа по терминам",
        "ГОСТу по терминам",
        "ГОСТом по терминам",
        "ГОСТе по терминам",
    )
    point_forms = ("пункт", "пункта", "пункте", "пунктом", "п.")
    prepositions = ("из", "в", "по", "для", "согласно", "с")
    variants: list[str] = []
    for stem in stems:
        for prep in prepositions:
            for document in document_forms:
                for point in point_forms:
                    variants.append(f"{stem} {point} 22 {prep} {document}")
                    variants.append(f". {stem} {point} 22 {prep} {document}")
    variants.extend(
        [
            "ГОСТ по терминам, пункт 22",
            "из ГОСТа по терминам п.22",
            "в ГОСТе по терминам п. 22",
            "согласно ГОСТу по терминам, пунктом 22",
            "ГОСТа по терминам — пункт 22",
            "ГОСТ Р 57114-2022 пункт 22",
            "пункт 22 ГОСТ Р 57114",
            ". дай пункт 22 из ГОСТа по терминам",
        ]
    )
    return list(dict.fromkeys(variants))


def _act_number_matrix() -> list[tuple[str, str]]:
    return [
        ("постановление 937", "doc_6b2e4ec78884fb5f"),
        ("постановлением № 937", "doc_6b2e4ec78884fb5f"),
        ("приказ 690", "doc_aba6345406b47fba"),
        ("приказом № 690", "doc_aba6345406b47fba"),
        ("приказ от 29_11_2016 N 1256", "doc_e41ce9884ed5620b"),
        ("приказом от 29.11.2016 № 1256", "doc_e41ce9884ed5620b"),
        ("ФЗ 35", "doc_888d9d2a434ba4e8"),
        ("35-ФЗ", "doc_888d9d2a434ba4e8"),
        ("федеральном законе № 35-ФЗ", "doc_888d9d2a434ba4e8"),
        ("ГОСТ Р 1.4-2004", "doc_ee47fbd4b1175a52"),
        ("ГОСТ Р 58651.1-2019", "doc_ef18285b05ae80fc"),
    ]


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for query in _sentence_variants():
        scope = resolve_document_scope(query)
        rows.append(
            {
                "kind": "gost_terms_point",
                "query": query,
                "expected_doc_id": GOST_DOC_ID,
                "actual_doc_ids": list(scope.include_doc_ids),
                "point_hints": extract_point_number_hints(query),
                "ok": scope.include_doc_ids == (GOST_DOC_ID,)
                and "22" in extract_point_number_hints(query),
            }
        )

    for label, expected in _act_number_matrix():
        result = resolve_document_from_label(label)
        rows.append(
            {
                "kind": "act_number",
                "query": label,
                "expected_doc_id": expected,
                "actual_doc_id": result.doc_id,
                "ok": result.found and result.doc_id == expected,
            }
        )

    failures = [row for row in rows if not row["ok"]]
    summary = {
        "total": len(rows),
        "passed": len(rows) - len(failures),
        "failed": len(failures),
        "pass_rate": round((len(rows) - len(failures)) / len(rows), 4) if rows else 0.0,
        "gost_variants": len(_sentence_variants()),
        "act_number_cases": len(_act_number_matrix()),
        "failures": failures,
    }
    payload = {"summary": summary, "cases": rows}
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Полная матрица морфологии распознавания документов",
        "",
        f"Проверено: **{summary['total']}**, успешно: **{summary['passed']}**, "
        f"ошибок: **{summary['failed']}**, доля: **{summary['pass_rate']:.1%}**.",
        "",
        "Проверены падежи ГОСТ, предлоги, формы «пункт/пункта/пункте/пунктом/п.», "
        "точка в начале сообщения, номера актов, дефисы, N/№ и варианты ФЗ.",
        "",
    ]
    if failures:
        md.extend(["## Ошибки", ""])
        for row in failures:
            md.append(f"- `{row['query']}` → ожидалось `{row['expected_doc_id']}`, получено `{row}`")
    else:
        md.extend(["## Результат", "", "Все варианты матрицы распознаны корректно."])
    REPORT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
