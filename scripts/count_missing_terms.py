"""Count definition lemmas that are absent from the top term list.

The join key is the lemma already stored on each definition card. Tokenization
and lemmatization follow that key, including prepositions that the frequency
counter drops. The supplemented file keeps the original 10000 rows and appends
the missing lemmas with corpus counts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymorphy3

from count_term_freq import SENTENCE_RE, lemma_pos
from extract_glossary_cards import rtf_to_plain

TOKEN_RE = re.compile(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{1,}")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def lemma_tokens(text: str, morph: pymorphy3.MorphAnalyzer) -> list[str]:
    tokens = []
    for chunk in SENTENCE_RE.findall(text):
        tokens.append("")
        for raw in TOKEN_RE.findall(chunk):
            lemma, _pos = lemma_pos(morph, raw)
            tokens.append(lemma)
    return tokens


def count_lemmas(directory: Path, targets: set[tuple[str, ...]], morph: pymorphy3.MorphAnalyzer) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], int]]:
    counts = {gram: 0 for gram in targets}
    docs = {gram: 0 for gram in targets}
    by_length: dict[int, set[tuple[str, ...]]] = {}
    for gram in targets:
        by_length.setdefault(len(gram), set()).add(gram)
    lengths = sorted(by_length)
    files = sorted(directory.glob("*.rtf"))
    for index, path in enumerate(files, start=1):
        tokens = lemma_tokens(rtf_to_plain(path), morph)
        seen: set[tuple[str, ...]] = set()
        total = len(tokens)
        for start, token in enumerate(tokens):
            if not token:
                continue
            for size in lengths:
                if start + size > total:
                    break
                window = tokens[start : start + size]
                if "" in window:
                    break
                gram = tuple(window)
                if gram in by_length[size]:
                    counts[gram] += 1
                    seen.add(gram)
        for gram in seen:
            docs[gram] += 1
        if index % 20 == 0 or index == len(files):
            print(f"[{index}/{len(files)}]", flush=True)
    return counts, docs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--definitions", type=Path, required=True)
    parser.add_argument("--frequencies", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    definitions = load_jsonl(args.definitions)
    frequencies = load_jsonl(args.frequencies)
    known = {row["term"] for row in frequencies}
    missing = [row for row in definitions if row["lemma"] not in known]
    targets = {tuple(row["lemma"].split()) for row in missing}
    morph = pymorphy3.MorphAnalyzer()
    counts, docs = count_lemmas(args.directory, targets, morph)

    rows = []
    for row in frequencies:
        rows.append(
            {
                "lemma": row["term"],
                "term": row["term"],
                "pattern": row["pattern"],
                "count": row["count"],
                "docs": row["docs"],
                "rank": row["rank"],
                "origin": "top",
            }
        )
    added = []
    for row in missing:
        gram = tuple(row["lemma"].split())
        size = len(gram)
        added.append(
            {
                "lemma": row["lemma"],
                "term": row["term"],
                "pattern": "NOUN" if size == 1 else f"{size}-gram",
                "count": counts[gram],
                "docs": docs[gram],
                "rank": None,
                "origin": "definition",
            }
        )
    added.sort(key=lambda row: (-row["count"], row["lemma"]))
    rows.extend(added)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    definition_lemmas = {row["lemma"] for row in definitions}
    written = {row["lemma"] for row in rows}
    zero = [row for row in added if row["count"] == 0]
    summary = {
        "top_terms": len(frequencies),
        "added": len(added),
        "added_with_counts": len(added) - len(zero),
        "added_zero": len(zero),
        "rows": len(rows),
        "definition_lemmas": len(definition_lemmas),
        "definition_lemmas_present": len(definition_lemmas & written),
        "zero_lemmas": [row["lemma"] for row in zero],
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("added", "added_with_counts", "added_zero", "rows", "definition_lemmas_present")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
