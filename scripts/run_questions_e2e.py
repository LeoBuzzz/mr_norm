"""End-to-end norm_lookup benchmark for tests/questions.json against live Qdrant."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.text_normalize import normalize_catalog_text
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup

DEFAULT_QUESTIONS = ROOT / "tests" / "questions.json"
DEFAULT_REPORT = ROOT / "reports" / "questions_e2e.json"


@dataclass
class QuestionE2E:
    question: str
    expected_answer: str
    actual_answer: str = ""
    evidence_count: int = 0
    top_doc_names: list[str] = field(default_factory=list)
    top_point_numbers: list[str] = field(default_factory=list)
    exact_phrase_terms: list[str] = field(default_factory=list)
    payload_queries: list[str] = field(default_factory=list)
    required_tokens: list[str] = field(default_factory=list)
    resolved_doc_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected_tokens_hit: int = 0
    expected_tokens_total: int = 0
    phrase_tokens_in_top_evidence: bool = False


def _expected_tokens(expected: str) -> list[str]:
    norm = normalize_catalog_text(expected)
    tokens: list[str] = []
    for match in re.finditer(r"\d+[-\d]*", norm):
        tokens.append(match.group(0))
    for token in norm.split():
        if len(token) >= 5 and token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _proxy_hits(expected: str, blob: str) -> tuple[int, int]:
    tokens = _expected_tokens(expected)
    if not tokens:
        return 0, 0
    norm = normalize_catalog_text(blob)
    hits = sum(1 for token in tokens if token in norm)
    return hits, len(tokens)


def _phrase_tokens_in_evidence(exact_phrases: list[str], top_text: str) -> bool:
    if not exact_phrases:
        return False
    from mr_norm.retrieval.document_knowledge import phrase_required_tokens, primary_exact_phrase

    primary = primary_exact_phrase(exact_phrases)
    if not primary:
        return False
    required = phrase_required_tokens(primary)
    norm = normalize_catalog_text(top_text)
    return bool(required) and all(token in norm for token in required)


def run_e2e(
    questions_path: Path,
    *,
    limit: int = 5,
    profile: str = "balanced",
    enable_pue_aliases: bool = False,
    skip_vector: bool = False,
) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    config = IndexingConfig.from_env()
    entries: list[dict] = []
    with_evidence = 0
    token_hits = 0
    token_total = 0
    phrase_ok = 0
    started = time.perf_counter()

    for index, item in enumerate(questions, start=1):
        question = str(item.get("question") or "").strip()
        expected = str(item.get("answer") or "").strip()
        safe_question = question[:70].encode("cp1251", errors="replace").decode("cp1251")
        print(f"[{index}/{len(questions)}] {safe_question}...", flush=True)
        request = NormLookupRequest(
            query=question,
            limit=limit,
            profile=profile if not skip_vector else "fast",
            understand_query_mode="auto",
            planner_backend="deterministic",
            reranker_backend="score",
            final_answer_backend="evidence",
            llm_provider="none",
            enable_pue_aliases=enable_pue_aliases,
        )
        try:
            result = run_norm_lookup(request, config)
        except Exception as exc:
            entries.append(
                asdict(
                    QuestionE2E(
                        question=question,
                        expected_answer=expected,
                        actual_answer=f"ERROR: {type(exc).__name__}: {exc}",
                        warnings=[str(exc)],
                    )
                )
            )
            continue

        plan = result.prepared_plan
        exact_phrases = list(plan.exact_phrase_terms) if plan else []
        payload_queries: list[str] = []
        required_tokens: list[str] = []
        if plan:
            for entry in plan.tool_queries:
                if entry.tool_name == "payload":
                    payload_queries = list(entry.queries)
                    required_tokens = list(entry.required_tokens)
                    break

        evidence_blob = " ".join(
            f"{item.doc_name} {item.point_number} {item.text}" for item in result.evidence[:limit]
        )
        answer_blob = f"{result.answer}\n{evidence_blob}"
        hits, total = _proxy_hits(expected, answer_blob)
        token_hits += hits
        token_total += total
        if result.evidence:
            with_evidence += 1
        phrase_match = _phrase_tokens_in_evidence(exact_phrases, evidence_blob)
        if phrase_match or not exact_phrases:
            phrase_ok += 1

        entries.append(
            asdict(
                QuestionE2E(
                    question=question,
                    expected_answer=expected,
                    actual_answer=result.answer,
                    evidence_count=len(result.evidence),
                    top_doc_names=[item.doc_name for item in result.evidence[:3]],
                    top_point_numbers=[item.point_number for item in result.evidence[:3]],
                    exact_phrase_terms=exact_phrases,
                    payload_queries=payload_queries,
                    required_tokens=required_tokens,
                    resolved_doc_names=list(plan.resolved_doc_names) if plan else [],
                    warnings=list(result.warnings)[:12],
                    expected_tokens_hit=hits,
                    expected_tokens_total=total,
                    phrase_tokens_in_top_evidence=phrase_match,
                )
            )
        )

    elapsed = round(time.perf_counter() - started, 3)
    return {
        "schema_version": "mr_questions_e2e_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "questions_path": str(questions_path),
        "collection_name": config.collection_name,
        "qdrant_url": config.qdrant_url,
        "profile": profile,
        "enable_pue_aliases": enable_pue_aliases,
        "questions_total": len(entries),
        "metrics": {
            "with_evidence": with_evidence,
            "expected_token_hit_rate": round(token_hits / token_total, 4) if token_total else 0.0,
            "exact_phrase_evidence_rate": round(phrase_ok / len(entries), 4) if entries else 0.0,
            "elapsed_sec": elapsed,
        },
        "questions": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run questions.json through norm_lookup E2E")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--enable-pue", action="store_true")
    parser.add_argument("--skip-vector", action="store_true", help="Use fast profile (payload-heavy).")
    args = parser.parse_args()

    os.environ.setdefault("QDRANT_HOST", "localhost")
    os.environ.setdefault("QDRANT_PORT", "6333")
    os.environ.setdefault("MR_NORM_QDRANT_COLLECTION", "mr_norm_docs_bge_m3")

    report = run_e2e(
        args.questions,
        limit=args.limit,
        profile=args.profile,
        enable_pue_aliases=args.enable_pue,
        skip_vector=args.skip_vector,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
