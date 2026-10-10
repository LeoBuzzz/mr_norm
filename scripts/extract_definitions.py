"""Extract term-definition pairs from the regulatory corpus.

Each definition stays attached to the source file name. One term may have
several definitions. Frequency fields are copied from the ranked term list
when the lemmatized head matches.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import pymorphy3

from count_term_freq import lemma_pos
from extract_glossary_cards import extract_definitions as extract_glossary
from extract_glossary_cards import rtf_to_plain

NUMBERED = re.compile(r"(?<!\d)(\d+(?:\.\d+)+)\.\s+")
DASH_HEAD = re.compile(
    r"([А-ЯЁ][а-яёА-ЯЁ\-]{1,40}(?:\s+(?:\([^)]{1,80}\)|[а-яёА-ЯЁ][а-яёА-ЯЁ\-]{1,40})){0,11})\s+-\s+"
)
PONIM = re.compile(
    r"[Пп]од\s+((?:[а-яёА-ЯЁ\-]+(?:\s+\([^)]{1,40}\))?\s+){0,10}[а-яёА-ЯЁ\-]+)\s+понима(?:е|ю)тся\s+(.{15,700}?)(?=\.\s|\s[Пп]од\s+|\Z)",
    re.S,
)
CONCEPTS = re.compile(r"основн\w+\s+поняти\w+\s*:", re.I)
ARTICLE = re.compile(r"Статья\s+(\d+(?:\.\d+)*)")
JUNK_HEAD = re.compile(
    r"редакц|абзац|федеральн|см\.|далее|изменен|ошибка|вероятно|приложение|постановление|приказ\b|закон\b",
    re.I,
)


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ;,.")


def clean_definition(text: str) -> str:
    text = re.split(r"\(?\s*[Аа]бзац\b", text)[0]
    text = re.sub(r"\([^)]{0,40}редакц[^)]*\)", " ", text, flags=re.I)
    text = re.sub(r"_{3,}", " ", text)
    text = re.sub(r"\s*\(Абзац[^)]*\)", " ", text, flags=re.I)
    text = re.split(r"\s\d+\)\s", text)[0]
    text = clean_space(text).rstrip(":")
    return strip_trailing_heading(text)


def strip_trailing_heading(text: str) -> str:
    """Drop a section title glued after the last sentence, such as 'Основные виды энергоустановок'."""
    match = re.match(r"^(.*\.)\s+([А-ЯЁ][^.]{3,90})$", text)
    if not match:
        return text
    tail = match.group(2)
    words = re.findall(r"[А-ЯЁа-яё\-]+", tail)
    if not words or len(words) > 6 or "," in tail:
        return text
    for word in words:
        _lemma, pos = lemma_pos(_morph(), word)
        if pos in {"VERB", "INFN", "GRND", "PRTF", "PRTS"}:
            return text
    return match.group(1)


def _alias(item: str, aliases: list[str]) -> None:
    item = clean_space(re.sub(r"^Нрк\.?\s*", "", item, flags=re.I)).strip("()")
    if item and item not in aliases:
        aliases.append(item)


def split_head(head: str) -> tuple[str, list[str]]:
    head = clean_space(head.replace(";", "; "))
    aliases: list[str] = []
    # "(Нрк. a; b)" is one parenthesis. Splitting on ";" first would cut it open.
    # A parenthesis left in front of that note is part of the term.
    nrk = re.search(r"\s*\(\s*Нрк\.?\s*([^)]*)\)\s*$", head, flags=re.I)
    stripped_nrk = bool(nrk)
    if nrk:
        for part in nrk.group(1).split(";"):
            _alias(part, aliases)
        head = clean_space(head[: nrk.start()])
    pieces = [clean_space(part) for part in head.split(";") if clean_space(part)]
    if not pieces:
        return "", aliases
    primary = pieces[0]
    for extra in pieces[1:]:
        _alias(extra, aliases)
    # A parenthesis at the end is a synonym or abbreviation. A parenthesis in
    # the middle is part of the term: "цены (тарифы) в электроэнергетике".
    tail = re.search(r"\s*\(([^)]+)\)\s*$", primary)
    if tail and not stripped_nrk and primary.count("(") == primary.count(")"):
        _alias(tail.group(1), aliases)
        primary = clean_space(primary[: tail.start()])
    elif primary.count("(") != primary.count(")"):
        primary = clean_space(primary[: primary.rfind("(")])
    return primary, aliases


def good_head(head: str) -> bool:
    if not head or len(head) < 3 or len(head) > 140:
        return False
    if JUNK_HEAD.search(head):
        return False
    if re.search(r"\d", head):
        return False
    words = head.split()
    return 1 <= len(words) <= 18 and len(head) <= 220


def good_definition(text: str) -> bool:
    if len(text) < 20 or len(text) > 2500:
        return False
    if JUNK_HEAD.search(text[:80]) and len(text) < 80:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters >= 15


def has_content_noun(head: str) -> bool:
    for token in re.findall(r"[А-ЯЁа-яё][А-ЯЁа-яё\-]{3,}", head):
        _lemma, pos = lemma_pos(_morph(), token)
        if pos == "NOUN":
            return True
    return False


def _morph() -> pymorphy3.MorphAnalyzer:
    global MORPH
    if MORPH is None:
        MORPH = pymorphy3.MorphAnalyzer()
    return MORPH


MORPH: pymorphy3.MorphAnalyzer | None = None


def nominative_head(head: str) -> str:
    """Turn an instrumental 'под ...' phrase into a nominative term up to the first noun."""
    words = head.split()
    out = []
    seen_noun = False
    for word in words:
        parsed = _morph().parse(word)[0]
        if not seen_noun:
            inflected = parsed.inflect({"nomn"})
            out.append(inflected.word if inflected else word)
            if str(parsed.tag.POS or "") == "NOUN":
                seen_noun = True
        else:
            out.append(word)
    if not out:
        return head
    out[0] = out[0][:1].upper() + out[0][1:] if head[:1].isupper() else out[0]
    return " ".join(out)


# Heads Grok marked as clearly not terms: clauses, form rows, editorial notes, fragments.
REJECTED_HEADS = {
    "топливо",
    "методическим указаниям",
    "температуры воздуха",
    "тпир",
    "высшее образование",
    "асу тп оснащена электростанция",
    "вл всех уровней напряжения",
    "вл до кабельной линии связи",
    "вл или ее условное обозначение",
    "вторая буква",
    "защитного зажима",
    "инструктаж должен производить руководитель (или уполномоченный им работник) подразделения организации",
    "количество обслуживаемых договоров",
    "количество точек учета по обслуживаемым договорам",
    "количество этажей",
    "лпв не уплотнена вч аппаратурой",
    "лэп неоднородных участков",
    "лэп по условиям электроснабжения потребителя возможно",
    "место нахождения министерства энергетики российской федерации",
    "министерства",
    "министерство энергетики российской федерации вправе иметь геральдический знак",
    "несоблюдение сроков проведения обязательного энергетического обследования",
    "нн или нескольких компенсационных обмоток",
    "ограничители перенапряжений имеют защитный уровень ограничения коммутационных перенапряжений фаза",
    "орд организации",
    "первая буква",
    "первичный инструктаж командированного персонала должен проводить работник организации",
    "переносные заземления следует присоединять на металлических опорах",
    "последующие (после ) буквы",
    "правил технологического присоединения",
    "правилами расследования",
    "продолжительность испытания во всех случаях",
    "профессиональное обучение",
    "проходная по схеме два трансформатора",
    "проходная по схеме трансформатор",
    "пункт утратил силу",
    "разновидностью кру является ксо",
    "расходы на производство",
    "резервная защита блоков генератор",
    "рирт",
    "сведения об организациях",
    "сведения об энергосбытовых организациях",
    "смо работниками организации",
    "сопротивление самостоятельного заземляющего устройства должно соответствовать требованиям предприятия",
    "среднее профессиональное образование",
    "то же из несгораемых материалов",
    "токоведущая часть электроустановки под напряжением",
    "требования правил распространяются на работодателей",
    "тупиковая по схеме два трансформатора",
    "узо для защиты нескольких электроприемников",
    "указанный состав подписей должностных лиц организации",
    "цены (тарифами) на электрическую энергию и (или) мощность",
}
INSTRUCTION_HEAD = re.compile(
    r"\b(должен|должна|должно|должны|следует|вправе|необходимо|допускается|распространяются)\b|"
    r"\b(оснащена|оснащен|имеют|является|являются)\b|"
    r"утратил\w*\s+силу|^то же\b",
    re.I,
)


def add_pair(bucket: list[dict], file_name: str, locator: str, head: str, definition: str, pattern: str) -> None:
    if pattern == "ponimaetsya":
        if any(word in head.lower() for word in ("также", "либо", "который", "которая", "если", "однако")):
            return
        if len(head.split()) > 8:
            return
        head = nominative_head(head)
    primary, aliases = split_head(head)
    definition = clean_definition(definition)
    if pattern == "numbered" and not definition[:1].isalpha():
        return
    if LEADING_PREP.match(primary):
        return
    if primary.casefold() in REJECTED_HEADS or INSTRUCTION_HEAD.search(primary):
        return
    if re.search(r"\bкотор|утративш|настоящ\w+\s+стать", primary, re.I):
        return
    if primary.count("(") != primary.count(")"):
        return
    if re.search(r"\bкВ\b", primary) and re.search(r"[А-ЯЁA-Z]\s*-\s*[А-ЯЁA-Z]", primary):
        return
    if not has_content_noun(primary):
        return
    if not good_head(primary) or not good_definition(definition):
        return
    # Drop a definition that is only a cross-reference.
    if definition.lower().startswith("см.") or "предыдущую редакцию" in definition.lower()[:80]:
        return
    bucket.append(
        {
            "file": file_name,
            "locator": locator,
            "term_in_file": primary,
            "aliases": aliases,
            "definition": definition,
            "pattern": pattern,
        }
    )


def bare_head(head: str) -> str:
    head = re.sub(r"\([^)]*\)", " ", head)
    head = re.sub(r"\[[^]]*\]", " ", head)
    head = head.split(";")[0]
    return clean_space(head).lower()


def glossary_pairs(text: str, file_name: str) -> list[dict]:
    cards = extract_glossary(text)
    pairs = []
    for card in cards:
        head = card["head"]
        if not re.match(r"^[а-яё][а-яё\s\-]{1,220}$", bare_head(head)):
            continue
        add_pair(pairs, file_name, card["id"], head, card["context"], "glossary")
    titled = "термины и определения" in file_name.lower()
    if len(pairs) >= 8 or (titled and len(pairs) >= 5):
        return pairs
    return []


def dash_pairs_in_span(span: str, file_name: str, locator: str, pattern: str) -> list[dict]:
    matches = list(DASH_HEAD.finditer(span))
    pairs = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(span)
        add_pair(pairs, file_name, locator, match.group(1), span[match.end() : end], pattern)
    return pairs


def numbered_dash_pairs(text: str, file_name: str) -> list[dict]:
    flat = clean_space(text)
    marks = list(NUMBERED.finditer(flat))
    pairs = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else min(len(flat), mark.end() + 2500)
        body = flat[mark.end() : end]
        found = dash_pairs_in_span(body, file_name, mark.group(1), "numbered")
        for inline in re.finditer(
            r"выделяется\s+([а-яё][^,]{8,90}),\s+([а-яё].{20,400}?)(?=\.)",
            body,
        ):
            add_pair(found, file_name, mark.group(1), inline.group(1), inline.group(2), "numbered")
        for item in found:
            item["definition"] = clean_definition(
                re.split(r"\s+[Ии]з состава\b|\s+выделяется\s+", item["definition"])[0]
            )
        # A defining dash is followed by a lowercase explanation. That keeps
        # several terms inside one numbered point and drops narrative dashes.
        pairs.extend(
            item
            for item in found
            if item["definition"][:1].islower() and good_definition(item["definition"])
        )
    return pairs


CONCEPT_HEAD = re.compile(
    r"(?:(?<=[:;)])|^)\s*"
    r"([а-яёА-ЯЁ][^;]{2,220}?)\s+-\s+"
)
LEADING_PREP = re.compile(r"^(на|в|во|для|при|если|после|до|к|ко|с|со|по|о|об|от)\s+", re.I)


def concept_pairs(text: str, file_name: str) -> list[dict]:
    flat = clean_space(text)
    pairs = []
    for match in CONCEPTS.finditer(flat):
        start = match.end()
        window = flat[start : start + 80000]
        end_match = re.search(r"Статья\s+\d+|Глава\s+\d+", window[400:])
        section = window[: 400 + end_match.start()] if end_match else window[:25000]
        section = re.sub(r"\(далее\b[^)]*\)", " ", section)
        back = flat[max(0, match.start() - 500) : match.start()]
        articles = list(ARTICLE.finditer(back))
        locator = f"статья {articles[-1].group(1)}" if articles else "основные понятия"
        marks = list(CONCEPT_HEAD.finditer(section))
        for index, mark in enumerate(marks):
            head = re.sub(r"\s*\(далее\b[^)]*\)?", "", mark.group(1))
            head = clean_space(head)
            if LEADING_PREP.match(head) or "," in head or "утратил силу" in head.lower():
                continue
            end = marks[index + 1].start() if index + 1 < len(marks) else len(section)
            definition = section[mark.end() : end]
            add_pair(pairs, file_name, locator, head, definition, "concepts")
    return pairs


def ponimaetsya_pairs(text: str, file_name: str) -> list[dict]:
    flat = clean_space(text)
    pairs = []
    for match in PONIM.finditer(flat):
        head = match.group(1)
        if re.search(r"которы|настоящ|ним\b|ними\b", head, re.I):
            continue
        add_pair(pairs, file_name, "понимается", head, match.group(2), "ponimaetsya")
    return pairs


def extract_file(path: Path) -> list[dict]:
    text = rtf_to_plain(path)
    found = []
    found.extend(glossary_pairs(text, path.name))
    found.extend(numbered_dash_pairs(text, path.name))
    found.extend(concept_pairs(text, path.name))
    found.extend(ponimaetsya_pairs(text, path.name))
    # Same file can rediscover one article through two patterns. Keep the first.
    seen = set()
    unique = []
    for item in found:
        key = (item["term_in_file"].lower(), item["definition"][:180])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def lemma_key(morph: pymorphy3.MorphAnalyzer, term: str) -> str:
    parts = []
    for token in re.findall(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{1,}", term):
        lemma, _pos = lemma_pos(morph, token)
        parts.append(lemma)
    return " ".join(parts)


def load_frequencies(path: Path) -> dict[str, dict]:
    table = {}
    if not path.exists():
        return table
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        table[row["term"]] = {"count": row["count"], "docs": row["docs"], "rank": row["rank"]}
    return table


def group_terms(pairs: list[dict], morph: pymorphy3.MorphAnalyzer, frequencies: dict[str, dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for item in pairs:
        key = lemma_key(morph, item["term_in_file"])
        if not key:
            continue
        if key not in grouped:
            freq = frequencies.get(key, {})
            grouped[key] = {
                "term": item["term_in_file"],
                "lemma": key,
                "count": freq.get("count"),
                "docs": freq.get("docs"),
                "rank": freq.get("rank"),
                "definitions": [],
            }
            order.append(key)
        record = grouped[key]
        # Prefer the more frequent surface form already stored; first one stays
        # unless the new one is shorter and equally specific.
        definition = {
            "file": item["file"],
            "locator": item["locator"],
            "term": item["term_in_file"],
            "definition": item["definition"],
        }
        if item["aliases"]:
            definition["aliases"] = item["aliases"]
        record["definitions"].append(definition)
    rows = [grouped[key] for key in order]
    for row in rows:
        forms: dict[str, int] = defaultdict(int)
        for item in row["definitions"]:
            forms[item["term"]] += 1
        row["term"] = sorted(forms, key=lambda form: (-forms[form], len(form)))[0]
    rows.sort(key=lambda row: (-(row["count"] or 0), row["term"].lower()))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--frequencies", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    morph = pymorphy3.MorphAnalyzer()
    frequencies = load_frequencies(args.frequencies)
    pairs: list[dict] = []
    per_file = defaultdict(int)
    files = sorted(args.directory.glob("*.rtf"))
    for index, path in enumerate(files, start=1):
        found = extract_file(path)
        pairs.extend(found)
        per_file[path.name] = len(found)
        if index % 20 == 0 or index == len(files):
            print(f"[{index}/{len(files)}] pairs={len(pairs)}", flush=True)

    rows = group_terms(pairs, morph, frequencies)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_pattern = defaultdict(int)
    # pattern is not stored on the grouped row; count from raw pairs instead
    for item in pairs:
        by_pattern[item["pattern"]] += 1
    summary = {
        "files": len(files),
        "files_with_definitions": sum(1 for count in per_file.values() if count),
        "definitions": len(pairs),
        "terms": len(rows),
        "terms_with_frequency": sum(1 for row in rows if row["count"]),
        "by_pattern": dict(by_pattern),
        "top_files": sorted(per_file.items(), key=lambda item: -item[1])[:15],
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("definitions", "terms", "terms_with_frequency", "by_pattern")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
