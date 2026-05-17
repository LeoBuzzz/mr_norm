"""Generate 100 chunk-grounded Q&A pairs for query-preprocessing benchmarks.

Method: sample a random corpus chunk (excluding PUE chapter files), formulate a
question from its text, use the same chunk as the gold answer excerpt.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_PATH = ROOT / "output" / "qdrant_chunks.json"
OUT_PATH = ROOT / "tests" / "query_preprocessing_benchmark_100.md"

RANDOM_SEED = 42
CASE_COUNT = 100
MIN_TEXT_LEN = 90


def is_pue_chapter(payload: dict) -> bool:
    filename = (payload.get("filename") or "").lower()
    doc_name = (payload.get("doc_name") or "").lower()
    blob = f"{filename} {doc_name}"
    if "пуэ" not in blob and "правила устройства электроустановок" not in blob:
        return False
    if re.search(r"пуэ.*глава|глава\s*[\d.]", blob):
        return True
    if "правила устройства электроустановок" in blob and "глава" in blob:
        return True
    return "пуэ" in filename and "глава" in filename


def is_low_quality_chunk(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < MIN_TEXT_LEN:
        return True
    lowered = cleaned.lower()
    if lowered.startswith("[1]") or "библиограф" in lowered[:120]:
        return True
    if lowered.startswith("информация о введении") or lowered.startswith("информация об изменениях"):
        return True
    if cleaned.count("____") >= 2 and len(cleaned) < 200:
        return True
    alpha = sum(1 for ch in cleaned if ch.isalpha())
    if alpha < len(cleaned) * 0.45:
        return True
    return False


def short(text: str, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def extract_order_number(filename: str) -> str:
    match = re.search(r"(?:n|№|no\.?)\s*([\w-]+)", filename, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def extract_gost_number(filename: str, doc_name: str) -> str:
    for source in (filename, doc_name):
        match = re.search(r"гост\s*р?\s*([\d]+(?:-[\d]+)?)", source, flags=re.IGNORECASE)
        if match:
            prefix = "ГОСТ Р" if "гост р" in source.lower() or "гостр" in source.lower() else "ГОСТ"
            return f"{prefix} {match.group(1)}"
    return ""


def extract_point_number(payload: dict, text: str) -> str:
    point = str(payload.get("point_number") or "").strip()
    if point:
        return point
    match = re.match(r"^(\d+(?:\.\d+)+|\d+)\s*[.)]", text.strip())
    return match.group(1) if match else ""


def extract_topic_hint(text: str, *, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(\d+(?:\.\d+)+|\d+)\s*[.)]\s*", "", text)
    sentence = re.split(r"(?<=[.!?])\s+", text)[0].strip()
    if len(sentence) > limit:
        words = sentence.split()
        sentence = " ".join(words[:12]).rstrip(",;:") + "…"
    return sentence


def extract_definition_term(text: str) -> str:
    match = re.search(
        r"([А-Яа-яЁё][А-Яа-яЁё\s\-]{3,60}?)\s*[-–—:]\s",
        text,
    )
    if match:
        return match.group(1).strip()
    return ""


def doc_hint_short(payload: dict) -> str:
    filename = payload.get("filename") or ""
    doc_name = payload.get("doc_name") or ""
    order = extract_order_number(filename)
    if order:
        if "постановлен" in filename.lower():
            return f"постановлении № {order}"
        if "приказ" in filename.lower():
            return f"приказе № {order}"
        if "распоряжен" in filename.lower():
            return f"распоряжении № {order}"
        return f"документе № {order}"
    gost = extract_gost_number(filename, doc_name)
    if gost:
        return gost
    if doc_name and len(doc_name) < 90:
        return f"«{short(doc_name, 70)}»"
    return "нормативном документе"


def build_question(
    text: str,
    payload: dict,
    *,
    variant: int,
) -> tuple[str, str]:
    """Return (question, question_style)."""
    point = extract_point_number(payload, text)
    topic = extract_topic_hint(text)
    doc_hint = doc_hint_short(payload)
    term = extract_definition_term(text)
    gost = extract_gost_number(payload.get("filename") or "", payload.get("doc_name") or "")

    styles = [
        "point",
        "thematic",
        "doc_point",
        "definition",
        "implicit_doc",
        "abbrev_gost",
    ]
    style = styles[variant % len(styles)]

    if style == "point" and point:
        return f"Что установлено в пункте {point}?", "point_lookup"

    if style == "doc_point" and point:
        return f"Что предусмотрено в {doc_hint} в пункте {point}?", "implicit_doc"

    if style == "definition" and term:
        return f"Как определяется «{short(term, 50)}»?", "term"

    if style == "abbrev_gost" and gost:
        return f"Что указано в {gost} в приведённом фрагменте?", "abbreviation"

    if style == "implicit_doc":
        return f"Что требуется по {doc_hint} в отношении: {short(topic, 65)}?", "implicit_doc"

    if point:
        return f"Какие требования содержит пункт {point}?", "point_lookup"

    return f"Что говорится о следующем: {short(topic, 70)}?", "term"


def build_case(case_id: int, chunk: dict, variant: int) -> dict:
    payload = chunk.get("payload") or {}
    text = str(chunk.get("text") or "").strip()
    question, style = build_question(text, payload, variant=variant)
    point = extract_point_number(payload, text)
    return {
        "id": case_id,
        "style": style,
        "question": question,
        "expected_doc_name": str(payload.get("doc_name") or "").strip(),
        "expected_point_number": point,
        "expected_heading": str(payload.get("heading_path_text") or "").strip(),
        "expected_chunk_id": str(chunk.get("chunk_id") or ""),
        "source_filename": str(payload.get("filename") or "").strip(),
        "answer_excerpt": short(text, 400),
    }


def select_eligible_chunks(chunks: list[dict]) -> list[dict]:
    eligible: list[dict] = []
    for chunk in chunks:
        payload = chunk.get("payload") or {}
        if is_pue_chapter(payload):
            continue
        text = str(chunk.get("text") or "")
        if is_low_quality_chunk(text):
            continue
        eligible.append(chunk)
    return eligible


def render_markdown(cases: list[dict]) -> str:
    lines = [
        "# Бенчмарк предобработки запросов (100 пар вопрос–ответ)",
        "",
        "Набор собран **от чанка к вопросу**: для каждого кейса взят случайный фрагмент",
        "из `output/qdrant_chunks.json`, сформулирован вопрос по его тексту, эталон ответа —",
        "тот же фрагмент. **Главы ПУЭ исключены** из выборки.",
        "",
        "## Методика",
        "",
        f"- Случайная выборка: `random.seed({RANDOM_SEED})`, без повторов chunk_id.",
        f"- Минимальная длина текста чанка: {MIN_TEXT_LEN} символов.",
        "- Отфильтрованы библиография, преамбулы «Информация о введении…», обрывки с мусором.",
        "- Пересборка: `python scripts/build_query_benchmark_100.py`",
        "",
        "## Как использовать",
        "",
        "1. Прогнать вопрос через `plan_query` / `norm-lookup`.",
        "2. Проверить, что `expected_chunk_id` попадает в top-k retrieval.",
        "3. Сверить, что preprocessing не ломает ключевые слова из вопроса и фрагмента.",
        "",
        f"- Источник: `{CHUNKS_PATH.as_posix()}`",
        f"- Кейсов: {len(cases)}",
        "",
        "---",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"### {case['id']:03d} [{case['style']}]",
                "",
                f"**Вопрос:** {case['question']}",
                "",
                f"**Ожидаемый документ:** {case['expected_doc_name'] or '—'}",
            ]
        )
        if case["expected_point_number"]:
            lines.append(f"**Ожидаемый пункт:** {case['expected_point_number']}")
        if case["expected_heading"]:
            lines.append(f"**Раздел:** {case['expected_heading']}")
        lines.extend(
            [
                f"**Источник (файл):** {case['source_filename'] or '—'}",
                f"**Ожидаемый chunk_id:** `{case['expected_chunk_id']}`",
                "",
                "**Эталонный ответ (текст того же фрагмента):**",
                "",
                f"> {case['answer_excerpt']}",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    if not CHUNKS_PATH.is_file():
        raise SystemExit(f"Missing chunks: {CHUNKS_PATH}")

    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    eligible = select_eligible_chunks(chunks)
    if len(eligible) < CASE_COUNT:
        raise SystemExit(f"Not enough eligible chunks: {len(eligible)} < {CASE_COUNT}")

    rng = random.Random(RANDOM_SEED)
    selected = rng.sample(eligible, CASE_COUNT)

    cases: list[dict] = []
    for index, chunk in enumerate(selected, start=1):
        cases.append(build_case(index, chunk, variant=index))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render_markdown(cases), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(cases)} cases from {len(eligible)} eligible chunks)")
    styles: dict[str, int] = {}
    for case in cases:
        styles[case["style"]] = styles.get(case["style"], 0) + 1
    print("styles:", styles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
