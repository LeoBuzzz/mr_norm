"""Pipeline research: generate chunk-grounded Q&A corpus and evaluate norm_lookup (Polza).

Phases:
  generate — sample 50 fragments, ask Polza to formulate Q&A pairs
  eval     — run norm_lookup (balanced, limit=40, polza preset) + answer judge 1-10
             + final-evidence metrics (chunk/doc/point hit, per-chunk judge, bundle judge)
  report   — build markdown conclusions from eval JSON
  all      — run all phases sequentially
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mr_norm.apps.human_cli import apply_mode_preset
from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.runtime.llm_clients import LLMRequest, build_chat_client, parse_json_object
from mr_norm.runtime.llm_profiles import (
    POLZA_FINAL_ANSWER_FALLBACK_MODEL,
    POLZA_FINAL_ANSWER_MODEL,
    POLZA_PLANNER_FALLBACK_MODEL,
)
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup
from mr_norm.tools.chunker import load_chunks

CHUNKS_PATH = ROOT / "output" / "qdrant_chunks.json"
DEFAULT_DIR = ROOT / "reports" / "pipeline_research"
DEFAULT_CORPUS = DEFAULT_DIR / "corpus_50.json"
DEFAULT_EVAL = DEFAULT_DIR / "eval_results.json"
DEFAULT_EVAL_V3 = DEFAULT_DIR / "eval_results_v3.json"
DEFAULT_REPORT = DEFAULT_DIR / "REPORT.md"
FINAL_EVIDENCE_JUDGE_TOP_N = 12

CORPUS_SIZE = 50
RANDOM_SEED = 20260523
RANDOM_SEED_NATURAL = 20260524
MIN_TEXT_LEN = 120
MAX_PER_DOC = 2
GENERATION_MODEL = "deepseek/deepseek-v3.2"
GENERATION_FALLBACK = "anthropic/claude-sonnet-4.6"
JUDGE_MODEL = "deepseek/deepseek-v3.2"
JUDGE_FALLBACK = "anthropic/claude-sonnet-4.6"
BATCH_SIZE = 5

GENERATION_SYSTEM = """Ты эксперт по нормативным документам электроэнергетики РФ
(ФЗ об электроэнергетике, приказы и постановления Минэнерго, ПТЭ, ПУЭ, ГОСТ,
правила переключений, ОТ, ОДУ, технологическое присоединение и т.п.).

По фрагментам из индексированного корпуса составь пары «вопрос — эталонный ответ»
для тестирования RAG-системы mr_norm.

ЖЁСТКИЕ ПРАВИЛА:
1. Ответ должен опираться ТОЛЬКО на текст фрагмента — не добавляй факты из памяти.
2. Вопрос формулируй так, как его задал бы инженер/диспетчер/эксперт по нормативке.
3. Разнообразь типы: definition, point_lookup, requirement, procedure, factual, document_scope.
4. В ответе укажи ключевые числа, сроки, требования, определения из фрагмента.
5. Не ссылайся на «данный фрагмент» — ответ должен звучать как справка по норме.
6. Если в фрагменте только отсылка к другому пункту без содержания — пометь difficulty=hard
   и сформулируй вопрос об отсылке, не выдумывая определение.

Верни ТОЛЬКО JSON-объект (без markdown):
{
  "pairs": [
    {
      "id": "<chunk_id из входа>",
      "question": "...",
      "answer": "...",
      "question_type": "definition|point_lookup|requirement|procedure|factual|document_scope",
      "difficulty": "easy|medium|hard"
    }
  ]
}
Количество элементов в pairs = количеству фрагментов во входе."""

GENERATION_SYSTEM_NATURAL = """Ты эксперт по нормативным документам электроэнергетики РФ
(ФЗ об электроэнергетике, приказы и постановления Минэнерго, ПТЭ, ПУЭ, ГОСТ,
правила переключений, ОТ, ОДУ, технологическое присоединение и т.п.).

По фрагментам из индексированного корпуса составь пары «вопрос — эталонный ответ»
для тестирования RAG-системы mr_norm в режиме «естественный запрос пользователя».

ЖЁСТКИЕ ПРАВИЛА ДЛЯ ВОПРОСА:
1. Вопрос НЕ ДОЛЖЕН содержать: названия документов, номера приказов/постановлений/ФЗ/ГОСТ,
   ссылки «согласно …», «в соответствии с …», «пункт/подпункт/статья/раздел/приложение № …»,
   кавычки с названием акта, даты утверждения документа, doc_id, chunk_id.
2. Вопрос формулируй так, как его задал бы инженер/диспетчер, НЕ зная заранее, в каком документе
   спрятана норма — только по предмету, ситуации, требованию, процедуре или определению.
3. Допустимы предметные термины из текста (электроустановка, договор ресурсоснабжения, АПВ, ОДУ),
   но без привязки к конкретному номеру нормативного акта.
4. Ответ должен опираться ТОЛЬКО на текст фрагмента — не добавляй факты из памяти.
5. В ответе укажи ключевые числа, сроки, требования, определения из фрагмента; можно назвать
   документ/пункт в ответе, если это явно следует из фрагмента.
6. Разнообразь типы: definition, point_lookup, requirement, procedure, factual, document_scope.
7. Не ссылайся на «данный фрагмент» — ответ должен звучать как справка по норме.

Верни ТОЛЬКО JSON-объект (без markdown):
{
  "pairs": [
    {
      "id": "<chunk_id из входа>",
      "question": "...",
      "answer": "...",
      "question_type": "definition|point_lookup|requirement|procedure|factual|document_scope",
      "difficulty": "easy|medium|hard"
    }
  ]
}
Количество элементов в pairs = количеству фрагментов во входе."""

DOC_REFERENCE_PATTERNS = (
    re.compile(r"(?:№|n[oº\.]\s*)\s*\d", re.IGNORECASE),
    re.compile(r"\b(?:пункт|п\.|подпункт|статья|ст\.|раздел|приложение)\s*[\d_.]", re.IGNORECASE),
    re.compile(r"\b(?:гост|сто)\s*[\d\-–]", re.IGNORECASE),
    re.compile(r"\b\d+\s*-\s*фз\b", re.IGNORECASE),
    re.compile(r"postanovlen|prikaz|postanov|prikaz|minenergo|минэнерго|правительств", re.IGNORECASE),
    re.compile(r"согласно\s+", re.IGNORECASE),
    re.compile(r"в\s+соответствии\s+с\s+", re.IGNORECASE),
    re.compile(r"«[^»]{8,}»"),
)

JUDGE_SYSTEM = """Ты эксперт-оценщик ответов RAG-системы по нормативным документам электроэнергетики РФ.

Сравни фактический ответ системы с эталонным (из фрагмента корпуса).
Эталон может быть короче; фактический может содержать цитаты и дополнительный контекст.

Оцени качество по шкале 1–10:
  1–3 — неверно, противоречит эталону, ключевые факты отсутствуют или выдуманы
  4–5 — частично верно, но пропущены важные детали или неверна привязка к норме
  6–7 — в целом верно, есть мелкие пробелы или лишний шум
  8–9 — хорошо, сущность и ключевые факты совпадают с эталоном
  10 — полное покрытие эталона без противоречий

Верни ТОЛЬКО JSON:
{
  "score": 1-10,
  "equivalent": true|false,
  "reason": "кратко на русском",
  "dimensions": {
    "factual_accuracy": 1-10,
    "completeness": 1-10,
    "relevance": 1-10
  }
}"""

FINAL_EVIDENCE_JUDGE_SYSTEM = """Ты эксперт по оценке качества фрагментов (evidence), которые поступают в финальный ответчик RAG-системы по нормативным документам электроэнергетики РФ.

Тебе даны:
- вопрос пользователя;
- эталонный ответ (из gold chunk корпуса);
- список фрагментов evidence в порядке rank (top-N для финального ответчика).

Оцени КАЖДЫЙ фрагмент и набор в целом. Не оценивай стиль — только релевантность вопросу и полезность для ответа по эталону.

Для каждого chunk:
- relevance_score 1–10 (насколько фрагмент относится к вопросу)
- useful_for_answer: можно ли использовать для ответа по эталону
- issue: none | noise | wrong_doc | wrong_point | partial | cross_ref_only | duplicate

Шкала bundle score 1–10:
  1–3 — ключевых фактов эталона нет или они противоречат
  4–5 — частичное покрытие, критичные детали отсутствуют
  6–7 — в целом достаточно, есть мелкие пробелы
  8–9 — хорошее покрытие эталона
  10 — полное покрытие эталона фактами из evidence

Верни ТОЛЬКО JSON:
{
  "chunks": [
    {
      "chunk_id": "<из входа>",
      "relevance_score": 1-10,
      "useful_for_answer": true|false,
      "issue": "none|noise|wrong_doc|wrong_point|partial|cross_ref_only|duplicate"
    }
  ],
  "score": 1-10,
  "can_answer": true|false,
  "reason": "кратко на русском",
  "relevant_doc_present": true|false,
  "relevant_point_present": true|false
}"""


@dataclass
class CorpusItem:
    id: str
    question: str
    answer: str
    question_type: str
    difficulty: str
    chunk_id: str
    doc_id: str
    doc_name: str
    point_number: str
    source_filename: str
    answer_excerpt: str


@dataclass
class EvalEntry:
    id: str
    question: str
    expected_answer: str
    question_type: str
    difficulty: str
    chunk_id: str
    doc_id: str
    doc_name: str
    point_number: str
    actual_answer: str = ""
    evidence_count: int = 0
    chunk_in_evidence: bool = False
    chunk_rank: int | None = None
    doc_in_evidence: bool = False
    doc_rank: int | None = None
    point_in_evidence: bool = False
    point_rank: int | None = None
    gost_prefetch_count: int = 0
    resolved_doc_name: str = ""
    top_doc_names: list[str] = field(default_factory=list)
    top_evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    judge_score: int = 0
    judge_equivalent: bool = False
    judge_reason: str = ""
    judge_dimensions: dict[str, int] = field(default_factory=dict)
    judge_score_mismatch: bool = False
    evidence_judge_score: int = 0
    evidence_judge_can_answer: bool = False
    evidence_judge_reason: str = ""
    evidence_judge_relevant_doc: bool = False
    evidence_judge_relevant_point: bool = False
    final_evidence: list[dict[str, Any]] = field(default_factory=list)
    final_evidence_count: int = 0
    chunk_judgments: list[dict[str, Any]] = field(default_factory=list)
    useful_chunks_count: int = 0
    noise_chunks_count: int = 0
    gost_chunks_top5: int = 0
    gost_chunks_total: int = 0
    chunks_from_resolved_doc: int = 0
    unique_docs_in_final: int = 0
    mean_retrieval_score_top10: float = 0.0
    retrieval_limit: int = 0
    final_answer_limit: int = 0
    wide_pool_enabled: bool = False
    early_doc_resolver_applied: bool = False
    early_doc_resolver_reason: str = ""
    intent_routing_mode: str = ""
    intent_routing_tools: list[str] = field(default_factory=list)
    doc_point_boost_moves: int = 0
    retry_triggered: bool = False
    retry_reason: str = ""
    retry_items_added: int = 0
    source_ranks_summary: dict[str, Any] = field(default_factory=dict)
    pipeline_diagnostics: dict[str, Any] = field(default_factory=dict)
    stage_timings: dict[str, float] = field(default_factory=dict)
    tool_timings: dict[str, float] = field(default_factory=dict)
    error: str = ""


def resolve_keys_path() -> Path | None:
    keys_path = ProjectPaths.from_root(ROOT).root / "keys"
    return keys_path if keys_path.is_file() else None


def is_pue_chapter(payload: dict) -> bool:
    filename = (payload.get("filename") or "").lower()
    doc_name = (payload.get("doc_name") or "").lower()
    blob = f"{filename} {doc_name}"
    if "пуэ" not in blob and "правила устройства электроустановок" not in blob:
        return False
    if re.search(r"пуэ.*глава|глава\s*[\d.]", blob):
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
    return alpha < len(cleaned) * 0.45


def short(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def sample_chunks(
    chunks: list[dict],
    *,
    count: int = CORPUS_SIZE,
    random_seed: int = RANDOM_SEED,
    exclude_chunk_ids: set[str] | None = None,
) -> list[dict]:
    excluded = exclude_chunk_ids or set()
    eligible: list[dict] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        if chunk_id and chunk_id in excluded:
            continue
        payload = chunk.get("payload") or {}
        if is_pue_chapter(payload):
            continue
        text = str(chunk.get("text") or "")
        if is_low_quality_chunk(text):
            continue
        if not str(chunk.get("chunk_id") or "").strip():
            continue
        eligible.append(chunk)

    by_doc: dict[str, list[dict]] = defaultdict(list)
    for chunk in eligible:
        doc_id = str((chunk.get("payload") or {}).get("doc_id") or "unknown")
        by_doc[doc_id].append(chunk)

    rng = random.Random(random_seed)
    doc_ids = list(by_doc.keys())
    rng.shuffle(doc_ids)

    selected: list[dict] = []
    per_doc_count: Counter[str] = Counter()

    while len(selected) < count and doc_ids:
        progressed = False
        for doc_id in list(doc_ids):
            if len(selected) >= count:
                break
            if per_doc_count[doc_id] >= MAX_PER_DOC:
                continue
            bucket = by_doc[doc_id]
            if not bucket:
                continue
            chunk = bucket.pop(rng.randrange(len(bucket)))
            selected.append(chunk)
            per_doc_count[doc_id] += 1
            progressed = True
        doc_ids = [doc_id for doc_id in doc_ids if by_doc[doc_id] and per_doc_count[doc_id] < MAX_PER_DOC]
        if not progressed:
            break

    if len(selected) < count:
        remaining = [c for c in eligible if c not in selected]
        rng.shuffle(remaining)
        selected.extend(remaining[: count - len(selected)])

    return selected[:count]


def polza_json_chat(
    *,
    system: str,
    user_payload: Any,
    model: str,
    fallback_model: str,
    keys_path: Path | None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> Any:
    models = [item for item in (model, fallback_model, POLZA_FINAL_ANSWER_FALLBACK_MODEL) if item]
    errors: list[str] = []
    for current in dict.fromkeys(models):
        try:
            client = build_chat_client("polza", current, keys_path=keys_path)
            response = client.chat(
                LLMRequest(
                    messages=[
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": json.dumps(user_payload, ensure_ascii=False),
                        },
                    ],
                    model=current,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
            )
            return parse_json_object(response.content)
        except Exception as exc:
            errors.append(f"{current}: {exc}")
            time.sleep(2.0)
    raise RuntimeError("Polza chat failed: " + "; ".join(errors[:3]))


def fragment_payload(chunk: dict) -> dict[str, Any]:
    payload = chunk.get("payload") or {}
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "doc_id": str(payload.get("doc_id") or ""),
        "doc_name": str(payload.get("doc_name") or ""),
        "point_number": str(payload.get("point_number") or ""),
        "filename": str(payload.get("filename") or ""),
        "heading": str(payload.get("heading_path_text") or ""),
        "text": short(str(chunk.get("text") or ""), 1200),
    }


def question_has_doc_references(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return True
    return any(pattern.search(text) for pattern in DOC_REFERENCE_PATTERNS)


def fallback_natural_question(chunk: dict, *, question_type: str = "factual") -> str:
    text = re.sub(r"\s+", " ", str(chunk.get("text") or "")).strip()
    text = re.sub(r"^[\d._]+\s*", "", text)
    excerpt = short(text, 140).rstrip("?.…")
    templates = {
        "definition": f"Что означает или как определяется: {excerpt}?",
        "requirement": f"Какие требования установлены: {excerpt}?",
        "procedure": f"Какой порядок действий предусмотрен: {excerpt}?",
        "point_lookup": f"Какое значение или норма установлена: {excerpt}?",
        "document_scope": f"Что регламентируется в отношении: {excerpt}?",
    }
    question = templates.get(question_type, f"Какие правила или условия установлены: {excerpt}?")
    if question_has_doc_references(question):
        question = f"Какие нормативные требования описаны для следующей ситуации: {excerpt}?"
    return question


def load_exclude_chunk_ids(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item.get("chunk_id") or "") for item in data.get("items") or [] if item.get("chunk_id")}


def generate_corpus(
    *,
    chunks_path: Path,
    output_path: Path,
    keys_path: Path | None,
    count: int = CORPUS_SIZE,
    corpus_style: str = "explicit",
    random_seed: int | None = None,
    exclude_corpus: Path | None = None,
) -> dict[str, Any]:
    style = (corpus_style or "explicit").strip().lower()
    if style not in {"explicit", "natural"}:
        raise ValueError(f"unsupported corpus_style: {corpus_style}")
    seed = random_seed if random_seed is not None else (RANDOM_SEED_NATURAL if style == "natural" else RANDOM_SEED)
    generation_system = GENERATION_SYSTEM_NATURAL if style == "natural" else GENERATION_SYSTEM
    exclude_chunk_ids = load_exclude_chunk_ids(exclude_corpus)
    chunks = load_chunks(chunks_path)
    sampled = sample_chunks(chunks, count=count, random_seed=seed, exclude_chunk_ids=exclude_chunk_ids)
    items: list[CorpusItem] = []

    for batch_start in range(0, len(sampled), BATCH_SIZE):
        batch = sampled[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(sampled) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"[generate] batch {batch_num}/{total_batches} ({len(batch)} fragments)...", flush=True)

        payload = {
            "task": "create_qa_pairs_for_rag_benchmark",
            "domain": "Russian energy sector normative documents",
            "question_style": style,
            "fragments": [fragment_payload(chunk) for chunk in batch],
        }
        raw = polza_json_chat(
            system=generation_system,
            user_payload=payload,
            model=GENERATION_MODEL,
            fallback_model=GENERATION_FALLBACK,
            keys_path=keys_path,
            max_tokens=6000,
        )
        pairs = raw if isinstance(raw, list) else raw.get("pairs") or raw.get("items") or raw.get("qa_pairs") or []
        if isinstance(pairs, dict):
            pairs = list(pairs.values())

        by_chunk = {str(fragment_payload(c)["chunk_id"]): c for c in batch}
        used: set[str] = set()
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            chunk_id = str(pair.get("id") or pair.get("chunk_id") or "")
            chunk = by_chunk.get(chunk_id)
            if chunk is None or chunk_id in used:
                continue
            used.add(chunk_id)
            payload_meta = chunk.get("payload") or {}
            question = str(pair.get("question") or "").strip()
            answer = str(pair.get("answer") or "").strip()
            if not question or not answer:
                continue
            qtype = str(pair.get("question_type") or "factual")
            if style == "natural" and question_has_doc_references(question):
                question = fallback_natural_question(chunk, question_type=qtype)
            items.append(
                CorpusItem(
                    id=f"case_{len(items) + 1:03d}",
                    question=question,
                    answer=answer,
                    question_type=str(pair.get("question_type") or "factual"),
                    difficulty=str(pair.get("difficulty") or "medium"),
                    chunk_id=chunk_id,
                    doc_id=str(payload_meta.get("doc_id") or ""),
                    doc_name=str(payload_meta.get("doc_name") or ""),
                    point_number=str(payload_meta.get("point_number") or ""),
                    source_filename=str(payload_meta.get("filename") or ""),
                    answer_excerpt=short(str(chunk.get("text") or ""), 400),
                )
            )

        for chunk in batch:
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id in used:
                continue
            payload_meta = chunk.get("payload") or {}
            text = str(chunk.get("text") or "")
            point = str(payload_meta.get("point_number") or "")
            doc_name = short(str(payload_meta.get("doc_name") or ""), 60)
            qtype = "point_lookup" if point else "factual"
            if style == "natural":
                question = fallback_natural_question(chunk, question_type=qtype)
            else:
                question = (
                    f"Что установлено в пункте {point} документа «{doc_name}»?"
                    if point
                    else f"Какие требования содержит документ «{doc_name}» в приведённом фрагменте?"
                )
            items.append(
                CorpusItem(
                    id=f"case_{len(items) + 1:03d}",
                    question=question,
                    answer=short(text, 500),
                    question_type=qtype,
                    difficulty="medium",
                    chunk_id=chunk_id,
                    doc_id=str(payload_meta.get("doc_id") or ""),
                    doc_name=str(payload_meta.get("doc_name") or ""),
                    point_number=point,
                    source_filename=str(payload_meta.get("filename") or ""),
                    answer_excerpt=short(text, 400),
                )
            )
        time.sleep(1.0)

    items = items[:count]
    schema_version = "mr_pipeline_research_corpus_v2" if style == "natural" else "mr_pipeline_research_corpus_v1"
    result = {
        "schema_version": schema_version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "generation_model": GENERATION_MODEL,
        "question_style": style,
        "chunks_path": str(chunks_path),
        "random_seed": seed,
        "exclude_corpus": str(exclude_corpus) if exclude_corpus else "",
        "excluded_chunks": len(exclude_chunk_ids),
        "corpus_size": len(items),
        "items": [asdict(item) for item in items],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote corpus: {output_path} ({len(items)} items)")
    return result


def judge_answer(
    question: str,
    expected: str,
    actual: str,
    *,
    keys_path: Path | None,
) -> dict[str, Any]:
    if not actual.strip() or actual.startswith("ERROR:"):
        return {
            "score": 1,
            "equivalent": False,
            "reason": "пустой или ошибочный ответ",
            "dimensions": {"factual_accuracy": 1, "completeness": 1, "relevance": 1},
        }
    payload = polza_json_chat(
        system=JUDGE_SYSTEM,
        user_payload={
            "question": question,
            "expected_answer": expected[:4000],
            "actual_answer": actual[:6000],
        },
        model=JUDGE_MODEL,
        fallback_model=JUDGE_FALLBACK,
        keys_path=keys_path,
        max_tokens=512,
        temperature=0.0,
    )
    score = int(payload.get("score", 0))
    score = max(1, min(10, score))
    dims = payload.get("dimensions") or {}
    return {
        "score": score,
        "equivalent": bool(payload.get("equivalent")),
        "reason": str(payload.get("reason") or ""),
        "dimensions": {
            "factual_accuracy": int(dims.get("factual_accuracy") or score),
            "completeness": int(dims.get("completeness") or score),
            "relevance": int(dims.get("relevance") or score),
        },
    }


def normalize_doc_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalize_point_key(value: str) -> str:
    text = (value or "").strip().lower().replace("_", ".")
    return re.sub(r"\s+", "", text)


def points_match(expected: str, actual: str) -> bool:
    left = normalize_point_key(expected)
    right = normalize_point_key(actual)
    if not left or not right:
        return False
    return left == right or left.startswith(f"{right}.") or right.startswith(f"{left}.")


def doc_names_match(expected: str, actual: str) -> bool:
    left = normalize_doc_key(expected)
    right = normalize_doc_key(actual)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def analyze_retrieval_hits(
    evidence: list[RetrievedItem],
    *,
    chunk_id: str,
    doc_id: str,
    doc_name: str,
    point_number: str,
) -> dict[str, Any]:
    chunk_hit = False
    chunk_rank: int | None = None
    doc_hit = False
    doc_rank: int | None = None
    point_hit = False
    point_rank: int | None = None

    for index, item in enumerate(evidence, start=1):
        item_chunk = str(item.chunk_id or "")
        item_doc_id = str(item.doc_id or "")
        item_doc_name = str(item.doc_name or "")
        item_point = str(item.point_number or "")

        if not chunk_hit and chunk_id and item_chunk == chunk_id:
            chunk_hit = True
            chunk_rank = index
        if not doc_hit:
            same_doc = (doc_id and item_doc_id == doc_id) or doc_names_match(doc_name, item_doc_name)
            if same_doc:
                doc_hit = True
                doc_rank = index
        if not point_hit and point_number and points_match(point_number, item_point):
            same_doc = (doc_id and item_doc_id == doc_id) or doc_names_match(doc_name, item_doc_name)
            if same_doc or not doc_name:
                point_hit = True
                point_rank = index

    return {
        "chunk_in_evidence": chunk_hit,
        "chunk_rank": chunk_rank,
        "doc_in_evidence": doc_hit,
        "doc_rank": doc_rank,
        "point_in_evidence": point_hit,
        "point_rank": point_rank,
    }


def build_top_evidence_snapshot(
    evidence: list[RetrievedItem],
    *,
    limit: int = 10,
    text_limit: int = 320,
) -> list[dict[str, Any]]:
    return build_final_evidence_snapshot(evidence, limit=limit, text_limit=text_limit)


def build_final_evidence_snapshot(
    evidence: list[RetrievedItem],
    *,
    limit: int,
    text_limit: int = 320,
) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for index, item in enumerate(evidence[:limit], start=1):
        snapshot.append(
            {
                "rank": index,
                "chunk_id": str(item.chunk_id or ""),
                "doc_id": str(item.doc_id or ""),
                "doc_name": str(item.doc_name or ""),
                "point_number": str(item.point_number or ""),
                "source_tool": str(item.source_tool or ""),
                "score": item.score,
                "source_ranks": (item.matched or {}).get("source_ranks")
                if isinstance(getattr(item, "matched", None), dict)
                else None,
                "text": short(str(item.text or ""), text_limit),
            }
        )
    return snapshot


def is_gost_doc_name(doc_name: str) -> bool:
    lowered = normalize_doc_key(doc_name)
    return lowered.startswith("гост") or "гост р" in lowered


def analyze_final_evidence_deterministic(
    evidence: list[RetrievedItem],
    *,
    limit: int,
    resolved_doc_name: str,
) -> dict[str, Any]:
    final = list(evidence[:limit])
    top5 = final[:5]
    top10 = final[:10]
    scores = [float(item.score) for item in top10 if item.score is not None]
    resolved_doc = resolved_doc_name.strip()
    resolved_matches = 0
    if resolved_doc:
        for item in final:
            if doc_names_match(resolved_doc, str(item.doc_name or "")):
                resolved_matches += 1
    return {
        "final_evidence_count": len(final),
        "gost_chunks_top5": sum(1 for item in top5 if is_gost_doc_name(str(item.doc_name or ""))),
        "gost_chunks_total": sum(1 for item in final if is_gost_doc_name(str(item.doc_name or ""))),
        "chunks_from_resolved_doc": resolved_matches,
        "unique_docs_in_final": len({str(item.doc_name or "") for item in final if str(item.doc_name or "").strip()}),
        "mean_retrieval_score_top10": round(sum(scores) / len(scores), 4) if scores else 0.0,
    }


def normalize_chunk_judgments(
    raw_chunks: Any,
    *,
    evidence_snapshot: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("chunk_id") or ""): item for item in evidence_snapshot}
    allowed_issues = {
        "none",
        "noise",
        "wrong_doc",
        "wrong_point",
        "partial",
        "cross_ref_only",
        "duplicate",
    }
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_chunks, list):
        return normalized
    for raw in raw_chunks:
        if not isinstance(raw, dict):
            continue
        chunk_id = str(raw.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id not in by_id:
            continue
        source = by_id[chunk_id]
        issue = str(raw.get("issue") or "none").strip().lower()
        if issue not in allowed_issues:
            issue = "none"
        relevance = max(1, min(10, int(raw.get("relevance_score") or 0)))
        normalized.append(
            {
                "rank": int(source.get("rank") or 0),
                "chunk_id": chunk_id,
                "doc_name": str(source.get("doc_name") or ""),
                "point_number": str(source.get("point_number") or ""),
                "relevance_score": relevance,
                "useful_for_answer": bool(raw.get("useful_for_answer")),
                "issue": issue,
            }
        )
    normalized.sort(key=lambda item: int(item.get("rank") or 0))
    return normalized


def judge_final_evidence(
    question: str,
    expected: str,
    evidence_snapshot: list[dict[str, Any]],
    *,
    keys_path: Path | None,
    judge_top_n: int = FINAL_EVIDENCE_JUDGE_TOP_N,
) -> dict[str, Any]:
    judge_snapshot = evidence_snapshot[:judge_top_n]
    empty_verdict = {
        "score": 1,
        "can_answer": False,
        "reason": "evidence пуст",
        "relevant_doc_present": False,
        "relevant_point_present": False,
        "chunks": [],
        "useful_chunks_count": 0,
        "noise_chunks_count": 0,
    }
    if not judge_snapshot:
        return empty_verdict
    payload = polza_json_chat(
        system=FINAL_EVIDENCE_JUDGE_SYSTEM,
        user_payload={
            "question": question,
            "expected_answer": expected[:4000],
            "evidence": judge_snapshot,
            "note": f"Оцени каждый из {len(judge_snapshot)} фрагментов; это top-N для финального ответчика.",
        },
        model=JUDGE_MODEL,
        fallback_model=JUDGE_FALLBACK,
        keys_path=keys_path,
        max_tokens=2048,
        temperature=0.0,
    )
    score = max(1, min(10, int(payload.get("score", 0))))
    chunk_judgments = normalize_chunk_judgments(payload.get("chunks"), evidence_snapshot=judge_snapshot)
    useful_chunks_count = sum(1 for item in chunk_judgments if item.get("useful_for_answer"))
    noise_chunks_count = sum(
        1 for item in chunk_judgments if str(item.get("issue") or "") in {"noise", "wrong_doc", "duplicate"}
    )
    return {
        "score": score,
        "can_answer": bool(payload.get("can_answer")),
        "reason": str(payload.get("reason") or ""),
        "relevant_doc_present": bool(payload.get("relevant_doc_present")),
        "relevant_point_present": bool(payload.get("relevant_point_present")),
        "chunks": chunk_judgments,
        "useful_chunks_count": useful_chunks_count,
        "noise_chunks_count": noise_chunks_count,
    }


def judge_evidence(
    question: str,
    expected: str,
    evidence_snapshot: list[dict[str, Any]],
    *,
    keys_path: Path | None,
) -> dict[str, Any]:
    verdict = judge_final_evidence(
        question,
        expected,
        evidence_snapshot,
        keys_path=keys_path,
    )
    return {
        "score": verdict["score"],
        "can_answer": verdict["can_answer"],
        "reason": verdict["reason"],
        "relevant_doc_present": verdict["relevant_doc_present"],
        "relevant_point_present": verdict["relevant_point_present"],
        "chunks": verdict["chunks"],
        "useful_chunks_count": verdict["useful_chunks_count"],
        "noise_chunks_count": verdict["noise_chunks_count"],
    }


def detect_judge_score_mismatch(score: int, equivalent: bool, reason: str) -> bool:
    reason_lower = reason.lower()
    if equivalent and score <= 3:
        return True
    if "ошибка в оценке" in reason_lower or "оценка должна быть" in reason_lower:
        return True
    return False


def apply_pipeline_diagnostics(entry: EvalEntry, result: Any) -> None:
    trace = getattr(result, "trace", None)
    diagnostics = dict(getattr(trace, "pipeline_diagnostics", None) or {})
    if not diagnostics and getattr(result, "pipeline", None) is not None:
        diagnostics = dict(getattr(result.pipeline, "diagnostics", None) or {})
    entry.pipeline_diagnostics = diagnostics
    entry.retrieval_limit = int(diagnostics.get("retrieval_limit") or getattr(trace, "retrieval_limit", 0) or 0)
    entry.final_answer_limit = int(
        diagnostics.get("final_answer_limit") or getattr(trace, "final_answer_limit", 0) or 0
    )
    entry.wide_pool_enabled = bool(diagnostics.get("wide_pool_enabled"))
    entry.early_doc_resolver_applied = bool(diagnostics.get("early_doc_resolver_applied"))
    entry.early_doc_resolver_reason = str(diagnostics.get("early_doc_resolver_reason") or "")
    entry.intent_routing_mode = str(diagnostics.get("intent_routing_mode") or "")
    entry.intent_routing_tools = list(diagnostics.get("intent_routing_tools") or [])
    entry.doc_point_boost_moves = int(diagnostics.get("doc_point_boost_moves") or 0)
    entry.retry_triggered = bool(diagnostics.get("retry_triggered") or getattr(trace, "retry_triggered", False))
    entry.retry_reason = str(diagnostics.get("retry_reason") or getattr(trace, "retry_reason", "") or "")
    entry.retry_items_added = int(diagnostics.get("retry_items_added") or 0)
    entry.source_ranks_summary = dict(diagnostics.get("source_ranks_summary") or {})
    entry.stage_timings = dict(diagnostics.get("stage_timings") or {})
    entry.tool_timings = dict(diagnostics.get("tool_timings") or {})


def apply_retrieval_metrics(
    entry: EvalEntry,
    evidence: list[RetrievedItem],
    *,
    keys_path: Path | None,
    skip_evidence_judge: bool,
    evidence_limit: int,
    resolved_doc_name: str = "",
) -> None:
    hits = analyze_retrieval_hits(
        evidence,
        chunk_id=entry.chunk_id,
        doc_id=entry.doc_id,
        doc_name=entry.doc_name,
        point_number=entry.point_number,
    )
    entry.chunk_in_evidence = hits["chunk_in_evidence"]
    entry.chunk_rank = hits["chunk_rank"]
    entry.doc_in_evidence = hits["doc_in_evidence"]
    entry.doc_rank = hits["doc_rank"]
    entry.point_in_evidence = hits["point_in_evidence"]
    entry.point_rank = hits["point_rank"]
    entry.final_evidence = build_final_evidence_snapshot(evidence, limit=evidence_limit)
    entry.top_evidence = entry.final_evidence[:10]
    entry.top_doc_names = [item["doc_name"] for item in entry.final_evidence[:5] if item.get("doc_name")]

    deterministic = analyze_final_evidence_deterministic(
        evidence,
        limit=evidence_limit,
        resolved_doc_name=resolved_doc_name or entry.resolved_doc_name,
    )
    entry.final_evidence_count = int(deterministic["final_evidence_count"])
    entry.gost_chunks_top5 = int(deterministic["gost_chunks_top5"])
    entry.gost_chunks_total = int(deterministic["gost_chunks_total"])
    entry.chunks_from_resolved_doc = int(deterministic["chunks_from_resolved_doc"])
    entry.unique_docs_in_final = int(deterministic["unique_docs_in_final"])
    entry.mean_retrieval_score_top10 = float(deterministic["mean_retrieval_score_top10"])

    if skip_evidence_judge:
        return
    evidence_verdict = judge_final_evidence(
        entry.question,
        entry.expected_answer,
        entry.final_evidence,
        keys_path=keys_path,
    )
    entry.evidence_judge_score = evidence_verdict["score"]
    entry.evidence_judge_can_answer = evidence_verdict["can_answer"]
    entry.evidence_judge_reason = evidence_verdict["reason"]
    entry.evidence_judge_relevant_doc = evidence_verdict["relevant_doc_present"]
    entry.evidence_judge_relevant_point = evidence_verdict["relevant_point_present"]
    entry.chunk_judgments = list(evidence_verdict["chunks"])
    entry.useful_chunks_count = int(evidence_verdict["useful_chunks_count"])
    entry.noise_chunks_count = int(evidence_verdict["noise_chunks_count"])


def _entry_from_cached(cached: dict[str, Any], *, corpus_item: dict[str, Any] | None = None) -> EvalEntry:
    merged = dict(cached)
    fallback = corpus_item or {}
    for key in EvalEntry.__dataclass_fields__:
        if key not in merged or merged[key] is None:
            if key in fallback:
                merged[key] = fallback[key]
            elif key in {"top_doc_names", "top_evidence", "final_evidence", "chunk_judgments", "warnings", "judge_dimensions"}:
                merged[key] = []
            elif key in {"chunk_rank", "doc_rank", "point_rank"}:
                merged[key] = None
            elif key in {"elapsed_sec", "mean_retrieval_score_top10"}:
                merged[key] = 0.0
            elif key.endswith("_count") or key in {"judge_score", "evidence_judge_score", "gost_prefetch_count"}:
                merged[key] = 0
            elif key.startswith("judge_") or key.startswith("evidence_judge_") or key.startswith("chunk_in_") or key.startswith("doc_in_") or key.startswith("point_in_"):
                merged[key] = False
            else:
                merged[key] = ""
    return EvalEntry(**{k: merged[k] for k in EvalEntry.__dataclass_fields__})


def _has_final_evidence_quality(cached: dict[str, Any], *, skip_evidence_judge: bool) -> bool:
    if skip_evidence_judge:
        return bool(cached.get("final_evidence"))
    return (
        "evidence_judge_score" in cached
        and bool(cached.get("final_evidence"))
        and "chunk_judgments" in cached
    )


def _run_norm_lookup_for_entry(
    entry: EvalEntry,
    *,
    question: str,
    expected: str,
    limit: int,
    profile: str,
    preset: dict[str, Any],
    config: IndexingConfig,
    keys_path: Path | None,
    skip_evidence_judge: bool,
    run_answer_judge: bool,
) -> None:
    t0 = time.perf_counter()
    request = NormLookupRequest(
        query=question,
        limit=limit,
        profile=profile,
        understand_query_mode=str(preset["understand_query_mode"]),
        planner_backend=str(preset["planner_backend"]),
        reranker_backend=str(preset["reranker_backend"]),
        final_answer_backend=str(preset["final_answer_backend"]),
        llm_provider=str(preset["llm_provider"]),
        enable_pue_aliases=False,
    )
    try:
        result = run_norm_lookup(request, config, keys_path=keys_path)
        if run_answer_judge or not entry.actual_answer:
            entry.actual_answer = result.answer
        entry.evidence_count = len(result.evidence)
        entry.gost_prefetch_count = result.trace.gost_prefetch_count
        entry.warnings = list(result.warnings)[:15]
        if result.understanding and result.understanding.resolved_doc_names:
            entry.resolved_doc_name = result.understanding.resolved_doc_names[0]
        apply_pipeline_diagnostics(entry, result)
        ranked_evidence = list(result.pipeline.rerank.items) if result.pipeline else list(result.evidence)
        t_evidence = time.perf_counter()
        apply_retrieval_metrics(
            entry,
            ranked_evidence,
            keys_path=keys_path,
            skip_evidence_judge=skip_evidence_judge,
            evidence_limit=entry.retrieval_limit or limit,
            resolved_doc_name=entry.resolved_doc_name,
        )
        if not skip_evidence_judge:
            entry.stage_timings = {
                **entry.stage_timings,
                "evidence_judge_sec": round(time.perf_counter() - t_evidence, 3),
            }
        if run_answer_judge:
            t_answer = time.perf_counter()
            verdict = judge_answer(question, expected, entry.actual_answer, keys_path=keys_path)
            entry.stage_timings = {
                **entry.stage_timings,
                "answer_judge_sec": round(time.perf_counter() - t_answer, 3),
            }
            entry.judge_score = verdict["score"]
            entry.judge_equivalent = verdict["equivalent"]
            entry.judge_reason = verdict["reason"]
            entry.judge_dimensions = verdict["dimensions"]
            entry.judge_score_mismatch = detect_judge_score_mismatch(
                entry.judge_score,
                entry.judge_equivalent,
                entry.judge_reason,
            )
    except Exception as exc:
        entry.error = f"{type(exc).__name__}: {exc}"
        if run_answer_judge or not entry.actual_answer:
            entry.actual_answer = f"ERROR: {entry.error}"
        if run_answer_judge or not entry.judge_score:
            entry.judge_score = 1
            entry.judge_reason = entry.error
    entry.elapsed_sec = round(time.perf_counter() - t0, 2)
    entry.stage_timings = {
        **entry.stage_timings,
        "eval_total_sec": entry.elapsed_sec,
    }


def run_eval(
    corpus_path: Path,
    *,
    output_path: Path,
    keys_path: Path | None,
    limit: int = 40,
    profile: str = "balanced",
    reuse_path: Path | None = None,
    skip_evidence_judge: bool = False,
    judge_only: bool = False,
    retrieval_only: bool = False,
    question_type: str | None = None,
    max_cases: int | None = None,
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    items: list[dict] = list(corpus.get("items") or [])
    if question_type:
        items = [item for item in items if str(item.get("question_type") or "") == question_type]
    if case_ids:
        items = [item for item in items if str(item.get("id") or "") in case_ids]
    if max_cases is not None and max_cases > 0:
        items = items[:max_cases]

    reuse: dict[str, dict] = {}
    if reuse_path and reuse_path.is_file():
        prior = json.loads(reuse_path.read_text(encoding="utf-8"))
        for entry in prior.get("entries") or []:
            reuse[str(entry.get("id") or "")] = entry

    preset = apply_mode_preset("polza")
    config = IndexingConfig.from_env()
    run_case_ids = {str(item.get("id") or "") for item in items}
    entry_by_id: dict[str, dict] = {
        case_id: dict(cached)
        for case_id, cached in reuse.items()
        if case_id and case_id not in run_case_ids
    }
    score_sum = sum(int(entry.get("judge_score") or 0) for entry in entry_by_id.values())
    chunk_hit = sum(1 for entry in entry_by_id.values() if entry.get("chunk_in_evidence"))
    started = time.perf_counter()

    for index, item in enumerate(items, start=1):
        case_id = str(item.get("id") or f"case_{index:03d}")
        question = str(item.get("question") or "").strip()
        expected = str(item.get("answer") or "").strip()
        chunk_id = str(item.get("chunk_id") or "")
        doc_id = str(item.get("doc_id") or "")
        safe = question[:65].encode("cp1251", errors="replace").decode("cp1251")
        print(f"[eval {index}/{len(items)}] {safe}...", flush=True)

        cached = reuse.get(case_id)
        cached_complete = (
            cached
            and cached.get("actual_answer")
            and not cached.get("error")
            and "doc_in_evidence" in cached
            and _has_final_evidence_quality(cached, skip_evidence_judge=skip_evidence_judge)
        )

        if retrieval_only and cached and cached.get("actual_answer") and not cached.get("error"):
            entry = _entry_from_cached(cached, corpus_item=item)
            if not entry.doc_id and doc_id:
                entry.doc_id = doc_id
            print(f"  [retrieval-only] refresh evidence + chunk judge", flush=True)
            _run_norm_lookup_for_entry(
                entry,
                question=question,
                expected=expected,
                limit=limit,
                profile=profile,
                preset=preset,
                config=config,
                keys_path=keys_path,
                skip_evidence_judge=skip_evidence_judge,
                run_answer_judge=False,
            )
        elif cached_complete and not judge_only and not retrieval_only:
            entry = _entry_from_cached(cached, corpus_item=item)
        elif (
            cached
            and cached.get("actual_answer")
            and not cached.get("error")
            and (cached.get("final_evidence") or cached.get("top_evidence"))
            and not skip_evidence_judge
            and not _has_final_evidence_quality(cached, skip_evidence_judge=False)
            and not judge_only
            and not retrieval_only
        ):
            entry = _entry_from_cached(cached, corpus_item=item)
            snapshot = list(entry.final_evidence or entry.top_evidence)
            if not entry.final_evidence:
                entry.final_evidence = snapshot
            evidence_verdict = judge_final_evidence(
                question,
                expected,
                snapshot,
                keys_path=keys_path,
            )
            entry.evidence_judge_score = evidence_verdict["score"]
            entry.evidence_judge_can_answer = evidence_verdict["can_answer"]
            entry.evidence_judge_reason = evidence_verdict["reason"]
            entry.evidence_judge_relevant_doc = evidence_verdict["relevant_doc_present"]
            entry.evidence_judge_relevant_point = evidence_verdict["relevant_point_present"]
            entry.chunk_judgments = list(evidence_verdict["chunks"])
            entry.useful_chunks_count = int(evidence_verdict["useful_chunks_count"])
            entry.noise_chunks_count = int(evidence_verdict["noise_chunks_count"])
            if entry.judge_score:
                entry.judge_score_mismatch = detect_judge_score_mismatch(
                    entry.judge_score,
                    entry.judge_equivalent,
                    entry.judge_reason,
                )
        elif cached and cached.get("actual_answer") and not cached.get("error") and judge_only:
            entry = _entry_from_cached(cached, corpus_item=item)
            if not entry.judge_score:
                verdict = judge_answer(question, expected, entry.actual_answer, keys_path=keys_path)
                entry.judge_score = verdict["score"]
                entry.judge_equivalent = verdict["equivalent"]
                entry.judge_reason = verdict["reason"]
                entry.judge_dimensions = verdict["dimensions"]
                entry.judge_score_mismatch = detect_judge_score_mismatch(
                    entry.judge_score,
                    entry.judge_equivalent,
                    entry.judge_reason,
                )
        else:
            entry = EvalEntry(
                id=case_id,
                question=question,
                expected_answer=expected,
                question_type=str(item.get("question_type") or ""),
                difficulty=str(item.get("difficulty") or ""),
                chunk_id=chunk_id,
                doc_id=doc_id,
                doc_name=str(item.get("doc_name") or ""),
                point_number=str(item.get("point_number") or ""),
            )
            _run_norm_lookup_for_entry(
                entry,
                question=question,
                expected=expected,
                limit=limit,
                profile=profile,
                preset=preset,
                config=config,
                keys_path=keys_path,
                skip_evidence_judge=skip_evidence_judge,
                run_answer_judge=True,
            )

        score_sum += entry.judge_score
        if entry.chunk_in_evidence:
            chunk_hit += 1
        entry_by_id[case_id] = asdict(entry)
        entries = sorted(entry_by_id.values(), key=lambda row: str(row.get("id") or ""))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = {
            "schema_version": "mr_pipeline_research_eval_v3",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "corpus_path": str(corpus_path),
            "run_config": {
                "profile": profile,
                "limit": limit,
                "mode_preset": "polza",
                "understand_query_mode": preset["understand_query_mode"],
                "llm_provider": preset["llm_provider"],
                "final_answer_backend": preset["final_answer_backend"],
                "enable_pue_aliases": False,
                "skip_evidence_judge": skip_evidence_judge,
                "retrieval_only": retrieval_only,
                "final_evidence_judge_top_n": FINAL_EVIDENCE_JUDGE_TOP_N,
                "pipeline_enhancements": [
                    "early_doc_resolver",
                    "intent_tool_routing",
                    "wide_pool_narrow_final_answer",
                    "doc_point_rerank_boost",
                    "doc_point_retry",
                    "doc_scoped_payload_retry",
                    "select_final_answer_evidence",
                    "source_ranks_logging",
                    "final_answer_v4",
                    "routing_hybrid_vector",
                    "requirement_query_alias",
                    "doc_point_boost_gated",
                    "stage_timings",
                ],
            },
            "judge_model": JUDGE_MODEL,
            "entries_total": len(items),
            "entries_completed": len(entries),
            "metrics": _compute_metrics(entries),
            "entries": entries,
        }
        output_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.5)

    elapsed = round(time.perf_counter() - started, 1)
    report = {
        "schema_version": "mr_pipeline_research_eval_v3",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus_path": str(corpus_path),
        "run_config": partial["run_config"],
        "judge_model": JUDGE_MODEL,
        "entries_total": len(entries),
        "metrics": {**partial["metrics"], "elapsed_sec": elapsed},
        "entries": entries,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"Wrote eval: {output_path}")
    return report


def _compute_metrics(entries: list[dict]) -> dict[str, Any]:
    total = len(entries)
    if not total:
        return {}
    scores = [int(e.get("judge_score") or 0) for e in entries]
    evidence_scores = [int(e.get("evidence_judge_score") or 0) for e in entries if e.get("evidence_judge_score")]
    with_point = [e for e in entries if str(e.get("point_number") or "").strip()]
    by_type: dict[str, list[int]] = defaultdict(list)
    by_diff: dict[str, list[int]] = defaultdict(list)
    for entry in entries:
        by_type[str(entry.get("question_type") or "unknown")].append(int(entry.get("judge_score") or 0))
        by_diff[str(entry.get("difficulty") or "unknown")].append(int(entry.get("judge_score") or 0))

    metrics = {
        "mean_judge_score": round(sum(scores) / total, 2),
        "median_judge_score": sorted(scores)[total // 2],
        "score_ge_7_rate": round(sum(1 for s in scores if s >= 7) / total, 3),
        "score_ge_8_rate": round(sum(1 for s in scores if s >= 8) / total, 3),
        "equivalent_rate": round(sum(1 for e in entries if e.get("judge_equivalent")) / total, 3),
        "judge_mismatch_rate": round(sum(1 for e in entries if e.get("judge_score_mismatch")) / total, 3),
        "chunk_in_evidence_rate": round(sum(1 for e in entries if e.get("chunk_in_evidence")) / total, 3),
        "doc_in_evidence_rate": round(sum(1 for e in entries if e.get("doc_in_evidence")) / total, 3),
        "mean_evidence_count": round(sum(int(e.get("evidence_count") or 0) for e in entries) / total, 1),
        "errors": sum(1 for e in entries if e.get("error")),
        "by_question_type": {
            key: round(sum(vals) / len(vals), 2) for key, vals in sorted(by_type.items())
        },
        "by_difficulty": {
            key: round(sum(vals) / len(vals), 2) for key, vals in sorted(by_diff.items())
        },
        "score_histogram": dict(Counter(scores)),
    }
    if with_point:
        metrics["point_in_evidence_rate"] = round(
            sum(1 for e in with_point if e.get("point_in_evidence")) / len(with_point),
            3,
        )
    if evidence_scores:
        metrics["mean_evidence_judge_score"] = round(sum(evidence_scores) / len(evidence_scores), 2)
        metrics["evidence_can_answer_rate"] = round(
            sum(1 for e in entries if e.get("evidence_judge_can_answer")) / total,
            3,
        )
        metrics["mean_useful_chunks"] = round(
            sum(int(e.get("useful_chunks_count") or 0) for e in entries) / total,
            2,
        )
        metrics["mean_noise_chunks"] = round(
            sum(int(e.get("noise_chunks_count") or 0) for e in entries) / total,
            2,
        )
    gost_rates = [int(e.get("gost_chunks_top5") or 0) for e in entries if "gost_chunks_top5" in e]
    if gost_rates:
        metrics["mean_gost_chunks_top5"] = round(sum(gost_rates) / total, 2)
    resolved_rates = [int(e.get("chunks_from_resolved_doc") or 0) for e in entries if "chunks_from_resolved_doc" in e]
    if resolved_rates:
        metrics["mean_chunks_from_resolved_doc"] = round(sum(resolved_rates) / total, 2)
    if any("early_doc_resolver_applied" in e for e in entries):
        metrics["early_doc_resolver_rate"] = round(
            sum(1 for e in entries if e.get("early_doc_resolver_applied")) / total,
            3,
        )
        metrics["retry_trigger_rate"] = round(
            sum(1 for e in entries if e.get("retry_triggered")) / total,
            3,
        )
        metrics["wide_pool_rate"] = round(
            sum(1 for e in entries if e.get("wide_pool_enabled")) / total,
            3,
        )
        metrics["mean_doc_point_boost_moves"] = round(
            sum(int(e.get("doc_point_boost_moves") or 0) for e in entries) / total,
            2,
        )
        metrics["mean_retry_items_added"] = round(
            sum(int(e.get("retry_items_added") or 0) for e in entries) / total,
            2,
        )
    stage_keys: set[str] = set()
    for entry in entries:
        stage_keys.update((entry.get("stage_timings") or {}).keys())
    if stage_keys:
        mean_stage: dict[str, float] = {}
        for key in sorted(stage_keys):
            values = [float((entry.get("stage_timings") or {}).get(key) or 0.0) for entry in entries]
            mean_stage[key] = round(sum(values) / total, 3)
        metrics["mean_stage_timings"] = mean_stage
    tool_keys: set[str] = set()
    for entry in entries:
        tool_keys.update((entry.get("tool_timings") or {}).keys())
    if tool_keys:
        mean_tools: dict[str, float] = {}
        for key in sorted(tool_keys):
            values = [float((entry.get("tool_timings") or {}).get(key) or 0.0) for entry in entries]
            mean_tools[key] = round(sum(values) / total, 3)
        metrics["mean_tool_timings"] = mean_tools
    return metrics


def build_report_md(eval_path: Path, *, output_path: Path) -> str:
    data = json.loads(eval_path.read_text(encoding="utf-8"))
    metrics = data.get("metrics") or {}
    entries: list[dict] = list(data.get("entries") or [])
    run_cfg = data.get("run_config") or {}

    worst = sorted(entries, key=lambda e: int(e.get("judge_score") or 0))[:8]
    best = sorted(entries, key=lambda e: int(e.get("judge_score") or 0), reverse=True)[:5]
    no_chunk = [e for e in entries if not e.get("chunk_in_evidence")]
    low_score = [e for e in entries if int(e.get("judge_score") or 0) <= 5]

    warning_counter: Counter[str] = Counter()
    for entry in entries:
        for warning in entry.get("warnings") or []:
            key = warning[:80]
            warning_counter[key] += 1

    lines = [
        "# Исследование пайплайна mr_norm (50 Q&A из корпуса)",
        "",
        f"Дата: {data.get('created_at', '')}",
        f"Корпус: `{data.get('corpus_path', '')}`",
        "",
        "## Конфигурация прогона",
        "",
        f"- Режим: **polza** (`understand_query={run_cfg.get('understand_query_mode')}`, `final_answer={run_cfg.get('final_answer_backend')}`)",
        f"- Профиль: **{run_cfg.get('profile')}**, limit evidence: **{run_cfg.get('limit')}**",
        f"- Судья: `{data.get('judge_model', JUDGE_MODEL)}`, шкала **1–10**",
        f"- Кейсов: **{data.get('entries_total', len(entries))}**",
        "",
        "## Сводные метрики",
        "",
        "| Метрика | Значение |",
        "|---------|----------|",
        f"| Средний score | **{metrics.get('mean_judge_score', '—')}** / 10 |",
        f"| Медиана | {metrics.get('median_judge_score', '—')} |",
        f"| Доля score ≥ 7 | {metrics.get('score_ge_7_rate', '—')} |",
        f"| Доля score ≥ 8 | {metrics.get('score_ge_8_rate', '—')} |",
        f"| Эквивалентность (judge) | {metrics.get('equivalent_rate', '—')} |",
        f"| Эталонный chunk в evidence | {metrics.get('chunk_in_evidence_rate', '—')} |",
        f"| Тот же doc (любой chunk) | {metrics.get('doc_in_evidence_rate', '—')} |",
        f"| Тот же point в doc | {metrics.get('point_in_evidence_rate', '—')} |",
        f"| Evidence judge: can_answer | {metrics.get('evidence_can_answer_rate', '—')} |",
        f"| Средний evidence judge score | {metrics.get('mean_evidence_judge_score', '—')} |",
        f"| Полезных chunks (top-{FINAL_EVIDENCE_JUDGE_TOP_N}, judge) | {metrics.get('mean_useful_chunks', '—')} |",
        f"| Шумных chunks (top-{FINAL_EVIDENCE_JUDGE_TOP_N}, judge) | {metrics.get('mean_noise_chunks', '—')} |",
        f"| ГОСТ в top-5 final evidence | {metrics.get('mean_gost_chunks_top5', '—')} |",
        f"| Chunks из resolved doc | {metrics.get('mean_chunks_from_resolved_doc', '—')} |",
        f"| Early doc resolver | {metrics.get('early_doc_resolver_rate', '—')} |",
        f"| Doc/point retry triggered | {metrics.get('retry_trigger_rate', '—')} |",
        f"| Wide pool (2x recall) | {metrics.get('wide_pool_rate', '—')} |",
        f"| Mean doc/point boost moves | {metrics.get('mean_doc_point_boost_moves', '—')} |",
        f"| Расхождение answer judge (mismatch) | {metrics.get('judge_mismatch_rate', '—')} |",
        f"| Среднее число фрагментов | {metrics.get('mean_evidence_count', '—')} |",
        f"| Ошибки runtime | {metrics.get('errors', 0)} |",
        f"| Время прогона (с) | {metrics.get('elapsed_sec', '—')} |",
        "",
    ]
    mean_stage = metrics.get("mean_stage_timings") or {}
    if mean_stage:
        lines.extend(
            [
                "### Среднее время по этапам (с)",
                "",
                "| Этап | с |",
                "|------|---|",
            ]
        )
        for key, value in mean_stage.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")
    mean_tools = metrics.get("mean_tool_timings") or {}
    if mean_tools:
        lines.extend(
            [
                "### Среднее время по retrieval tools (с)",
                "",
                "| Tool | с |",
                "|------|---|",
            ]
        )
        for key, value in mean_tools.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")
    lines.extend(
        [
            "### По типу вопроса",
            "",
        ]
    )
    for qtype, avg in sorted((metrics.get("by_question_type") or {}).items()):
        lines.append(f"- **{qtype}**: средний score {avg}")

    lines.extend(["", "### По сложности", ""])
    for diff, avg in sorted((metrics.get("by_difficulty") or {}).items()):
        lines.append(f"- **{diff}**: средний score {avg}")

    lines.extend(["", "### Распределение score", ""])
    for score, count in sorted((metrics.get("score_histogram") or {}).items()):
        lines.append(f"- {score}: {count} кейсов")

    lines.extend(["", "## Лучшие кейсы (score ≥ 8)", ""])
    for entry in best:
        if int(entry.get("judge_score") or 0) < 8:
            continue
        lines.append(
            f"- **{entry.get('id')}** (score={entry.get('judge_score')}, type={entry.get('question_type')}): "
            f"{short(entry.get('question', ''), 90)}"
        )

    lines.extend(["", "## Худшие кейсы (score ≤ 5)", ""])
    for entry in low_score[:10]:
        lines.append(f"### {entry.get('id')} — score {entry.get('judge_score')}")
        lines.append(f"- **Вопрос:** {entry.get('question')}")
        lines.append(f"- **Эталон:** {short(entry.get('expected_answer', ''), 200)}")
        lines.append(f"- **Ответ:** {short(entry.get('actual_answer', ''), 200)}")
        lines.append(f"- **Причина:** {entry.get('judge_reason')}")
        lines.append(
            f"- chunk={entry.get('chunk_in_evidence')} (rank={entry.get('chunk_rank')}), "
            f"doc={entry.get('doc_in_evidence')} (rank={entry.get('doc_rank')}), "
            f"point={entry.get('point_in_evidence')} (rank={entry.get('point_rank')}), "
            f"evidence_judge={entry.get('evidence_judge_score')}, "
            f"resolved_doc={entry.get('resolved_doc_name') or '—'}"
        )
        lines.append("")

    lines.extend(["", "## Retrieval: детальная диагностика", ""])
    lines.append(
        f"- **chunk hit** (exact chunk_id): {metrics.get('chunk_in_evidence_rate', '—')}"
    )
    lines.append(
        f"- **doc hit** (любой chunk того же doc_id/doc_name): {metrics.get('doc_in_evidence_rate', '—')}"
    )
    if metrics.get("point_in_evidence_rate") is not None:
        lines.append(
            f"- **point hit** (point_number в том же doc): {metrics.get('point_in_evidence_rate')}"
        )
    if metrics.get("mean_evidence_judge_score") is not None:
        lines.append(
            f"- **final evidence judge** (top-{FINAL_EVIDENCE_JUDGE_TOP_N} per-chunk + bundle): "
            f"mean={metrics.get('mean_evidence_judge_score')}, "
            f"can_answer={metrics.get('evidence_can_answer_rate')}, "
            f"useful_chunks={metrics.get('mean_useful_chunks')}, "
            f"noise_chunks={metrics.get('mean_noise_chunks')}"
        )
    mismatch = [e for e in entries if e.get("judge_score_mismatch")]
    if mismatch:
        lines.append(f"- **answer judge mismatch** (equivalent vs low score): {len(mismatch)} кейсов")
        for entry in mismatch[:5]:
            lines.append(
                f"  - {entry.get('id')}: score={entry.get('judge_score')}, "
                f"equivalent={entry.get('judge_equivalent')}"
            )

    lines.extend(["", "## Retrieval: эталонный chunk не найден", ""])
    lines.append(f"Кейсов без эталонного chunk_id в evidence: **{len(no_chunk)}** / {len(entries)}")
    for entry in no_chunk[:8]:
        lines.append(
            f"- {entry.get('id')} (answer score={entry.get('judge_score')}, "
            f"doc_hit={entry.get('doc_in_evidence')}, evidence_score={entry.get('evidence_judge_score')}): "
            f"{short(entry.get('question', ''), 80)}"
        )

    lines.extend(["", "## Частые предупреждения пайплайна", ""])
    for warning, count in warning_counter.most_common(8):
        lines.append(f"- ({count}×) {warning}")

    lines.extend(
        [
            "",
            "## Выводы",
            "",
            _build_conclusions(metrics, entries, no_chunk, low_score),
            "",
            "## Предложения по улучшению пайплайна",
            "",
            _build_recommendations(metrics, entries, no_chunk, low_score, warning_counter),
            "",
            f"Полные данные: `{eval_path.as_posix()}`",
        ]
    )

    text = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Wrote report: {output_path}")
    return text


def _build_conclusions(
    metrics: dict[str, Any],
    entries: list[dict],
    no_chunk: list[dict],
    low_score: list[dict],
) -> str:
    mean = float(metrics.get("mean_judge_score") or 0)
    chunk_rate = float(metrics.get("chunk_in_evidence_rate") or 0)
    doc_rate = float(metrics.get("doc_in_evidence_rate") or 0)
    evidence_mean = metrics.get("mean_evidence_judge_score")
    parts = [
        f"1. Средний балл **финального ответа** **{mean:.1f}/10** (balanced, limit=40, Polza).",
        f"2. Exact chunk hit: **{chunk_rate:.0%}**; doc hit (любой chunk): **{doc_rate:.0%}**.",
        f"3. Кейсов с score ≤ 5: **{len(low_score)}** из {len(entries)}.",
    ]
    if evidence_mean is not None:
        parts.append(
            f"4. Final evidence judge (top-{FINAL_EVIDENCE_JUDGE_TOP_N}): средний **{evidence_mean}/10**, "
            f"can_answer={metrics.get('evidence_can_answer_rate', '—')}, "
            f"useful_chunks={metrics.get('mean_useful_chunks', '—')}."
        )
    by_type = metrics.get("by_question_type") or {}
    if by_type:
        worst_type = min(by_type.items(), key=lambda item: item[1])
        best_type = max(by_type.items(), key=lambda item: item[1])
        idx = 5 if evidence_mean is not None else 4
        parts.append(
            f"{idx}. Слабее всего тип **{worst_type[0]}** ({worst_type[1]}), "
            f"лучше **{best_type[0]}** ({best_type[1]})."
        )
    summary_idx = 6 if evidence_mean is not None and by_type else (5 if evidence_mean is not None or by_type else 4)
    if mean >= 7:
        parts.append(
            f"{summary_idx}. В целом пайплайн даёт приемлемые ответы на chunk-grounded вопросы, "
            "но стабильность retrieval ниже качества финального текста."
        )
    else:
        parts.append(
            f"{summary_idx}. Качество ответов ниже целевого порога 7/10 — "
            "узкие места в планировании и/или retrieval."
        )
    return "\n".join(parts)


def _build_recommendations(
    metrics: dict[str, Any],
    entries: list[dict],
    no_chunk: list[dict],
    low_score: list[dict],
    warning_counter: Counter[str],
) -> str:
    recs = []
    chunk_rate = float(metrics.get("chunk_in_evidence_rate") or 0)
    if chunk_rate < 0.6:
        recs.append(
            "- **Retrieval:** повысить recall для point_lookup — буст payload по `point_number` + `doc_id`, "
            "когда планировщик уверенно определил документ."
        )
    if any("no verified document" in w for w in warning_counter):
        recs.append(
            "- **Планировщик:** для chunk-grounded вопросов с явным пунктом/документом в формулировке — "
            "детерминированный резолвер doc_id до LLM, без отказа при score 0.55."
        )
    if any("ambiguous" in w.lower() for w in warning_counter):
        recs.append(
            "- **Неоднозначность:** не трактовать широкий hybrid как ошибку для comparison/wide_overview; "
            "снижать severity warning, если prefetch ГОСТ или несколько релевантных актов ожидаемы."
        )
    definition_low = [
        e for e in low_score if str(e.get("question_type") or "") == "definition"
    ]
    if definition_low:
        recs.append(
            "- **Определения:** prefetch ГОСТ + sectoral point (796 п.98 и т.п.) уже помогает; "
            "расширить без topic_alias — через registry_key / doc_id в metadata чанка."
        )
    recs.append(
        "- **Оценка:** периодически перезапускать `scripts/run_pipeline_research.py --phase eval` "
        "на фиксированном corpus_50.json для регрессии после изменений planner/retrieval."
    )
    recs.append(
        "- **Промпты Polza:** при score ≤ 5 чаще всего расходятся эталон (узкий фрагмент) и ответ (обобщение) — "
        "усилить final_answer правило «не расширять beyond evidence top-N»."
    )
    return "\n".join(recs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline research: corpus + eval + report")
    parser.add_argument(
        "--phase",
        choices=["all", "generate", "eval", "report"],
        default="all",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--corpus-style",
        choices=["explicit", "natural"],
        default="explicit",
        help="explicit: вопросы с названиями документов/пунктов; natural: без ссылок на акты.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Seed для выборки фрагментов (default: 20260523 explicit / 20260524 natural).",
    )
    parser.add_argument(
        "--exclude-corpus",
        type=Path,
        default=None,
        help="JSON-корпус: chunk_id из него исключаются из новой выборки.",
    )
    parser.add_argument("--eval-output", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--chunks", type=Path, default=CHUNKS_PATH)
    parser.add_argument("--count", type=int, default=CORPUS_SIZE)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--reuse-eval", type=Path, default=None)
    parser.add_argument(
        "--skip-evidence-judge",
        action="store_true",
        help="Skip Polza judge on retrieved evidence (faster; no evidence_judge_* fields).",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Re-run norm_lookup for final evidence metrics + chunk judge; keep cached answer judge.",
    )
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="Re-run answer judge only on cached eval entries (no norm_lookup).",
    )
    parser.add_argument(
        "--question-type",
        default=None,
        help="Run eval only for corpus items with this question_type (e.g. requirement).",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Limit eval to the first N corpus items after filters.",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="Comma-separated case ids to run (e.g. case_001,case_013).",
    )
    args = parser.parse_args()

    os.environ.setdefault("QDRANT_HOST", "localhost")
    os.environ.setdefault("QDRANT_PORT", "6333")
    os.environ.setdefault("MR_NORM_QDRANT_COLLECTION", "mr_norm_docs_bge_m3")
    os.environ.setdefault("RAG_EMBEDDING_DEVICE", "cpu")

    keys_path = resolve_keys_path()

    if args.phase in {"all", "generate"}:
        if args.phase == "all" and args.corpus.is_file():
            print(f"Corpus exists, skipping generate: {args.corpus}")
        else:
            generate_corpus(
                chunks_path=args.chunks,
                output_path=args.corpus,
                keys_path=keys_path,
                count=args.count,
                corpus_style=args.corpus_style,
                random_seed=args.random_seed,
                exclude_corpus=args.exclude_corpus,
            )

    if args.phase in {"all", "eval"}:
        if not args.corpus.is_file():
            raise SystemExit(f"Corpus not found: {args.corpus}. Run --phase generate first.")
        if args.reuse_eval:
            reuse_path = args.reuse_eval
        elif args.retrieval_only and DEFAULT_EVAL.is_file() and args.eval_output.resolve() != DEFAULT_EVAL.resolve():
            reuse_path = DEFAULT_EVAL
        elif args.eval_output.is_file():
            reuse_path = args.eval_output
        else:
            reuse_path = None
        run_eval(
            args.corpus,
            output_path=args.eval_output,
            keys_path=keys_path,
            limit=args.limit,
            profile=args.profile,
            reuse_path=reuse_path,
            skip_evidence_judge=args.skip_evidence_judge,
            judge_only=args.judge_only,
            retrieval_only=args.retrieval_only,
            question_type=args.question_type,
            max_cases=args.max_cases,
            case_ids={part.strip() for part in args.case_ids.split(",") if part.strip()} if args.case_ids else None,
        )

    if args.phase in {"all", "report"}:
        if not args.eval_output.is_file():
            raise SystemExit(f"Eval not found: {args.eval_output}")
        build_report_md(args.eval_output, output_path=args.report_output)


if __name__ == "__main__":
    main()
