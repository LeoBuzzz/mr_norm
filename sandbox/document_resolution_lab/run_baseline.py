"""Run the agreed document-resolution cases against the current implementation."""

from __future__ import annotations

import json
from pathlib import Path

from mr_norm.skills.document_resolve import resolve_document_from_label


ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("document_resolution_cases.json")


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    for case in data["cases"]:
        result = resolve_document_from_label(case["query"])
        expected = case.get("doc_id", "")
        rows.append(
            {
                "query": case["query"],
                "expected_doc_id": expected,
                "actual_doc_id": result.doc_id,
                "status": result.status,
                "confidence": result.confidence,
                "pass": bool(expected and result.doc_id == expected)
                if case.get("status") != "catalog_gap"
                else True,
                "warnings": list(result.warnings),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(row["pass"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
