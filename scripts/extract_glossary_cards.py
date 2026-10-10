"""Extract glossary cards from a Kodeks-style RTF terminology standard.

Writes JSONL cards and a classification prompt. Does not call a model.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from striprtf.striprtf import rtf_to_text

KODEKS_LINK = re.compile(r'\("kodeks://[^"]*"(?:\\o"[^"]*")?\)')
# Numbered glossary entry. The head may open with "(", as in
# "(электрическая) подстанция". The colon closes the head.
DEF_RE = re.compile(
    r"(?:(?<=\s)|^)(\d{1,3})\s+(\(?[а-яё][^:]{1,180}?):\s+",
)


def rtf_to_plain(path: Path) -> str:
    raw = path.read_text(encoding="cp1251", errors="replace")
    text = rtf_to_text(raw)
    text = KODEKS_LINK.sub(" ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def extract_definitions(text: str) -> list[dict]:
    marker = "2 Термины и определения"
    start = text.find(marker)
    if start < 0:
        start = text.find("Термины и определения")
    if start < 0:
        start = 0
    end = text.find("Алфавитный указатель", start)
    body = text[start:end] if end > start else text[start:]
    body = re.sub(r"\s+", " ", body).strip()
    matches = list(DEF_RE.finditer(body))
    cards: list[dict] = []
    for i, match in enumerate(matches):
        def_start = match.end()
        def_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        definition = body[def_start:def_end].strip(" ;")
        definition = re.sub(r"\s+", " ", definition)
        head = re.sub(r"\s+", " ", match.group(2)).strip()
        cards.append(
            {
                "id": match.group(1),
                "head": head,
                "context": definition,
                "kind": "glossary",
            }
        )
    return cards


BOILERPLATE = [
    ("B1", "межгосударственный стандарт", "Обозначение вида документа на титуле."),
    ("B2", "настоящий стандарт", "Ссылка документа на самого себя в предисловии."),
    ("B3", "федеральное агентство по техническому регулированию и метрологии", "Орган, вводящий стандарт в действие."),
    ("B4", "алфавитный указатель терминов", "Навигационный раздел в конце стандарта."),
    ("B5", "ключевые слова", "Служебная строка библиографического описания."),
    ("B6", "дата введения", "Реквизит начала действия стандарта."),
    ("B7", "официальное издание", "Помета об источнике сверки электронного текста."),
    ("B8", "электронный текст документа", "Помета подготовителя выгрузки, не термин предметной области."),
]


def write_cards(cards: list[dict], path: Path, limit: int | None) -> list[dict]:
    chosen = cards[:limit] if limit else cards
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for card in chosen:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    return chosen


def build_prompt(cards: list[dict], context_chars: int) -> str:
    lines = []
    for card in cards:
        context = card["context"][:context_chars].strip()
        lines.append(f"{card['id']}\t{card['head']}\t{context}")
    body = "\n".join(lines)
    return (
        "Разметь карточки кандидата в термины электроэнергетики.\n"
        "Для каждой строки верни один объект: "
        '{"id","keep","canonical","abbr"}.\n'
        "keep=true, если это термин предметной области (слово или словосочетание), "
        "включая краткие формы в head.\n"
        "keep=false для служебных реквизитов, отсылок к документу и общих слов вне области.\n"
        "canonical — словарная форма в именительном падеже, без номера и без аббревиатуры.\n"
        "abbr — краткая форма из head, если она есть, иначе пустая строка.\n"
        "Ответ — только JSON-массив, без пояснений и без markdown.\n\n"
        "id\thead\tcontext\n"
        f"{body}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rtf", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--context-chars", type=int, default=220)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    text = rtf_to_plain(args.rtf)
    glossary = extract_definitions(text)
    boilerplate = [
        {"id": i, "head": h, "context": c, "kind": "boilerplate"}
        for i, h, c in BOILERPLATE
    ]
    cards = glossary + boilerplate
    if args.limit:
        # Keep the probe mixed: glossary prefix plus all boilerplate.
        gloss_n = max(args.limit - len(boilerplate), 1)
        cards = glossary[:gloss_n] + boilerplate

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_cards(cards, args.out_dir / "cards.jsonl", None)
    prompt = build_prompt(cards, args.context_chars)
    (args.out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    summary = {
        "source": args.rtf.name,
        "glossary_cards": len(glossary),
        "cards_in_prompt": len(cards),
        "context_chars": args.context_chars,
        "prompt_chars": len(prompt),
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
