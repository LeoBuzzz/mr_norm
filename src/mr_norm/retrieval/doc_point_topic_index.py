from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from mr_norm.config.paths import ProjectPaths
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.document_catalog import normalize_catalog_text
from mr_norm.retrieval.document_knowledge import load_document_knowledge
from mr_norm.runtime.pipeline_diagnostics import doc_names_match, normalize_doc_key

TopicEvidencePolicy = Literal["topic_plus_baseline", "topic_only"]
TopicSearchProfile = Literal["base", "tuned"]

STOP_WORDS = frozenset(
    {
        "какие",
        "какой",
        "какая",
        "какое",
        "когда",
        "куда",
        "откуда",
        "зачем",
        "почему",
        "может",
        "могут",
        "нужно",
        "надо",
        "должен",
        "должна",
        "должны",
        "требования",
        "порядок",
        "правила",
        "условия",
        "случае",
        "объектов",
        "объекты",
        "электроэнергетики",
        "электрической",
        "энергии",
        "энергетики",
        "которые",
        "которых",
        "связанные",
        "связанных",
        "используемые",
        "документа",
        "наименования",
        "наименование",
    }
)

BASE_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "интернет": ("информационно телекоммуникационная сеть", "сеть интернет", "удаленный доступ"),
    "доступ": ("удаленный доступ", "подключение", "допускается", "запрещается"),
    "цели": ("расходоваться", "расходование", "направляются", "средства"),
    "средства": ("расходование средств", "затраты", "финансирование"),
    "связь": ("средства связи", "каналы связи", "организация связи"),
    "диспетчер": ("диспетчерский центр", "оперативно диспетчерское управление"),
    "дистанцион": ("дистанционное управление", "удаленный доступ"),
    "запрещ": ("запрещается", "не допускается", "не разрешается"),
    "разреш": ("допускается", "разрешается", "запрещается", "не допускается"),
    "срок": ("не позднее", "ежегодно", "период"),
}

TUNED_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    **BASE_QUERY_EXPANSIONS,
    "наименование": ("наименование изменено", "наименование документа", "предыдущую редакцию"),
    "название": ("наименование изменено", "наименование документа", "предыдущую редакцию"),
    "миллионник": ("свыше 1 миллиона", "5 МВт", "резервное электроснабжение", "0,82"),
    "резерв": ("резервное электроснабжение", "два независимых источника", "секции шин"),
    "автоном": ("автономные объекты", "технологически изолированные", "специальные правила"),
    "параметр": ("температурный режим", "распределение нагрузки", "эксцентриситет"),
}


@dataclass(frozen=True)
class TopicRetryPolicy:
    search_profile: TopicSearchProfile = "base"
    evidence_policy: TopicEvidencePolicy = "topic_plus_baseline"


@dataclass(frozen=True)
class TopicRerankPolicy:
    search_profile: TopicSearchProfile = "base"
    doc_ids: tuple[str, ...] = ()
    doc_names: tuple[str, ...] = ()
    top_k: int = 8
    min_promote_score: float = 2.5
    table_like: bool = False
    explicit_doc_scope: bool = False
    max_promotions: int = 5


@dataclass(frozen=True)
class TopicRerankResult:
    ranked_items: tuple[RetrievedItem, ...]
    applied: bool = False
    reason: str = ""
    policy_label: str = ""
    items_promoted: int = 0
    top_hits: tuple[TopicHit, ...] = ()


@dataclass(frozen=True)
class TopicEntry:
    topic_id: str
    doc_id: str
    doc_name: str
    point_number: str
    heading_path_text: str
    chunk_ids: tuple[str, ...]
    text: str
    doc_summary: str = ""
    point_topic: str = ""
    natural_aliases: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopicHit:
    entry: TopicEntry
    score: float
    hits: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocPointTopicIndex:
    entries: tuple[TopicEntry, ...]
    idf: dict[str, float]
    source_path: str = ""


def compact_text(text: str, *, limit: int) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def numeric_markers(text: str) -> list[str]:
    return re.findall(r"\b\d+(?:[,.]\d+)?\b", text or "")


def topic_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in normalize_catalog_text(text).split()
        if len(token) >= 4 and token not in STOP_WORDS
    )


def expanded_query(query: str, *, profile: TopicSearchProfile) -> str:
    norm = normalize_catalog_text(query)
    expansions = TUNED_QUERY_EXPANSIONS if profile == "tuned" else BASE_QUERY_EXPANSIONS
    additions: list[str] = []
    for marker, phrases in expansions.items():
        if marker in norm:
            additions.extend(phrases)
    return " ".join([query, *additions])


def natural_aliases(text: str, heading: str) -> tuple[str, ...]:
    norm = normalize_catalog_text(text)
    aliases: list[str] = []
    if "запрещ" in norm or "не допуска" in norm or "не разреш" in norm:
        aliases.extend(("что запрещено", "разрешено ли", "допускается ли", "какие запреты установлены"))
    if "не позднее" in norm or "ежегодн" in norm or "срок" in norm:
        aliases.extend(("когда нужно", "в какой срок", "до какой даты", "как часто"))
    if "обязан" in norm or "должен" in norm or "обеспеч" in norm:
        aliases.extend(("что обязан", "что должен обеспечить", "какие обязанности"))
    if "расход" in norm and "средств" in norm:
        aliases.extend(("на какие цели расходуются средства", "целевое расходование средств"))
    if "дистанцион" in norm or "удален" in norm:
        aliases.extend(("дистанционное управление", "удаленный доступ", "управление через сеть"))
    if heading:
        aliases.append(heading)
    aliases.extend(numeric_markers(text))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def point_topic(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", compact_text(text, limit=1600))
    markers = (
        "запрещ",
        "не допуска",
        "не позднее",
        "обязан",
        "должен",
        "осуществляется",
        "расход",
        "средств",
        "дистанцион",
        "удален",
        "связ",
    )
    important: list[str] = []
    for sentence in sentences:
        norm = normalize_catalog_text(sentence)
        if any(marker in norm for marker in markers):
            important.append(sentence)
        if len(important) >= 3:
            break
    if not important:
        important = sentences[:2]
    return compact_text(" ".join(important), limit=650)


def build_doc_point_topic_index(chunks_path: Path) -> DocPointTopicIndex:
    if not chunks_path.is_file():
        return DocPointTopicIndex(entries=(), idf={}, source_path=str(chunks_path))

    knowledge = load_document_knowledge()
    doc_summaries = {doc.doc_id: doc.annotation for doc in knowledge.documents}
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        payload = chunk.get("payload") or {}
        doc_id = str(payload.get("doc_id") or "")
        doc_name = str(payload.get("doc_name") or "")
        point_number = str(payload.get("point_number") or "")
        point_key = str(payload.get("point_id") or payload.get("point_identity_key") or "")
        if doc_id and doc_name and str(chunk.get("text") or "").strip():
            grouped[(doc_id, point_number, point_key)].append(chunk)

    entries: list[TopicEntry] = []
    for index, ((_doc_id, _point_number, _point_key), group) in enumerate(grouped.items(), start=1):
        group = sorted(group, key=lambda item: int((item.get("payload") or {}).get("chunk_index") or 0))
        first_payload = group[0].get("payload") or {}
        doc_id = str(first_payload.get("doc_id") or "")
        doc_name = str(first_payload.get("doc_name") or "")
        point_number = str(first_payload.get("point_number") or "")
        heading = str(first_payload.get("heading_path_text") or first_payload.get("point_scope") or "")
        text = compact_text(" ".join(str(item.get("text") or "") for item in group), limit=5200)
        summary = compact_text(doc_summaries.get(doc_id, ""), limit=600)
        topic = point_topic(text)
        aliases = natural_aliases(text, heading)
        tokens = topic_tokens(" ".join((doc_name, heading, summary, topic, " ".join(aliases), text)))
        entries.append(
            TopicEntry(
                topic_id=f"topic_{index:06d}",
                doc_id=doc_id,
                doc_name=doc_name,
                point_number=point_number,
                heading_path_text=heading,
                chunk_ids=tuple(str(item.get("chunk_id") or "") for item in group if item.get("chunk_id")),
                text=text,
                doc_summary=summary,
                point_topic=topic,
                natural_aliases=aliases,
                tokens=tokens,
            )
        )

    idf = compute_idf(entries)
    return DocPointTopicIndex(entries=tuple(entries), idf=idf, source_path=str(chunks_path))


def compute_idf(entries: list[TopicEntry] | tuple[TopicEntry, ...]) -> dict[str, float]:
    df: Counter[str] = Counter()
    for entry in entries:
        df.update(set(entry.tokens))
    total = len(entries)
    return {token: math.log((total + 1) / (count + 0.5)) for token, count in df.items()}


@lru_cache(maxsize=1)
def load_default_doc_point_topic_index(root: str = "") -> DocPointTopicIndex:
    paths = ProjectPaths.from_root(Path(root) if root else None)
    return build_doc_point_topic_index(paths.chunks_json)


TOPIC_RERANK_QUESTION_TYPES = frozenset(
    {"point_lookup", "definition", "document_scope", "requirement", "procedure", "factual"}
)

TOPIC_RERANK_QUERY_MARKERS = (
    "обязан",
    "должен",
    "срок",
    "не позднее",
    "запрещ",
    "допускается",
    "на какие объекты",
    "по каким параметрам",
    "индикатор",
    "показатель",
    "таблица",
    "автоном",
    "специальн",
    "форм договор",
    "обязанност",
)

TABLE_LIKE_MARKERS = (
    "параметр",
    "показател",
    "температур",
    "нагруз",
    "эксцентриситет",
    "шкал",
    "сегмент",
)


def _baseline_matches_resolved_docs(
    resolved_doc_names: tuple[str, ...] | list[str],
    ranked_items: list[RetrievedItem],
    *,
    top_n: int = 3,
) -> bool:
    names = tuple(str(value).strip() for value in resolved_doc_names if str(value).strip())
    if not names or not ranked_items:
        return False
    for item in ranked_items[:top_n]:
        actual = str(item.doc_name or "")
        if actual and any(doc_names_match(name, actual) for name in names):
            return True
    return False


def select_topic_rerank_policy(
    query: str,
    *,
    question_type: str = "",
    resolved_doc_ids: tuple[str, ...] | list[str] = (),
    resolved_doc_names: tuple[str, ...] | list[str] = (),
    ranked_items: list[RetrievedItem] | None = None,
) -> TopicRerankPolicy:
    norm = normalize_catalog_text(query)
    table_like = any(marker in norm for marker in TABLE_LIKE_MARKERS)
    profile: TopicSearchProfile = "tuned" if table_like or any(
        marker in norm for marker in ("наименован", "городах миллионниках", "интернет", "запрещ")
    ) else "base"
    doc_ids = tuple(str(value).strip() for value in resolved_doc_ids if str(value).strip())
    doc_names = tuple(str(value).strip() for value in resolved_doc_names if str(value).strip())
    explicit_doc_scope = bool(doc_ids or doc_names)
    if not doc_ids and not doc_names and ranked_items:
        top = ranked_items[0]
        top_doc_id = str(top.doc_id or "").strip()
        top_doc_name = str(top.doc_name or "").strip()
        if top_doc_id:
            doc_ids = (top_doc_id,)
        if top_doc_name:
            doc_names = (top_doc_name,)
    return TopicRerankPolicy(
        search_profile=profile,
        doc_ids=doc_ids,
        doc_names=doc_names,
        top_k=8,
        min_promote_score=2.0 if table_like else 2.5,
        table_like=table_like,
        explicit_doc_scope=explicit_doc_scope,
        max_promotions=5,
    )


def should_apply_topic_rerank(
    *,
    query: str,
    question_type: str = "",
    ranked_items: list[RetrievedItem] | None = None,
    resolved_doc_names: tuple[str, ...] | list[str] = (),
    point_number_hints: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    if os.environ.get("MR_NORM_DISABLE_TOPIC_RERANK", "").strip() == "1":
        return False, "topic_rerank_disabled"
    if not ranked_items:
        return False, "topic_rerank:no_ranked_items"
    qtype = (question_type or "").strip().lower()
    hints = tuple(str(value).strip() for value in point_number_hints if str(value).strip())
    if _baseline_matches_resolved_docs(resolved_doc_names, ranked_items) and not hints:
        if qtype in {"factual", "procedure", "document_scope", "requirement"}:
            return False, "topic_rerank:baseline_doc_satisfied"
    norm = normalize_catalog_text(query)
    if qtype in TOPIC_RERANK_QUESTION_TYPES:
        return True, f"topic_rerank:question_type:{qtype or 'unknown'}"
    if any(marker in norm for marker in TOPIC_RERANK_QUERY_MARKERS):
        return True, "topic_rerank:query_markers"
    return False, "topic_rerank:not_needed"


def _score_table_like_bonus(query: str, entry: TopicEntry) -> float:
    norm = normalize_catalog_text(query)
    text = entry.text or ""
    bonus = 0.0
    if any(marker in norm for marker in TABLE_LIKE_MARKERS):
        if re.search(r"\b\d+(?:[,.]\d+)?\b", text):
            bonus += 0.35
        if any(unit in text for unit in ("°C", "кг", "%", "МВт", "мвт")):
            bonus += 0.55
        if text.count(";") >= 2 or text.count("|") >= 2:
            bonus += 0.25
    return bonus


def _filter_hits_by_doc_scope(
    hits: list[TopicHit],
    *,
    doc_ids: tuple[str, ...],
    doc_names: tuple[str, ...],
    allow_global_fallback: bool = False,
) -> list[TopicHit]:
    if not doc_ids and not doc_names:
        return hits
    scoped: list[TopicHit] = []
    for hit in hits:
        entry = hit.entry
        if doc_ids and entry.doc_id in doc_ids:
            scoped.append(hit)
            continue
        if doc_names and any(doc_names_match(name, entry.doc_name) for name in doc_names):
            scoped.append(hit)
    if scoped or not allow_global_fallback:
        return scoped
    return hits


def _item_dedupe_key(item: RetrievedItem) -> str:
    chunk_id = str(item.chunk_id or "").strip()
    if chunk_id:
        return chunk_id
    return f"{item.doc_id}:{item.point_number}:{compact_text(item.text, limit=80)}"


def promote_topic_items(
    *,
    topic_items: list[RetrievedItem],
    ranked_items: list[RetrievedItem],
    limit: int,
) -> tuple[list[RetrievedItem], int]:
    selected: list[RetrievedItem] = []
    seen: set[str] = set()
    promoted = 0
    topic_keys = {_item_dedupe_key(item) for item in topic_items}
    for item in [*topic_items, *ranked_items]:
        key = _item_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if key in topic_keys:
            promoted += 1
        if len(selected) >= limit:
            break
    return selected, promoted


def rerank_with_doc_point_topics(
    query: str,
    ranked_items: list[RetrievedItem],
    index: DocPointTopicIndex,
    *,
    policy: TopicRerankPolicy,
) -> TopicRerankResult:
    if not ranked_items or not index.entries:
        return TopicRerankResult(ranked_items=tuple(ranked_items), reason="topic_rerank:no_index_or_items")

    hits = search_doc_point_topics(
        query,
        index,
        top_k=policy.top_k,
        profile=policy.search_profile,
    )
    if policy.doc_ids or policy.doc_names:
        hits = _filter_hits_by_doc_scope(
            hits,
            doc_ids=policy.doc_ids,
            doc_names=policy.doc_names,
            allow_global_fallback=False,
        )
    if policy.table_like:
        rescored: list[TopicHit] = []
        for hit in hits:
            bonus = _score_table_like_bonus(query, hit.entry)
            if bonus:
                rescored.append(
                    TopicHit(entry=hit.entry, score=round(hit.score + bonus, 4), hits=hit.hits)
                )
            else:
                rescored.append(hit)
        hits = sorted(rescored, key=lambda item: item.score, reverse=True)

    if not hits or hits[0].score < policy.min_promote_score:
        return TopicRerankResult(
            ranked_items=tuple(ranked_items),
            reason="topic_rerank:weak_hits",
            policy_label=f"{policy.search_profile}:promote",
            top_hits=tuple(hits[:5]),
        )

    topic_items = [
        topic_hit_to_retrieved_item(hit)
        for hit in hits[: policy.max_promotions]
    ]
    merged, promoted = promote_topic_items(
        topic_items=topic_items,
        ranked_items=ranked_items,
        limit=len(ranked_items),
    )
    if promoted <= 0 or merged == ranked_items:
        return TopicRerankResult(
            ranked_items=tuple(ranked_items),
            reason="topic_rerank:no_promotion",
            policy_label=f"{policy.search_profile}:promote",
            top_hits=tuple(hits[:5]),
        )

    return TopicRerankResult(
        ranked_items=tuple(merged),
        applied=True,
        reason="topic_rerank:promoted",
        policy_label=f"{policy.search_profile}:promote",
        items_promoted=promoted,
        top_hits=tuple(hits[:5]),
    )


def select_topic_retry_policy(query: str) -> TopicRetryPolicy:
    norm = normalize_catalog_text(query)
    if "запрещ" in norm:
        return TopicRetryPolicy(search_profile="tuned", evidence_policy="topic_only")
    if any(marker in norm for marker in ("наименован", "городах миллионниках", "интернет")):
        return TopicRetryPolicy(search_profile="tuned", evidence_policy="topic_plus_baseline")
    return TopicRetryPolicy(search_profile="base", evidence_policy="topic_plus_baseline")


def search_doc_point_topics(
    query: str,
    index: DocPointTopicIndex,
    *,
    top_k: int = 8,
    profile: TopicSearchProfile = "base",
) -> list[TopicHit]:
    query_tokens = topic_tokens(expanded_query(query, profile=profile))
    if not query_tokens or not index.entries:
        return []
    query_counter = Counter(query_tokens)
    query_numbers = set(numeric_markers(query))
    scored: list[TopicHit] = []
    for entry in index.entries:
        entry_token_set = set(entry.tokens)
        hits = tuple(token for token in query_counter if token in entry_token_set)
        if not hits:
            continue
        score = sum(index.idf.get(token, 0.0) * query_counter[token] for token in hits)
        text_norm = normalize_catalog_text(entry.text)
        alias_norm = normalize_catalog_text(" ".join(entry.natural_aliases))
        doc_norm = normalize_catalog_text(entry.doc_name)
        for token in query_tokens:
            if token in alias_norm:
                score += 0.45
            if token in doc_norm:
                score += 0.25
            if token in text_norm:
                score += 0.08
        if profile == "tuned" and query_numbers:
            entry_numbers = set(numeric_markers(entry.text))
            score += 1.4 * len(query_numbers & entry_numbers)
        if entry.point_number and any(char.isdigit() for char in query):
            if entry.point_number.replace("_", ".") in query:
                score += 2.0
        if profile == "tuned" and entry.doc_name.strip().upper() == "ФЕДЕРАЛЬНЫЙ ЗАКОН":
            score *= 0.65
        scored.append(TopicHit(entry=entry, score=round(score, 4), hits=hits[:12]))
    scored.sort(key=lambda hit: hit.score, reverse=True)
    return scored[:top_k]


def topic_hit_to_retrieved_item(hit: TopicHit) -> RetrievedItem:
    entry = hit.entry
    return RetrievedItem(
        chunk_id=entry.chunk_ids[0] if entry.chunk_ids else entry.topic_id,
        doc_id=entry.doc_id,
        doc_name=entry.doc_name,
        heading_path_text=entry.heading_path_text,
        point_number=entry.point_number,
        text=entry.text,
        score=hit.score,
        source_tool="topic_index",
        matched={"topic_id": entry.topic_id, "hits": list(hit.hits)},
    )


def should_attempt_topic_retry(
    *,
    query: str,
    final_warnings: list[str],
    has_citations: bool,
    resolved_doc_names: tuple[str, ...] | list[str] = (),
    topic_rerank_applied: bool = False,
) -> tuple[bool, str]:
    if os.environ.get("MR_NORM_DISABLE_TOPIC_RETRY", "").strip() == "1":
        return False, "topic_retry_disabled"
    if topic_rerank_applied and has_citations:
        warning_text = " ".join(final_warnings)
        if "anti_refusal_guard" not in warning_text and "final answer returned no valid citations" not in warning_text:
            return False, "topic_retry:rerank_satisfied"
    warning_text = " ".join(final_warnings)
    if not has_citations:
        return True, "topic_retry:no_valid_citations"
    if "anti_refusal_guard" in warning_text or "final answer returned no valid citations" in warning_text:
        return True, "topic_retry:weak_final_answer"
    norm = normalize_catalog_text(query)
    if not resolved_doc_names and any(marker in norm for marker in ("запрещ", "интернет", "городах миллионниках")):
        return True, "topic_retry:unresolved_natural_marker"
    return False, "topic_retry:not_needed"


def merge_topic_retry_items(
    *,
    topic_items: list[RetrievedItem],
    ranked_items: list[RetrievedItem],
    policy: TopicEvidencePolicy,
    limit: int,
) -> tuple[list[RetrievedItem], int]:
    combined = topic_items if policy == "topic_only" else [*topic_items, *ranked_items]
    selected: list[RetrievedItem] = []
    seen: set[str] = set()
    added = 0
    topic_keys = {item.chunk_id for item in topic_items if item.chunk_id}
    for item in combined:
        key = item.chunk_id or f"{item.doc_id}:{item.point_number}:{item.text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if key in topic_keys:
            added += 1
        if len(selected) >= limit:
            break
    return selected, added
