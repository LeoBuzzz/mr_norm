"""Diagnostic runner for tests/questions.json — planner/retrieval proxy metrics only."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from mr_norm.retrieval.text_normalize import normalize_catalog_text
from mr_norm.runtime.query_planner import plan_query

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = ROOT / "tests" / "questions.json"
DEFAULT_REPORT = ROOT / "reports" / "questions_diagnostic.json"


@dataclass
class QuestionDiagnostic:
    question: str
    expected_answer: str
    resolved_doc_names: list[str] = field(default_factory=list)
    tool_queries: dict[str, list[str]] = field(default_factory=dict)
    exact_phrase_terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected_tokens_hit: int = 0
    expected_tokens_total: int = 0


def _expected_tokens(expected: str) -> list[str]:
    norm = normalize_catalog_text(expected)
    tokens: list[str] = []
    for match in re.finditer(r"\d+[-\d]*", norm):
        tokens.append(match.group(0))
    for token in norm.split():
        if len(token) >= 5 and token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _proxy_hits(expected: str, plan_text: str) -> tuple[int, int]:
    tokens = _expected_tokens(expected)
    if not tokens:
        return 0, 0
    norm = normalize_catalog_text(plan_text)
    hits = sum(1 for token in tokens if token in norm)
    return hits, len(tokens)


def run_diagnostic(
    questions_path: Path,
    *,
    enable_pue_aliases: bool = False,
    mode: str = "auto",
) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    with_evidence = 0
    with_doc_filter = 0
    token_hits = 0
    token_total = 0

    for item in questions:
        question = str(item.get("question") or "").strip()
        expected = str(item.get("answer") or "").strip()
        plan = plan_query(
            question,
            mode=mode,
            enable_pue_aliases=enable_pue_aliases,
        )
        tool_queries = {
            entry.tool_name: list(entry.queries) for entry in plan.tool_queries
        }
        plan_blob = json.dumps(plan.to_dict(), ensure_ascii=False)
        hits, total = _proxy_hits(expected, plan_blob)
        token_hits += hits
        token_total += total
        if plan.resolved_doc_names:
            with_doc_filter += 1
        if plan.tool_queries:
            with_evidence += 1

        from mr_norm.retrieval.document_knowledge import match_query_terms

        terms = match_query_terms(question, enable_pue_aliases=enable_pue_aliases)
        diag = QuestionDiagnostic(
            question=question,
            expected_answer=expected,
            resolved_doc_names=list(plan.resolved_doc_names),
            tool_queries=tool_queries,
            exact_phrase_terms=list(terms.exact_phrase_terms),
            warnings=list(plan.warnings),
            expected_tokens_hit=hits,
            expected_tokens_total=total,
        )
        entries.append(asdict(diag))

    return {
        "schema_version": "mr_questions_diagnostic_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "questions_path": str(questions_path),
        "mode": mode,
        "enable_pue_aliases": enable_pue_aliases,
        "questions_total": len(entries),
        "metrics": {
            "with_tool_queries": with_evidence,
            "with_doc_name_filter": with_doc_filter,
            "expected_token_hit_rate": round(token_hits / token_total, 4) if token_total else 0.0,
        },
        "questions": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run questions.json planner diagnostic")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--enable-pue", action="store_true")
    args = parser.parse_args()

    report = run_diagnostic(
        args.questions,
        enable_pue_aliases=args.enable_pue,
        mode=args.mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
