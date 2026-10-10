"""Count term frequencies in one cleaned RTF.

Keeps nouns, abbreviations, and noun phrases of two or three words.
Drops function words and a short legal stop list. Writes a ranked JSON
list with the occurrence count of each term.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pymorphy3

from extract_glossary_cards import rtf_to_plain

TOKEN_RE = re.compile(r"[А-ЯЁA-Zа-яёa-z][А-ЯЁA-Zа-яёa-z\-]{1,}")
SENTENCE_RE = re.compile(r"[^.!?;:\n]+")

# Document skeleton and function-like lemmas, not domain terms.
STOP = {
    "настоящий",
    "глава",
    "правило",
    "пункт",
    "статья",
    "приложение",
    "часть",
    "абзац",
    "подпункт",
    "случай",
    "лицо",
    "организация",
    "документ",
    "требование",
    "порядок",
    "соответствие",
    "осуществление",
    "проведение",
    "принятие",
    "утверждение",
    "федеральный",
    "российский",
    "федерация",
    "правительство",
    "министерство",
    "приказ",
    "постановление",
    "закон",
    "издание",
    "раздел",
    "содержание",
    "ссылка",
    "дата",
    "введение",
    "предисловие",
    "который",
    "этот",
    "весь",
    "свой",
    "такой",
    "другой",
    "также",
    "мочь",
    "должен",
    "быть",
    "являться",
    "год",
    "число",
    "вид",
    "форма",
    "цель",
    "отношение",
    "наличие",
    "отсутствие",
    "необходимость",
    "возможность",
    "время",
    "место",
    "работа",
    "действие",
    "применение",
    "использование",
    "обеспечение",
    "определение",
    "общий",
    "следующий",
    "указанный",
    "данный",
    "россия",
    "любой",
    "каждый",
    "первый",
    "второй",
    "третий",
    "особый",
    "нормальный",
    "дополнительный",
    "основной",
    "отдельный",
    "соответствующий",
    "установленный",
    "вышеуказанный",
}

PHRASE_POS = {"NOUN", "ADJF", "ABBR"}
HEAD_POS = {"NOUN", "ABBR"}


_LEMMA_CACHE: dict[str, tuple[str, str]] = {}


def lemma_pos(morph: pymorphy3.MorphAnalyzer, token: str) -> tuple[str, str]:
    cached = _LEMMA_CACHE.get(token)
    if cached is not None:
        return cached
    if re.fullmatch(r"[А-ЯЁ]{2,8}", token):
        result = (token, "ABBR")
    else:
        parsed = morph.parse(token)[0]
        pos = str(parsed.tag.POS or "")
        result = (parsed.normal_form.replace("ё", "е"), pos)
    _LEMMA_CACHE[token] = result
    return result


def iter_sentences(text: str):
    for chunk in SENTENCE_RE.findall(text):
        tokens = []
        for raw in TOKEN_RE.findall(chunk):
            if len(raw) < 3 and not re.fullmatch(r"[А-ЯЁ]{2,8}", raw):
                continue
            tokens.append(raw)
        if tokens:
            yield tokens


def count_terms(text: str, morph: pymorphy3.MorphAnalyzer) -> Counter:
    counts: Counter = Counter()
    for tokens in iter_sentences(text):
        analyzed = [lemma_pos(morph, token) for token in tokens]
        # Windows stay in text order. A dropped word must break the phrase,
        # otherwise "электроприемники первой категории" collapses into a fake bigram.
        for size in (1, 2, 3):
            for i in range(len(analyzed) - size + 1):
                window = analyzed[i : i + size]
                if any(lemma in STOP or pos not in PHRASE_POS or len(lemma) <= 2 for lemma, pos in window):
                    continue
                if window[-1][1] not in HEAD_POS:
                    continue
                if size == 1:
                    pos = "ABBR" if window[0][1] == "ABBR" else "NOUN"
                    counts[(window[0][0], pos)] += 1
                    continue
                term = " ".join(lemma for lemma, _ in window)
                counts[(term, f"{size}-gram")] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rtf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--source", default="")
    args = parser.parse_args()

    text = rtf_to_plain(args.rtf)
    morph = pymorphy3.MorphAnalyzer()
    counts = count_terms(text, morph)
    ranked = [
        {"term": term, "pattern": pattern, "count": count, "docs": 1}
        for (term, pattern), count in counts.items()
        if count >= args.min_count
    ]
    ranked.sort(key=lambda row: (-row["count"], row["term"]))
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    payload = {
        "source": args.source or args.rtf.name,
        "chars": len(text),
        "min_count": args.min_count,
        "terms": ranked,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "source": payload["source"],
                "chars": payload["chars"],
                "terms": len(ranked),
                "top": ranked[:15],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
