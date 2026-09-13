from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mr_norm.config.indexing import IndexingConfig
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup


CASES = (
    {
        "name": "missing_point_never_broadens",
        "query": ". дай пункт 22 из ГОСТа по терминам",
        "must_contain": "не найден",
        "must_have_no_evidence": True,
    },
    {
        "name": "term_definition_uses_gost_terms",
        "query": "дай определение оперативного персонала. используй ГОСТ по терминам",
        "must_contain": "107",
        "must_have_no_evidence": False,
    },
)


def main() -> None:
    config = IndexingConfig.from_env()
    results: list[dict[str, object]] = []
    for case in CASES:
        result = run_norm_lookup(
            NormLookupRequest(
                query=case["query"],
                understand_query_mode="auto",
                llm_provider="none",
                mode="evidence",
                limit=8,
            ),
            config,
        )
        answer = result.answer or ""
        evidence = [
            {
                "doc_name": item.doc_name,
                "point_number": item.point_number,
                "source_tool": item.source_tool,
                "text": item.text[:160],
            }
            for item in result.evidence[:5]
        ]
        ok = str(case["must_contain"]).casefold() in answer.casefold()
        if case["must_have_no_evidence"]:
            ok = ok and not evidence
        else:
            ok = ok and any(item["point_number"] == "107" for item in evidence)
        results.append(
            {
                "name": case["name"],
                "query": case["query"],
                "ok": ok,
                "answer": answer[:500],
                "warnings": result.warnings,
                "evidence": evidence,
            }
        )

    summary = {
        "total": len(results),
        "passed": sum(bool(item["ok"]) for item in results),
        "failed": sum(not bool(item["ok"]) for item in results),
    }
    payload = {"summary": summary, "cases": results}
    out = Path(__file__).with_name("reports") / "behavior_matrix_20260913.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0 if summary["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
