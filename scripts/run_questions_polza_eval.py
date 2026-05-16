"""E2E norm_lookup with Polza final answer + Polza semantic judge vs tests/questions.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.text_normalize import normalize_catalog_text
from mr_norm.runtime.llm_clients import LLMRequest, build_chat_client, parse_json_object
from mr_norm.runtime.llm_profiles import POLZA_FINAL_ANSWER_MODEL, resolve_role_profile
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup

DEFAULT_QUESTIONS = ROOT / "tests" / "questions.json"
DEFAULT_REPORT = ROOT / "reports" / "questions_polza_eval.json"
JUDGE_MODEL = "qwen/qwen3.5-flash-02-23"

JUDGE_SYSTEM = """Ты оцениваешь ответы по нормативным документам РФ.
Сравни фактический ответ с эталонным. Эталон может быть короче; фактический может содержать цитаты.
Ответ считается эквивалентным, если он указывает на тот же нормативный акт/документ/сущность и не противоречит эталону.
Верни только JSON:
{"equivalent": true|false, "score": 0.0-1.0, "reason": "кратко на русском"}"""


@dataclass
class PolzaEvalEntry:
    question: str
    expected_answer: str
    actual_answer: str = ""
    evidence_count: int = 0
    top_doc_names: list[str] = field(default_factory=list)
    judge_equivalent: bool = False
    judge_score: float = 0.0
    judge_reason: str = ""
    warnings: list[str] = field(default_factory=list)


def judge_answer(
    question: str,
    expected: str,
    actual: str,
    *,
    keys_path: Path | None,
    judge_model: str,
) -> dict:
    if not actual.strip() or actual.startswith("ERROR:"):
        return {"equivalent": False, "score": 0.0, "reason": "пустой или ошибочный ответ"}
    if actual.startswith("No evidence found"):
        return {"equivalent": False, "score": 0.0, "reason": "нет evidence"}

    profile = resolve_role_profile("polza", "final_answer")
    client = build_chat_client("polza", judge_model, keys_path=keys_path)
    response = client.chat(
        LLMRequest(
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "expected_answer": expected,
                            "actual_answer": actual[:6000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            model=judge_model,
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
    )
    payload = parse_json_object(response.content)
    return {
        "equivalent": bool(payload.get("equivalent")),
        "score": float(payload.get("score", 0.0)),
        "reason": str(payload.get("reason") or ""),
    }


def run_eval(
    questions_path: Path,
    *,
    limit: int = 5,
    profile: str = "balanced",
    keys_path: Path | None,
    judge_model: str,
    final_answer_model: str,
    max_questions: int | None = None,
    reuse_lookup_path: Path | None = None,
    judge_only: bool = False,
) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if max_questions:
        questions = questions[:max_questions]

    reuse: dict[str, dict] = {}
    if reuse_lookup_path and reuse_lookup_path.is_file():
        prior = json.loads(reuse_lookup_path.read_text(encoding="utf-8"))
        for entry in prior.get("questions") or []:
            reuse[str(entry.get("question") or "")] = entry

    config = IndexingConfig.from_env()
    entries: list[dict] = []
    equivalent_count = 0
    score_sum = 0.0
    with_evidence = 0
    started = time.perf_counter()

    for index, item in enumerate(questions, start=1):
        question = str(item.get("question") or "").strip()
        expected = str(item.get("answer") or "").strip()
        safe = question[:70].encode("cp1251", errors="replace").decode("cp1251")
        print(f"[{index}/{len(questions)}] {safe}...", flush=True)

        cached = reuse.get(question)
        actual = str(cached.get("actual_answer") or "") if cached else ""
        evidence_count = int(cached.get("evidence_count") or 0) if cached else 0
        top_docs = list(cached.get("top_doc_names") or []) if cached else []
        warnings = list(cached.get("warnings") or []) if cached else []

        if not judge_only:
            request = NormLookupRequest(
                query=question,
                limit=limit,
                profile=profile,
                understand_query_mode="auto",
                planner_backend="deterministic",
                reranker_backend="score",
                final_answer_backend="prompt",
                llm_provider="polza",
                final_answer_model=final_answer_model,
                enable_pue_aliases=False,
            )
            try:
                result = run_norm_lookup(request, config, keys_path=keys_path)
                actual = result.answer
                evidence_count = len(result.evidence)
                top_docs = [ev.doc_name for ev in result.evidence[:3]]
                warnings = list(result.warnings)[:8]
            except Exception as exc:
                actual = f"ERROR: {type(exc).__name__}: {exc}"
                warnings = [str(exc)]

        if evidence_count:
            with_evidence += 1

        try:
            verdict = judge_answer(
                question,
                expected,
                actual,
                keys_path=keys_path,
                judge_model=judge_model,
            )
        except Exception as exc:
            verdict = {"equivalent": False, "score": 0.0, "reason": f"judge error: {exc}"}
            warnings.append(str(exc))

        if verdict["equivalent"]:
            equivalent_count += 1
        score_sum += float(verdict["score"])

        entries.append(
            asdict(
                PolzaEvalEntry(
                    question=question,
                    expected_answer=expected,
                    actual_answer=actual,
                    evidence_count=evidence_count,
                    top_doc_names=top_docs,
                    judge_equivalent=bool(verdict["equivalent"]),
                    judge_score=round(float(verdict["score"]), 4),
                    judge_reason=str(verdict["reason"]),
                    warnings=warnings,
                )
            )
        )

    elapsed = round(time.perf_counter() - started, 3)
    total = len(entries)
    return {
        "schema_version": "mr_questions_polza_eval_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "questions_path": str(questions_path),
        "final_answer_model": final_answer_model,
        "judge_model": judge_model,
        "questions_total": total,
        "metrics": {
            "with_evidence": with_evidence,
            "equivalent_rate": round(equivalent_count / total, 4) if total else 0.0,
            "mean_judge_score": round(score_sum / total, 4) if total else 0.0,
            "elapsed_sec": elapsed,
        },
        "questions": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Polza E2E + semantic judge for questions.json")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--final-answer-model", default=POLZA_FINAL_ANSWER_MODEL)
    parser.add_argument("--reuse-lookup", type=Path, default=None)
    parser.add_argument("--judge-only", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("QDRANT_HOST", "localhost")
    os.environ.setdefault("QDRANT_PORT", "6333")
    os.environ.setdefault("MR_NORM_QDRANT_COLLECTION", "mr_norm_docs_bge_m3")

    keys_path = ProjectPaths.from_root(ROOT).root / "keys"
    report = run_eval(
        args.questions,
        limit=args.limit,
        profile=args.profile,
        keys_path=keys_path if keys_path.is_file() else None,
        judge_model=args.judge_model,
        final_answer_model=args.final_answer_model,
        max_questions=args.max_questions or None,
        reuse_lookup_path=args.reuse_lookup,
        judge_only=args.judge_only,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
