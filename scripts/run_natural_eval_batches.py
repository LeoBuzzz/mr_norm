"""Run natural corpus eval in batches of 10 with visible progress."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "reports/pipeline_research/corpus_50_natural.json"
OUTPUT = ROOT / "reports/pipeline_research/eval_results_natural42.json"
SCRIPT = ROOT / "scripts/run_pipeline_research.py"
BATCH_SIZE = 10


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    case_ids = [str(item.get("id") or "") for item in corpus.get("items") or [] if item.get("id")]
    batches = [case_ids[i : i + BATCH_SIZE] for i in range(0, len(case_ids), BATCH_SIZE)]

    print(f"Natural eval: {len(case_ids)} cases in {len(batches)} batches", flush=True)
    started = time.perf_counter()

    for batch_num, batch in enumerate(batches, start=1):
        print(
            f"\n=== BATCH {batch_num}/{len(batches)}: {batch[0]}..{batch[-1]} ({len(batch)} cases) ===",
            flush=True,
        )
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--phase",
            "eval",
            "--corpus",
            str(CORPUS),
            "--eval-output",
            str(OUTPUT),
            "--skip-evidence-judge",
            "--limit",
            "40",
            "--case-ids",
            ",".join(batch),
        ]
        if OUTPUT.is_file():
            cmd.extend(["--reuse-eval", str(OUTPUT)])

        result = subprocess.run(cmd, cwd=str(ROOT))
        if result.returncode != 0:
            print(f"Batch {batch_num} failed with exit code {result.returncode}", flush=True)
            return result.returncode

        if OUTPUT.is_file():
            data = json.loads(OUTPUT.read_text(encoding="utf-8"))
            metrics = data.get("metrics") or {}
            print(
                f"Batch {batch_num} done | completed={len(data.get('entries') or [])}/{len(case_ids)} "
                f"| mean={metrics.get('mean_judge_score')} "
                f"| chunk_hit={metrics.get('chunk_in_evidence_rate')} "
                f"| elapsed={metrics.get('elapsed_sec')}s",
                flush=True,
            )

    elapsed = time.perf_counter() - started
    print(f"\nAll batches finished in {elapsed:.0f}s -> {OUTPUT}", flush=True)
    if OUTPUT.is_file():
        metrics = json.loads(OUTPUT.read_text(encoding="utf-8")).get("metrics") or {}
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
