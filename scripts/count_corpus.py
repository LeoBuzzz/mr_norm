"""Rank terms across every RTF in a directory.

Same candidates as count_term_freq.py: nouns, abbreviations, and noun
phrases of two or three words. Each kept term stores how many times it
occurred and in how many files.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

import pymorphy3

from count_term_freq import count_terms
from extract_glossary_cards import rtf_to_plain


def rank_terms(
    totals: Counter,
    doc_freq: Counter,
    top: int,
    min_count: int,
) -> list[dict]:
    rows = [
        {
            "term": term,
            "pattern": pattern,
            "count": count,
            "docs": doc_freq[(term, pattern)],
        }
        for (term, pattern), count in totals.items()
        if count >= min_count
    ]
    rows.sort(key=lambda row: (-row["count"], -row["docs"], row["term"]))
    chosen = rows[:top]
    for index, row in enumerate(chosen, start=1):
        row["rank"] = index
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--top", type=int, default=10000)
    parser.add_argument("--min-count", type=int, default=2)
    args = parser.parse_args()

    files = sorted(args.directory.glob("*.rtf"))
    if not files:
        sys.exit(f"no rtf files in {args.directory}")

    morph = pymorphy3.MorphAnalyzer()
    totals: Counter = Counter()
    doc_freq: Counter = Counter()
    failed: list[dict] = []
    chars = 0
    started = time.time()

    for index, path in enumerate(files, start=1):
        file_started = time.time()
        try:
            text = rtf_to_plain(path)
            local = count_terms(text, morph)
        except Exception as exc:  # one bad export should not stop the corpus
            failed.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            traceback.print_exc()
            continue
        chars += len(text)
        for key, count in local.items():
            totals[key] += count
            doc_freq[key] += 1
        print(
            f"[{index}/{len(files)}] terms={len(totals)} chars={chars} "
            f"{time.time() - file_started:.1f}s {path.name[:80]}",
            flush=True,
        )

    ranked = rank_terms(totals, doc_freq, args.top, args.min_count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in ranked:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    above_min = sum(1 for count in totals.values() if count >= args.min_count)
    summary = {
        "files": len(files),
        "files_ok": len(files) - len(failed),
        "files_failed": failed,
        "chars": chars,
        "unique_terms": len(totals),
        "terms_at_min_count": above_min,
        "written": len(ranked),
        "min_count": args.min_count,
        "top_requested": args.top,
        "seconds": round(time.time() - started, 1),
        "top10": ranked[:10],
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("files_ok", "chars", "written", "seconds", "top10")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
