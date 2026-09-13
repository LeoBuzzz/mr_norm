from __future__ import annotations

import json
from pathlib import Path

from canonical_resolve import resolve_canonical


ROOT = Path(__file__).resolve().parent
cases = json.loads((ROOT / "document_resolution_cases.json").read_text(encoding="utf-8"))["cases"]
rows = []
for case in cases:
    result = resolve_canonical(case["query"])
    expected = case.get("doc_id", "")
    ok = result.get("doc_id", "") == expected if case.get("status") != "catalog_gap" else result["status"] == "catalog_gap"
    rows.append({"query": case["query"], "expected": expected, "result": result, "pass": ok})
print(json.dumps(rows, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(row["pass"] for row in rows) else 1)
