"""Wide, traceable point search and normative-reference expansion (sandbox only).

This pilot never edits the corpus or Qdrant. Search results are candidates, not a
claim of semantic completeness. Reference parsing deliberately fails closed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import time
import urllib.request
import copy
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from corpus import CanonicalCorpus, chunk_order
from corpus_point_search import (CorpusPointSearchContext, _point_identity,
                                 _row, _vector_hits, assemble_point_by_identity)


@dataclass
class FindAllConfig:
    discovery_top_k: int = 100
    deep_top_k: int = 100
    max_deep_docs: int = 24
    max_direct_points: int = 3000
    max_aspects: int = 4
    planner_mode: str = "llm"
    planner_url: str = "http://localhost:11434/api/chat"
    planner_model: str = "qwen3:8b"
    planner_timeout_sec: float = 120.0
    page_size: int = 50
    reference_depth: int = 1
    follow_external: bool = True
    max_reference_points: int = 200
    max_targets_per_reference: int = 100
    ranking_mode: str = "original"
    reference_priority: str = "legacy"
    reference_cache: bool = True
    min_reranker_score: float | None = None
    # None means every token window is scored. A positive cap explicitly leaves
    # a recorded unprocessed tail (currently not recommended for a recall task).
    max_windows_per_point: int | None = None
    offset: int = 0
    fingerprint: str | None = None
    tokenizer: Any = field(default=None, repr=False, compare=False)
    model_path: Any = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        ranges = {"discovery_top_k": (self.discovery_top_k, 1, 1000),
                  "deep_top_k": (self.deep_top_k, 1, 1000),
                  "max_deep_docs": (self.max_deep_docs, 1, 10000),
                  "max_direct_points": (self.max_direct_points, 1, 10000),
                  "max_aspects": (self.max_aspects, 0, 20),
                  "page_size": (self.page_size, 1, 500),
                  "reference_depth": (self.reference_depth, 0, 3),
                  "max_reference_points": (self.max_reference_points, 0, 5000),
                  "max_targets_per_reference": (self.max_targets_per_reference, 1, 5000),
                  "offset": (self.offset, 0, 10**9)}
        for name, (value, low, high) in ranges.items():
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        if self.max_windows_per_point is not None and (isinstance(self.max_windows_per_point, bool)
                or not isinstance(self.max_windows_per_point, int) or self.max_windows_per_point < 1):
            raise ValueError("max_windows_per_point must be positive or None")
        if self.planner_mode not in {"off", "llm"}:
            raise ValueError("planner_mode must be 'off' or 'llm'")
        if self.ranking_mode not in {"original", "aspect_interleave"}:
            raise ValueError("ranking_mode must be 'original' or 'aspect_interleave'")
        if self.reference_priority not in {"legacy", "exact_first"}:
            raise ValueError("reference_priority must be 'legacy' or 'exact_first'")
        if not isinstance(self.planner_url, str) or not self.planner_url.startswith("http://localhost:") and not self.planner_url.startswith("http://127.0.0.1:"):
            raise ValueError("planner_url must use localhost or 127.0.0.1")
        if not isinstance(self.planner_model, str) or not self.planner_model.strip():
            raise ValueError("planner_model must be nonempty")
        if isinstance(self.planner_timeout_sec, bool) or not isinstance(self.planner_timeout_sec, (int, float)) or not math.isfinite(self.planner_timeout_sec) or self.planner_timeout_sec <= 0:
            raise ValueError("planner_timeout_sec must be positive")
        if self.min_reranker_score is not None and (isinstance(self.min_reranker_score, bool)
                or not isinstance(self.min_reranker_score, (int, float))
                or not math.isfinite(float(self.min_reranker_score))):
            raise ValueError("min_reranker_score must be numeric or None")


def _canonical_fingerprint(corpus: CanonicalCorpus) -> str:
    h = hashlib.sha256()
    for did in sorted(corpus.docs):
        for c in corpus.docs[did]:
            h.update(str(c["chunk_id"]).encode())
            h.update(str(c.get("text") or "").encode())
            h.update(json.dumps(c.get("payload", {}), sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


PLANNER_PROMPT_VERSION = "find-all-aspect-planner-v2"


def _required_constraints(query: str) -> list[str]:
    constraints = []
    for m in re.finditer(r"\d+(?:[.,]\d+)?(?:\s*(?:лет|года|год|месяц(?:а|ев)?|дн(?:я|ей)?|час(?:а|ов)?|минут(?:а|ы)?|%))?", query, re.I):
        constraints.append(m.group(0))
    for m in re.finditer(r"\b(?:не|ни|нет|без|нельзя|кроме|только|исключительно)\b[^,;.!?]*", query, re.I):
        constraints.append(m.group(0).strip())
    return list(dict.fromkeys(constraints))


_UNSAFE_ASPECT_RE = re.compile(
    r"\b(?:игнорир\w*|не\s+учитывай\w*|убери|удали|исключи|ignore|disregard)\b"
    r".{0,100}\b(?:ограничен\w*|услови\w*|исходн\w*|вопрос\w*|запрос\w*|требован\w*|"
    r"област\w*|scope|constraints?)\b|"
    r"\b(?:измени|меняй|расширь|сузь|change|alter|expand|narrow)\b.{0,80}"
    r"\b(?:област\w*|поиск\w*|scope)\b", re.I)


def _validate_aspect_text(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "empty_or_non_string"
    normalized = " ".join(value.split())
    if not normalized:
        return None, "empty_or_non_string"
    if len(normalized) > 500:
        return None, "too_long"
    if _UNSAFE_ASPECT_RE.search(normalized):
        return None, "unsafe_instruction"
    return normalized, None


def _ollama_aspects(query: str, cfg: FindAllConfig) -> dict[str, Any]:
    prompt = (
        "Разложи пользовательский вопрос на не более четырёх дополнительных поисковых аспектов "
        "для поиска нормативного документа. Сохраняй предмет и все ограничения исходного вопроса. "
        "Подумай, какие стороны темы и этапы процесса могут быть самостоятельными поисковыми ветками: "
        "виды и область применения, подготовка и планирование, ход и способы выполнения, "
        "результаты, оценка и фиксация. Выбирай только подходящие исходному вопросу аспекты; "
        "не добавляй отсутствующие обязанности или факты. "
        "Не отвечай на вопрос, не заменяй и не переформулируй его, не предлагай игнорировать "
        "ограничения. Каждый аспект должен дополнять, а не отменять исходный вопрос. "
        "Верни только JSON с массивами aspects (объекты {text, rationale}) и constraints "
        "(дословные короткие цитаты ограничений из исходника). Если аспектов не нужно, верни [].\n"
        "Исходный вопрос:\n" + query
    )
    payload = {"model": cfg.planner_model, "messages": [{"role": "user", "content": prompt}],
               "stream": False, "think": False, "format": "json",
               "options": {"temperature": 0, "num_predict": 768}}
    req = urllib.request.Request(cfg.planner_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=cfg.planner_timeout_sec) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = (body.get("message") or {}).get("content")
    return json.loads(content) if isinstance(content, str) else content


def _query_plan(query: str, aspects: list[str] | None, cfg: FindAllConfig,
                planner_adapter: Callable[[str], Any] | None = None,
    saved_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    if saved_plan is not None:
        if not isinstance(saved_plan, dict) or saved_plan.get("original_query") != query:
            raise ValueError("saved_query_plan_query_mismatch")
        aspect_rows = saved_plan.get("aspects")
        constraints = saved_plan.get("constraints")
        skipped = saved_plan.get("skipped_aspects")
        planner = saved_plan.get("planner")
        if (not isinstance(aspect_rows, list) or not isinstance(constraints, list)
                or not isinstance(skipped, list) or not isinstance(planner, dict)):
            raise ValueError("saved_query_plan_invalid_structure")
        if len(aspect_rows) > cfg.max_aspects:
            raise ValueError("saved_query_plan_exceeds_max_aspects")
        if (planner.get("mode") not in {"off", "llm"}
                or planner.get("status") not in {"ok", "fallback", "disabled", "skipped"}
                or not isinstance(planner.get("backend"), str)
                or not isinstance(planner.get("prompt_version"), str)):
            raise ValueError("saved_query_plan_invalid_planner_status")
        if planner["status"] == "fallback" and (not planner.get("failure") or not planner.get("reason")):
            raise ValueError("saved_query_plan_fallback_missing_failure")
        if planner["status"] == "ok" and (planner.get("failure") or planner.get("reason")):
            raise ValueError("saved_query_plan_success_has_failure_metadata")
        if planner["status"] in {"disabled", "skipped"} and planner.get("failure"):
            raise ValueError("saved_query_plan_skipped_has_failure")
        required_constraints = _required_constraints(query)
        if any(not isinstance(x, str) or not x or x not in query for x in constraints):
            raise ValueError("saved_query_plan_constraint_not_verbatim")
        if any(item not in constraints for item in required_constraints):
            raise ValueError("saved_query_plan_constraint_omitted")
        seen = {" ".join(query.split()).casefold()}
        for row in aspect_rows:
            if not isinstance(row, dict) or row.get("source") not in {"caller_aspect", "llm_aspect"} or row.get("validation") != "accepted":
                raise ValueError("saved_query_plan_invalid_aspect_provenance")
            aspect, rationale = row.get("text"), row.get("rationale")
            normalized_aspect, aspect_error = _validate_aspect_text(aspect)
            if (aspect_error or normalized_aspect != aspect
                    or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 240):
                raise ValueError("saved_query_plan_invalid_aspect")
            key = " ".join(aspect.split()).casefold()
            if key in seen:
                raise ValueError("saved_query_plan_duplicate_aspect")
            seen.add(key)
        expected_queries = [{"query": query, "origin": "original", "aspect": None}]
        expected_queries.extend({"query": f"{query} [дополнительный аспект: {row['text']}]",
                                 "origin": row["source"], "aspect": row["text"]} for row in aspect_rows)
        if saved_plan.get("queries") != expected_queries or saved_plan.get("executed_queries") != [x["query"] for x in expected_queries]:
            raise ValueError("saved_query_plan_queries_do_not_match_validated_aspects")
        return json.loads(json.dumps(saved_plan, ensure_ascii=False))
    cleaned: list[str] = []
    skipped = []
    seen = {" ".join(query.split()).casefold()}
    for aspect in aspects or []:
        normalized, error = _validate_aspect_text(aspect)
        if error:
            skipped.append({"aspect": aspect, "reason": error})
            continue
        aspect = normalized
        key = aspect.casefold()
        if key in seen:
            skipped.append({"aspect": aspect, "reason": "duplicate"})
        elif len(cleaned) >= cfg.max_aspects:
            skipped.append({"aspect": aspect, "reason": "max_aspects"})
        else:
            seen.add(key)
            cleaned.append(aspect)
    plan: dict[str, Any] = {"original_query": query, "planner": {"mode": cfg.planner_mode,
        "backend": "injected" if planner_adapter else ("ollama" if cfg.planner_mode == "llm" else "disabled"),
        "model": cfg.planner_model if cfg.planner_mode == "llm" else None,
        "prompt_version": PLANNER_PROMPT_VERSION, "timeout_sec": cfg.planner_timeout_sec,
        "elapsed_sec": 0.0, "status": "disabled" if cfg.planner_mode == "off" else "skipped",
        "reason": None, "failure": None}, "constraints": _required_constraints(query),
        "aspects": [{"text": a, "rationale": "Передан вызывающим кодом.", "source": "caller_aspect",
                     "validation": "accepted"} for a in cleaned], "skipped_aspects": skipped}
    budget = max(0, cfg.max_aspects - len(cleaned))
    if cfg.planner_mode == "llm" and budget:
        plan["planner"]["status"] = "ok"
        started = time.perf_counter()
        try:
            raw = planner_adapter(query) if planner_adapter else _ollama_aspects(query, cfg)
            if not isinstance(raw, dict) or not isinstance(raw.get("aspects"), list) or not isinstance(raw.get("constraints"), list):
                raise ValueError("malformed_structured_reply")
            required = _required_constraints(query)
            quoted = raw["constraints"]
            if any(not isinstance(x, str) or not x or x not in query for x in quoted):
                raise ValueError("constraint_quote_not_verbatim")
            if len(raw["aspects"]) > 4:
                raise ValueError("too_many_planned_aspects")
            if any(item not in quoted for item in required):
                raise ValueError("required_constraint_omitted")
            for row in raw["aspects"]:
                if not isinstance(row, dict) or not {"text", "rationale"} <= set(row):
                    raise ValueError("aspect_missing_text_or_rationale")
                text, rationale = row["text"], row["rationale"]
                normalized_text, text_error = _validate_aspect_text(text)
                if text_error or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 240:
                    raise ValueError("invalid_aspect_shape")
                if text_error == "unsafe_instruction":
                    raise ValueError("unsafe_instruction_in_aspect")
                if text_error:
                    raise ValueError(f"invalid_aspect_{text_error}")
                text = normalized_text
                norm = text.casefold()
                if norm in seen:
                    plan["skipped_aspects"].append({"aspect": text, "reason": "duplicate"})
                elif len(plan["aspects"]) >= cfg.max_aspects:
                    plan["skipped_aspects"].append({"aspect": text, "reason": "max_aspects"})
                else:
                    seen.add(norm)
                    plan["aspects"].append({"text": " ".join(text.split()), "rationale": rationale.strip(),
                                            "source": "llm_aspect", "validation": "accepted"})
            plan["constraints"] = quoted
        except Exception as exc:
            # Planner output is accepted atomically; partial aspects from a bad
            # response must never leak into the fallback search plan.
            plan["aspects"] = [row for row in plan["aspects"] if row["source"] == "caller_aspect"]
            plan["constraints"] = _required_constraints(query)
            plan["planner"].update({"status": "fallback", "reason": "planner_failed", "failure": f"{type(exc).__name__}: {exc}"})
        finally:
            plan["planner"]["elapsed_sec"] = round(time.perf_counter()-started, 4)
    elif cfg.planner_mode == "llm":
        plan["planner"].update({"status": "skipped", "reason": "aspect_budget_filled" if cfg.max_aspects else "max_aspects_zero"})
    queries = [{"query": query, "origin": "original", "aspect": None}]
    queries.extend({"query": f"{query} [дополнительный аспект: {a['text']}]", "origin": a["source"], "aspect": a["text"]}
                   for a in plan["aspects"])
    plan["queries"] = queries
    plan["executed_queries"] = [row["query"] for row in queries]
    return plan


def _hit_row(hit: Any) -> dict[str, Any]:
    row = _row(hit)
    if "chunk" in row:
        row = {**row["chunk"], "score": row.get("score"), "rank": row.get("rank")}
    return row


def _validate_dense(context: CorpusPointSearchContext, hits: list[Any], allowed: set[str], query: str) -> tuple[list[dict], list[dict]]:
    good, rejected = [], []
    seen: set[str] = set()
    for rank, hit in enumerate(hits, 1):
        row = _hit_row(hit)
        cid = str(row.get("chunk_id") or "")
        canon = context.chunk_by_id.get(cid)
        if (not canon or cid in seen or str(row.get("doc_id") or "") != str(canon["payload"].get("doc_id") or "")
                or canon["payload"].get("doc_id") not in allowed
                or (row.get("text") is not None and row["text"] != canon.get("text"))):
            rejected.append({"chunk_id": cid, "reason": "duplicate_or_noncanonical_dense_hit", "query": query})
            continue
        bad_meta = [f for f in ("point_id", "point_number", "point_identity_key", "filename")
                    if row.get(f) is not None and str(row[f]) != str(canon["payload"].get(f) or "")]
        if bad_meta:
            rejected.append({"chunk_id": cid, "reason": "dense_payload_mismatch", "fields": bad_meta, "query": query})
            continue
        seen.add(cid)
        good.append({"chunk": canon, "rank": rank, "score": row.get("score"), "query": query})
    return good, rejected


def _dense_search(context: CorpusPointSearchContext, query: str, doc_id: str | None,
                  allowed: set[str], top_k: int) -> list[Any]:
    """Require a canonical doc_id filter for injected global dense adapters too."""
    runner = context.vector_runner
    if runner is None:
        return _vector_hits(context, query, doc_id, top_k)
    params = inspect.signature(runner).parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs = {"query": query, "doc_id": doc_id, "top_k": top_k, "config": context.config}
    if "doc_ids" in params or accepts_kwargs:
        kwargs["doc_ids"] = tuple(sorted(allowed))
    elif doc_id is None:
        raise RuntimeError("global_dense_runner_missing_doc_id_allowlist")
    return list(runner(**kwargs))


def _rerank_windowed(query: str, items: list[dict[str, Any]], context: CorpusPointSearchContext,
                     cfg: FindAllConfig) -> tuple[list[dict], dict[str, Any]]:
    """Score token windows covering each full point; fail explicitly if tokenization fails."""
    if not items:
        return [], {"device": None, "elapsed_sec": 0.0, "window_count": 0, "tokenizer": None}
    from hybrid_retrieval import DEFAULT_RERANKER, rerank_local
    tokenizer = cfg.tokenizer
    if tokenizer is None:
        try:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(str(cfg.model_path or DEFAULT_RERANKER), local_files_only=True)
        except Exception as exc:
            raise RuntimeError(f"reranker_tokenizer_unavailable: {type(exc).__name__}: {exc}") from exc
    q_ids = tokenizer.encode(query, add_special_tokens=False)
    if len(q_ids) + 16 >= 512:
        raise RuntimeError(f"query_exceeds_reranker_budget:{len(q_ids)} tokens")
    window_rows = []
    by_parent: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(items):
        metadata = "\n".join(x for x in (str(item.get("doc_name") or ""),
                                            str(item.get("point_number") or "")) if x)
        metadata_tokens = len(tokenizer.encode(metadata, add_special_tokens=False))
        window_size = min(380, 512 - len(q_ids) - metadata_tokens - 16)
        if window_size < 32:
            raise RuntimeError(f"insufficient_reranker_context_budget:{item.get('doc_id')}:{item.get('point_number')}")
        overlap = min(64, window_size // 4)
        ids = tokenizer.encode(item["text"], add_special_tokens=False)
        starts = list(range(0, max(1, len(ids)), max(1, window_size - overlap))) if ids else [0]
        windows = []
        for wi, start in enumerate(starts):
            end = min(len(ids), start + window_size)
            if start >= len(ids) and ids:
                break
            windows.append((start, end, tokenizer.decode(ids[start:end], skip_special_tokens=True)))
            if end >= len(ids):
                break
        cap = cfg.max_windows_per_point
        selected = windows if cap is None else windows[:cap]
        for wi, (start, end, text) in enumerate(selected):
            wid = f"{index}:{wi}"
            row = {**item, "chunk_id": wid, "text": text, "_parent_id": index,
                   "_window": {"index": wi, "token_start": start, "token_end": end}}
            window_rows.append(row)
            by_parent[index].append(row)
        item["rerank_windows"] = {"total": len(windows), "scored": len(selected),
                                  "unprocessed_token_range": ([windows[len(selected)][0], len(ids)]
                                      if len(selected) < len(windows) and selected else None),
                                  "window_token_size": window_size, "overlap_tokens": overlap}
    reranker = context.reranker or rerank_local
    ranked, device, elapsed = reranker(query, window_rows)
    ranked_by_id = {str(r.get("chunk_id")): r for r in ranked}
    if len(ranked_by_id) != len(window_rows) or set(ranked_by_id) != {r["chunk_id"] for r in window_rows}:
        raise RuntimeError("reranker_changed_window_identity")
    out = []
    for index, item in enumerate(items):
        rows = by_parent.get(index, [])
        scored = [(float(ranked_by_id[r["chunk_id"]].get("reranker_score")), r) for r in rows]
        score, best = max(scored, key=lambda pair: pair[0]) if scored else (None, None)
        out.append({**item, "reranker_score": score, "best_window": best["_window"] if best else None,
                    "window_count": len(rows), "reranker_device": device})
    out.sort(key=lambda r: (-(r["reranker_score"] if r["reranker_score"] is not None else float("-inf")),
                            r["doc_id"], r["point_identity_key"] or ""))
    return out, {"device": device, "elapsed_sec": elapsed, "window_count": len(window_rows),
                 "tokenizer": str(getattr(tokenizer, "name_or_path", "injected"))}


_POINT_RE = re.compile(r"\bпункт(?:а|е|у|ом|ы|ов|ам|ами)?\s*(\d+(?:[._]\d+)*(?:\([а-яa-z0-9]+\))?)", re.I)
_POINT_LIST_RE = re.compile(r"\bпункт(?:ы|ов|ами|ах)?\s+((?:\d+(?:[._]\d+)*(?:\([а-яa-z0-9]+\))?\s*(?:,|и|–|-|—)\s*)+\d+(?:[._]\d+)*(?:\([а-яa-z0-9]+\))?)", re.I)
_CHAPTER_RE = re.compile(r"\b(глав(?:ой|е|у|ы|ами|ах)|раздел(?:ом|е|а|у|ами|ах))\s+([IVXLCDM]+|\d+(?:[._]\d+)*)\b", re.I)
_CHAPTER_LIST_RE = re.compile(r"\b(глав(?:ой|е|у|ы|ами|ах)|раздел(?:ом|е|а|у|ами|ах))\s+((?:[IVXLCDM]+|\d+(?:[._]\d+)*)(?:\s*(?:,|и|–|-|—)\s*(?:[IVXLCDM]+|\d+(?:[._]\d+)*))+)", re.I)
_SELF_RE = re.compile(r"настоящ(?:их|его|им|ими|ее|ая|ие)\s+(?:правил|положени|инструкци|регламент)", re.I)
_DOC_NOUN_RE = re.compile(r"\b(?:правил\w*|инструкц\w*|положени\w*|приказ\w*|постановлени\w*|"
                         r"федеральн\w*\s+закон\w*|закон\w*|гост\w*|регламент\w*|акт\w*|"
                         r"документ\w*|стандарт\w*|кодекс\w*|норм\w*)\b", re.I)
_ACRONYM_RE = re.compile(r"(?<!\w)\s*[«\"']?(?=[А-ЯЁA-Z0-9]{2,7}[»\"']?\s*[,.:]?\s*$)(?=[А-ЯЁA-Z0-9]*[А-ЯЁA-Z])[А-ЯЁA-Z0-9]{2,7}[»\"']?\s*[,.:]?\s*$")
_REF_MARKER_RE = re.compile(r"\b(?:пункт(?:а|е|у|ом|ы|ов|ам|ами)?|глав(?:ой|е|у|ы)|раздел(?:ом|е|а|у)?)\b", re.I)


def _norm_no_punct(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.lower().replace("ё", "е"), re.UNICODE))


def _looks_like_acronym(value: str) -> bool:
    token = value.strip().strip("«»\"'.,:;()[]")
    return (re.fullmatch(r"[А-ЯЁа-яёA-Za-z0-9]{2,7}", token) is not None
            and sum(ch.isalpha() and ch.isupper() for ch in token) >= 2)


def _resolve_doc_reference(corpus: CanonicalCorpus, text: str, current_doc: str,
                           section_number: str | None = None) -> tuple[str | None, str, dict[str, Any]]:
    """Resolve only the exact address phrase already linked to one locator."""
    fragment = text.strip()
    self_ref = re.fullmatch(
        r"\s*настоящ(?:их|его|им|ими|ее|ая|ие)\s+(?:правил\w*|инструкц\w*|положени\w*|регламент\w*)"
        r"\s*[,.:]?\s*", fragment, re.I)
    if self_ref:
        return current_doc, "resolved_current", {"proof": "explicit_self_reference", "quoted_address": self_ref.group(0)}
    # Only full aliases/titles bounded as phrases resolve an external document.
    normalized = f" {_norm_no_punct(fragment)} "
    matches: set[str] = set()
    for entry in corpus.catalog_by_key.values():
        aliases = [entry.get("doc_name", ""), entry.get("short_title", ""), *entry.get("aliases", [])]
        for alias in aliases:
            phrase = _norm_no_punct(str(alias or ""))
            alias_text = str(alias or "")
            short_acronym = 2 <= len(phrase) <= 7 and re.fullmatch(r"[А-ЯЁA-Z0-9]{2,7}", alias_text.strip()) is not None
            if (len(phrase) >= 8 or short_acronym) and normalized.strip() == phrase:
                matches.add(str(entry["doc_id"]))
    if len(matches) == 1:
        return next(iter(matches)), "resolved_named_document", {"proof": "unique_full_alias", "matches": sorted(matches)}
    if len(matches) > 1:
        return None, "ambiguous_document", {"matches": sorted(matches)}
    if _ACRONYM_RE.fullmatch(fragment) or _looks_like_acronym(fragment):
        return None, "ambiguous_document", {"reason": "short_document_acronym_not_uniquely_matched"}
    # A local alias definition proves only a bare generic noun (e.g. «Правил»).
    # Qualifiers after it may name another act, so they must resolve by an exact
    # catalog alias/number or remain ambiguous.
    alias_fragment = re.sub(
        r"^\s*(?:глав\w*|раздел\w*)\s+[IVXLCDM]+(?:\s*[,;:]\s*)?",
        "", fragment, flags=re.I)
    bare_generic = re.fullmatch(
        r"\s*(?:[«\"']\s*)?(?:правил\w*|инструкц\w*)(?:\s*[»\"'])?\s*[,.:]?(?:\s+далее)?\s*",
        alias_fragment, re.I)
    if bare_generic:
        definition = _verified_local_alias_definition(corpus, current_doc, "правила")
        if definition:
            return current_doc, "resolved_current_by_definition", definition
        return None, "ambiguous_document", {"reason": "generic_document_noun_without_unique_name_or_self_reference"}
    if _DOC_NOUN_RE.search(fragment):
        number_match = re.search(r"(?:№|номер\s*)(\d+(?:[-./]\d+)*)", fragment, re.I)
        number = number_match.group(1) if number_match else None
        if number:
            known_numbers = set()
            for entry in corpus.catalog_by_key.values():
                raw = entry.get("order_numbers") or []
                values = raw if isinstance(raw, (list, tuple, set)) else [raw]
                known_numbers.update(str(v) for v in values)
                known_numbers.update(str(entry.get(k)) for k in ("doc_number", "order_number") if entry.get(k) is not None)
            if number not in known_numbers:
                return None, "document_not_in_corpus", {"reason": "explicit_document_number_not_in_canonical_scope", "number": number}
        return None, "ambiguous_document", {
            "reason": "document_kind_or_number_without_proven_full_title_or_issuer",
            "number": number}
    if fragment.strip():
        return None, "ambiguous_document", {"reason": "nonempty_linked_address_not_proven"}
    return current_doc, "resolved_current_by_context", {"proof": "no_document_noun_in_linked_address"}


def _roman_value(value: str) -> int | None:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    text = value.upper()
    if not text or any(ch not in vals for ch in text):
        return None
    total = 0
    for i, ch in enumerate(text):
        n = vals[ch]
        total += -n if i + 1 < len(text) and n < vals[text[i + 1]] else n
    return total


def _verified_local_alias_definition(corpus: CanonicalCorpus, doc_id: str, alias: str) -> dict[str, Any] | None:
    """Require a same-clause full catalog title immediately followed by (далее — alias)."""
    entry = corpus.entry_for_doc(doc_id)
    titles = [str(x) for x in (entry.get("doc_name"), entry.get("short_title"), entry.get("doc_title_full"),
                                *entry.get("aliases", [])) if x and len(_norm_no_punct(str(x))) >= 12]
    definition_re = re.compile(r"\(\s*далее\s*[-—–:]\s*[«\"']?([\w-]+)[»\"']?\s*\)", re.I)
    for chunk in corpus.docs.get(doc_id, []):
        text = str(chunk.get("text") or "")
        for match in definition_re.finditer(text):
            if _norm_no_punct(match.group(1)) != _norm_no_punct(alias):
                continue
            clause_start = max((text.rfind(mark, 0, match.start()) + 1 for mark in ("\n", ".", "!", "?", ";")), default=0)
            prefix = text[clause_start:match.start()]
            prefix_norm = _norm_no_punct(prefix)
            matched_title = next((title for title in titles
                                  if prefix_norm.endswith(_norm_no_punct(title))), None)
            # The introductory clause can itself declare the document title:
            # “Настоящие Правила <title> (далее — Правила)”. This is direct
            # grammatical evidence even if the catalog title names the approving act.
            self_title = re.fullmatch(
                r"\s*(?:\d+(?:[._]\d+)*[.)]?\s*)?настоящ(?:ие|их|его|ая|им)\s+"
                r"правил[а-яё]*(?:\s+[\w№-]+)+\s*", prefix, re.I)
            if self_title and _norm_no_punct(alias) == "правила":
                matched_title = prefix.strip()
            if matched_title:
                quote_start = clause_start
                return {"proof": "verified_in_document_alias_definition", "doc_id": doc_id,
                        "chunk_id": chunk["chunk_id"], "quoted_definition": text[quote_start:match.end()],
                        "full_title": matched_title, "alias": alias,
                        "definition_form": "self_title" if self_title else "catalog_title_suffix"}
    return None


def _linked_address_fragment(text: str, start: int, end: int, corpus: CanonicalCorpus) -> tuple[str, int, int]:
    """Return only an address phrase syntactically adjacent to a locator.

    Supported forms keep the document noun/title directly before or after the
    point/chapter marker. A later citation in the same sentence is never scanned.
    """
    suffix = text[end:]
    suffix_start = end + (len(suffix) - len(suffix.lstrip()))
    suffix = suffix.lstrip()
    # End the address fragment before another locator, clause, or sentence.
    stop = re.search(r"[,;.!?\n]|\b(?:пункт(?:а|е|у|ом|ы|ов|ам|ами)?|глава|главой|главу|раздел(?:а|е|ом|у)?)\b|\b(?:а\s+также|либо|однако)\b", suffix, re.I)
    tail = suffix[:stop.start()] if stop else suffix[:180]
    tail_leading = len(tail) - len(tail.lstrip(" ,:"))
    candidate = tail[tail_leading:]
    candidate = re.sub(r"\s+(?:и|а|либо)\s*$", "", candidate, flags=re.I).rstrip(" ,:")
    self_prefix = re.match(
        r"настоящ(?:их|его|им|ими|ее|ая|ие)\s+(?:правил\w*|инструкц\w*|положени\w*|регламент\w*)\b",
        candidate, re.I)
    remainder = candidate[self_prefix.end():].lstrip() if self_prefix else ""
    self_boundary = (not remainder or remainder[0] in ",;:.!?" or
                     re.match(r"^(?:если|когда|в\s+случае|при\s+условии|предусмотр\w*|установленн\w*|"
                              r"применяем\w*|регулиру\w*|указанн\w*|определенн\w*|содержащ\w*|"
                              r"являющ\w*|составляющ\w*|обязательн\w*|действу\w*|долж\w*|мож\w*)\b",
                              remainder, re.I) is not None)
    if self_prefix and self_boundary:
        # The grammatical self-address ends before the predicate/condition.
        return self_prefix.group(0), suffix_start + tail_leading, suffix_start + tail_leading + self_prefix.end()
    if candidate and (_SELF_RE.match(candidate) or _DOC_NOUN_RE.match(candidate) or _ACRONYM_RE.fullmatch(candidate) or _looks_like_acronym(candidate)
                      or re.match(r"неизвестн\w*\s+(?:нормативн\w*\s+)?(?:документ\w*|акт\w*)", candidate, re.I)):
        return candidate, suffix_start + tail_leading, suffix_start + tail_leading + len(candidate)
    # For a name before the locator, require the whole clause-level noun phrase
    # to match the catalog alias. Preserve unknown left qualifiers in the address
    # so the resolver will fail closed instead of accepting an alias suffix.
    prefix = text[:start]
    for entry in corpus.catalog_by_key.values():
        aliases = [entry.get("doc_name", ""), entry.get("short_title", ""), *entry.get("aliases", [])]
        for alias in aliases:
            alias_norm = _norm_no_punct(str(alias or ""))
            short_acronym = 2 <= len(alias_norm) <= 7 and re.fullmatch(r"[А-ЯЁA-Z0-9]{2,7}", str(alias).strip()) is not None
            if not alias or (len(alias_norm) < 8 and not short_acronym):
                continue
            match = re.search(rf"{re.escape(str(alias))}\s*$", prefix, re.I)
            if match:
                left = prefix[:match.start()].strip()
                left = re.sub(r"^(?:см\.?|согласно|по|в соответствии с|на основании)\s+", "", left, flags=re.I).strip()
                fragment = (left + " " + match.group(0).strip()).strip() if left else match.group(0).strip()
                return fragment, match.start() - (len(left)+1 if left else 0), match.end()
    normalized = f" {_norm_no_punct(candidate)} "
    for entry in corpus.catalog_by_key.values():
        aliases = [entry.get("doc_name", ""), entry.get("short_title", ""), *entry.get("aliases", [])]
        for alias in aliases:
            phrase = _norm_no_punct(str(alias or ""))
            if len(phrase) >= 8 and f" {phrase} " in normalized:
                return candidate, suffix_start + tail_leading, suffix_start + tail_leading + len(candidate)
    if candidate and (_DOC_NOUN_RE.search(candidate) or _ACRONYM_RE.fullmatch(candidate) or _looks_like_acronym(candidate)
                      or re.match(r"^[«\"'][^»\"']+[»\"']", candidate)):
        return candidate, suffix_start + tail_leading, suffix_start + tail_leading + len(candidate)
    # A directly preceding generic/self address is also syntactically adjacent.
    prefix = text[:start]
    prefix_match = re.search(r"((?:настоящ(?:их|его|им|ими|ее|ая|ие)\s+)?(?:правил\w*|инструкц\w*|положени\w*|приказ\w*|постановлени\w*|"
                             r"федеральн\w*\s+закон\w*|закон\w*|гост\w*|регламент\w*|акт\w*|документ\w*|"
                             r"стандарт\w*|кодекс\w*|норм\w*))\s*$", prefix, re.I)
    if prefix_match:
        left = prefix[:prefix_match.start(1)].strip()
        left = re.sub(r"^(?:см\.?|согласно|по|в соответствии с|на основании)\s+", "", left, flags=re.I).strip()
        if left:
            fragment = (left + " " + prefix_match.group(1)).strip()
            return fragment, prefix_match.start(1) - len(left)-1, prefix_match.end(1)
        return prefix_match.group(1), prefix_match.start(1), prefix_match.end(1)
    for entry in corpus.catalog_by_key.values():
        aliases = [entry.get("doc_name", ""), entry.get("short_title", ""), *entry.get("aliases", [])]
        for alias in aliases:
            if not alias:
                continue
            match = re.search(rf"{re.escape(str(alias))}\s*$", prefix, re.I)
            if match:
                return match.group(0), match.start(), match.end()
    acronym = re.search(r"(?<!\w)[А-ЯЁа-яёA-Za-z0-9]{2,7}\s*$", prefix)
    if acronym and _looks_like_acronym(acronym.group(0)):
        return acronym.group(0).strip(), acronym.start(), acronym.end()
    left_clause_start = max((text.rfind(mark, 0, start) + 1 for mark in ("\n", ".", "!", "?", ";")), default=0)
    left_clause = prefix[left_clause_start:]
    left_clause = re.sub(r"^\s*(?:см\.?|согласно|по|в соответствии с|на основании)\s*", "", left_clause, flags=re.I).strip()
    if left_clause and (_DOC_NOUN_RE.search(left_clause) or _ACRONYM_RE.search(left_clause)):
        return left_clause, left_clause_start + len(prefix[left_clause_start:]) - len(prefix[left_clause_start:].lstrip()), start
    return "", start, start


@lru_cache(maxsize=256)
def _structured_heading_texts(path: str) -> frozenset[str]:
    try:
        data = json.loads(open(path, encoding="utf-8-sig").read())
    except (OSError, ValueError):
        return frozenset()
    return frozenset(_norm_no_punct(str(row.get("text") or "")) for row in data.get("paragraphs", [])
                     if row.get("is_heading") and str(row.get("text") or "").strip())


def _verified_heading_segment(corpus: CanonicalCorpus, chunk: dict[str, Any], number: str, kind: str) -> dict[str, Any] | None:
    payload = chunk.get("payload", {})
    heading_path = payload.get("heading_path")
    segments = [str(x) for x in heading_path] if isinstance(heading_path, list) else str(
        payload.get("heading_path_text") or "").split(" > ")
    expected = _roman_value(number) if re.fullmatch(r"[IVXLCDM]+", number, re.I) else number.replace("_", ".")
    for segment in segments:
        explicit = re.match(r"\s*(глава|раздел)\s+([IVXLCDM]+|\d+(?:[._]\d+)*)\s*$", segment, re.I)
        if explicit:
            explicit_kind = "chapter" if explicit.group(1).lower() == "глава" else "section"
            raw = explicit.group(2)
            value = _roman_value(raw) if re.fullmatch(r"[IVXLCDM]+", raw, re.I) else raw.replace("_", ".")
            if value == expected and explicit_kind == kind:
                return {"heading_path_segment": segment, "heading_type_proof": "explicit_typed_heading_path_segment"}
        # Roman dotted segments are normative chapter headings only if this exact
        # path segment is also an is_heading paragraph in the paired source file.
        if kind == "chapter" and re.fullmatch(r"\s*[IVXLCDM]+\s*\.\s*.+", segment, re.I):
            raw = re.match(r"\s*([IVXLCDM]+)", segment, re.I).group(1)
            if _roman_value(raw) != expected:
                continue
            filename = payload.get("filename")
            if not filename:
                continue
            source_path = (corpus.root / "output/marked_docs" /
                           Path(str(filename)).with_suffix(".structured.json").name).resolve()
            marked = (corpus.root / "output/marked_docs").resolve()
            if marked not in source_path.parents:
                continue
            heading_texts = _structured_heading_texts(str(source_path)) if source_path.is_file() else frozenset()
            if _norm_no_punct(segment) in heading_texts:
                return {"heading_path_segment": segment, "heading_type_proof": "exact_roman_heading_segment_and_structured_is_heading",
                        "structured_source": str(source_path)}
    return None


def _heading_targets(corpus: CanonicalCorpus, doc_id: str, number: str, kind: str) -> list[dict[str, Any]]:
    field = "chapter_number" if kind == "chapter" else "section_number"
    target_val = _roman_value(number) if re.fullmatch(r"[IVXLCDM]+", number, re.I) else number.replace("_", ".")
    found: dict[str, dict[str, Any]] = {}
    for c in corpus.docs.get(doc_id, []):
        p = c.get("payload", {})
        raw = str(p.get(field) or "")
        val = _roman_value(raw) if re.fullmatch(r"[IVXLCDM]+", raw, re.I) else raw.replace("_", ".")
        explicit_number_match = val == target_val
        heading_proof = _verified_heading_segment(corpus, c, number, kind) if not explicit_number_match else {
            "heading_type_proof": f"canonical_{field}_payload"}
        if explicit_number_match or heading_proof:
            identity = str(p.get("point_identity_key") or "") or None
            found[identity or c["chunk_id"]] = {"doc_id": doc_id, "identity": identity, "point_number": p.get("point_number"),
                                                "heading_path_text": p.get("heading_path_text"),
                                                "heading_evidence": heading_proof}
    return list(found.values())


def _resolve_point(corpus: CanonicalCorpus, doc_id: str, label: str,
                   context: CorpusPointSearchContext | None = None) -> tuple[list[dict], str, str | None]:
    norm = label.replace("_", ".")
    matches: dict[str, dict] = {}
    for chunk in corpus.docs.get(doc_id, []):
        p = chunk.get("payload", {})
        if str(p.get("point_number") or "").replace("_", ".") == norm:
            identity = str(p.get("point_identity_key") or "")
            if identity:
                matches[identity] = {"doc_id": doc_id, "identity": identity, "point_number": p.get("point_number")}
    rows = list(matches.values())
    if len(rows) == 1 and context is not None:
        identity = rows[0]["identity"]
        parts = context.groups.get((doc_id, identity), [])
        if parts:
            _ident, basis = _point_identity(parts[0], context)
            assembly = assemble_point_by_identity(context, doc_id, identity, basis or "point_identity_key")
            if assembly.get("source_check") == "first_chunk_text_does_not_confirm_point_label":
                return [], "target_number_unverified", assembly.get("source_check")
    elif len(rows) == 1:
        identity = rows[0]["identity"]
        parts = sorted((c for c in corpus.docs.get(doc_id, [])
                        if str(c.get("payload", {}).get("point_identity_key") or "") == identity),
                       key=chunk_order)
        if parts:
            payload_label = str(parts[0].get("payload", {}).get("point_number") or "").replace("_", ".")
            escaped = re.escape(payload_label)
            if payload_label and not re.match(rf"^{escaped}(?:\s|\)|\.(?!\d))", str(parts[0].get("text") or "").lstrip()):
                return [], "target_number_unverified", "first_chunk_text_does_not_confirm_point_label"
    return rows, ("resolved" if len(rows) == 1 else "ambiguous_point" if rows else "point_not_found"), None


def extract_references(corpus: CanonicalCorpus, point: dict[str, Any],
                       context: CorpusPointSearchContext | None = None,
                       request_cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Conservative deterministic reference extraction from the complete point text."""
    text = point.get("text") or ""
    refs: list[dict[str, Any]] = []
    consumed: list[tuple[int, int]] = []
    doc_cache = request_cache.setdefault("doc_resolution", {}) if request_cache is not None else None
    point_cache = request_cache.setdefault("point_resolution", {}) if request_cache is not None else None

    def resolve_doc(fragment: str, current: str, section: str | None = None):
        if request_cache is not None:
            request_cache["lookup_calls"] = request_cache.get("lookup_calls", 0) + 1
        key = (fragment, current, section)
        if doc_cache is not None and key in doc_cache:
            request_cache["lookup_cache_hits"] = request_cache.get("lookup_cache_hits", 0) + 1
            return copy.deepcopy(doc_cache[key])
        value = _resolve_doc_reference(corpus, fragment, current, section)
        if doc_cache is not None:
            doc_cache[key] = copy.deepcopy(value)
        return value

    def resolve_point(doc: str, label: str):
        if request_cache is not None:
            request_cache["lookup_calls"] = request_cache.get("lookup_calls", 0) + 1
        key = (doc, label)
        if point_cache is not None and key in point_cache:
            request_cache["lookup_cache_hits"] = request_cache.get("lookup_cache_hits", 0) + 1
            return copy.deepcopy(point_cache[key])
        value = _resolve_point(corpus, doc, label, context)
        if point_cache is not None:
            point_cache[key] = copy.deepcopy(value)
        return value

    def add(start: int, end: int, typ: str, label: str, targets: list[dict], status: str,
            reason: str | None, target_doc: str | None, address_status: str, proof: dict,
            address: tuple[str, int, int] = ("", 0, 0)):
        address_text, address_start, address_end = address
        row = {"reference_id": f"{point['doc_id']}:{point.get('point_identity_key')}:{start}",
                     "source_doc_id": point["doc_id"], "source_identity": point.get("point_identity_key"),
                     "source_point_number": point.get("point_number"), "quoted_text": text[start:end],
                     "char_start": start, "char_end": end, "type": typ, "label": label,
                     "target_doc_id": target_doc, "address_status": address_status,
                     "address_proof": proof, "status": status, "reason": reason,
                     "resolution_status": status, "resolution_reason": reason,
                     "targets": targets,
                     "target_doc_ids": list(dict.fromkeys(t.get("doc_id") for t in targets if t.get("doc_id"))),
                     "target_point_numbers": [t.get("point_number") for t in targets],
                     "address_text": address_text, "address_char_start": address_start,
                     "address_char_end": address_end,
                     "target_point_identity_keys": [t.get("identity") for t in targets]}
        if len(targets) == 1:
            row["target_point_identity_key"] = targets[0].get("identity")
        refs.append(row)

    # A listed sequence/range takes precedence over individual point matches.
    for m in _POINT_LIST_RE.finditer(text):
        labels = re.findall(r"\d+(?:[._]\d+)*(?:\([а-яa-z0-9]+\))?", m.group(1), re.I)
        range_match = re.search(r"(\d+(?:[._]\d+)*)\s*[–—-]\s*(\d+(?:[._]\d+)*)", m.group(1))
        typ = "range" if range_match else "point"
        address = _linked_address_fragment(text, m.start(), m.end(), corpus)
        doc_id, addr_status, proof = resolve_doc(address[0], point["doc_id"])
        targets, status, reason = [], "resolved", None
        if doc_id is None:
            status, reason = addr_status, proof.get("reason")
        else:
            if range_match:
                a, b = range_match.groups()
                if "." in a or "_" in a or "." in b or "_" in b or int(b) < int(a) or int(b)-int(a) > 100:
                    status, reason = "unresolved_range", "range_boundaries_not_safely_expandable"
                else:
                    labels = [str(n) for n in range(int(a), int(b)+1)]
            range_invalid = False
            if range_match and status == "resolved":
                scope_signatures = []
                for label in labels:
                    point_rows, point_state, _ = resolve_point(doc_id, label)
                    if point_state != "resolved":
                        range_invalid = True
                        break
                    chunks = [c for c in corpus.docs.get(doc_id, [])
                              if str(c.get("payload", {}).get("point_identity_key") or "") == point_rows[0]["identity"]]
                    paths = {json.dumps(c.get("payload", {}).get("heading_path") or
                                        c.get("payload", {}).get("heading_path_text") or "", ensure_ascii=False, sort_keys=True)
                             for c in chunks}
                    scope_signatures.append(next(iter(paths)) if len(paths) == 1 else "")
                if not scope_signatures or not scope_signatures[0] or any(s != scope_signatures[0] for s in scope_signatures):
                    range_invalid = True
                if range_invalid:
                    status, reason, targets = "unresolved_range", "range_crosses_or_lacks_structural_scope_evidence", []
            if not range_invalid:
                for label in labels:
                    found, state, detail = resolve_point(doc_id, label)
                    if state != "resolved":
                        status, reason = state, detail or f"target_{label}_{state}"
                        targets = []
                        break
                    targets.extend(found)
            if len(targets) > 100:
                status, reason = "reference_target_limit", "range_target_cap"
                targets = targets[:100]
        add(m.start(), m.end(), typ, ",".join(labels), targets, status, reason, doc_id, addr_status, proof, address)
        consumed.append(m.span())

    for m in _POINT_RE.finditer(text):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        before = text[max(0, m.start()-2):m.start()]
        if not _REF_MARKER_RE.search(before + m.group(0)[:m.group(0).find(m.group(1))]):
            continue
        label = m.group(1)
        address = _linked_address_fragment(text, m.start(), m.end(), corpus)
        if re.search(r"неизвестн\w*\s+(?:нормативн\w*\s+)?(?:документ\w*|акт\w*)", address[0], re.I):
            add(m.start(), m.end(), "point", label, [], "document_not_in_corpus",
                "named_document_not_present_in_canonical_scope", None, "document_not_in_corpus", {}, address)
            consumed.append(m.span())
            continue
        doc_id, addr_status, proof = resolve_doc(address[0], point["doc_id"])
        found, state, detail = resolve_point(doc_id, label) if doc_id else ([], addr_status, None)
        add(m.start(), m.end(), "point", label, found, state, None if state == "resolved" else (detail or state),
            doc_id, addr_status, proof, address)
        consumed.append(m.span())

    for m in _CHAPTER_LIST_RE.finditer(text):
        kind = "chapter" if m.group(1).lower().startswith("глав") else "section"
        labels = re.findall(r"[IVXLCDM]+|\d+(?:[._]\d+)*", m.group(2), re.I)
        address = _linked_address_fragment(text, m.start(), m.end(), corpus)
        doc_id, addr_status, proof = resolve_doc(address[0], point["doc_id"], labels[0] if labels else None)
        all_targets = []
        status, reason = "resolved", None
        if doc_id is None:
            status, reason = addr_status, proof.get("reason")
        else:
            for label in labels:
                targets = _heading_targets(corpus, doc_id, label, kind)
                if not targets:
                    status, reason = "unresolved_section", f"heading_{label}_membership_not_proven"
                all_targets.extend(targets)
        # One edge represents the whole cited list and retains all exact targets.
        add(m.start(), m.end(), kind, ",".join(labels), all_targets, status, reason, doc_id, addr_status, proof, address)
        consumed.append(m.span())

    for m in _CHAPTER_RE.finditer(text):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        kind = "chapter" if m.group(1).lower().startswith("глав") else "section"
        label = m.group(2).upper()
        address = _linked_address_fragment(text, m.start(), m.end(), corpus)
        doc_id, addr_status, proof = resolve_doc(address[0], point["doc_id"], label)
        targets = []
        status, reason = ((addr_status, proof.get("reason")) if doc_id is None
                          else ("unresolved_section", "heading_membership_not_proven"))
        if doc_id:
            targets = _heading_targets(corpus, doc_id, label, kind)
            if targets:
                status, reason = "resolved", None
            else:
                status = "unresolved_section"
        add(m.start(), m.end(), kind, label, targets, status, reason, doc_id, addr_status, proof, address)
        consumed.append(m.span())

    # Preserve a link to an explicitly named document even if no target locator exists.
    alias_hits: dict[tuple[int, int], dict[str, Any]] = {}
    for entry in corpus.catalog_by_key.values():
        for name in [entry.get("doc_name"), entry.get("short_title"), *entry.get("aliases", [])]:
            if not name or len(str(name)) < 8:
                continue
            for m in re.finditer(re.escape(str(name)), text, re.I):
                # A document-only edge needs an explicit right boundary. Without
                # punctuation/closing quotes, following words may extend an
                # unrecognized title and a shorter catalog alias is unsafe.
                if not re.match(r"^\s*(?:[.,;:!?)]|[»\"']|$)", text[m.end():]):
                    continue
                key = m.span()
                row = alias_hits.setdefault(key, {"text": m.group(0), "doc_ids": set()})
                row["doc_ids"].add(str(entry["doc_id"]))
    spans = sorted(alias_hits, key=lambda span: (span[0], -(span[1]-span[0]), span[1]))
    for span in spans:
        start, end = span
        # Prefer the longest full catalog phrase at a location. A shorter alias
        # inside it cannot hide the more specific document title.
        if any(other_start <= start and end <= other_end and (other_end-other_start) > (end-start)
               for other_start, other_end in spans):
            continue
        if any(a <= start < b for a, b in consumed):
            continue
        matching = alias_hits[span]["doc_ids"]
        target = next(iter(matching)) if len(matching) == 1 else None
        state = "document_only" if target else "ambiguous_document"
        add(start, end, "document", alias_hits[span]["text"], [], state,
            None if target else "duplicate_document_alias", target,
            "resolved_named_document" if target else "ambiguous_document",
            {"proof": "unique_full_alias" if target else "duplicate_alias", "matches": sorted(matching)})
        consumed.append(span)
    refs.sort(key=lambda r: (r["char_start"], r["char_end"], r["reference_id"]))
    return refs


def _assemble_target(context: CorpusPointSearchContext, ref_target: dict[str, Any]) -> dict[str, Any] | None:
    did, identity = ref_target["doc_id"], ref_target["identity"]
    parts = context.groups.get((did, identity), [])
    if not parts:
        return None
    _ident, basis = _point_identity(parts[0], context)
    assembly = assemble_point_by_identity(context, did, identity, basis or "point_identity_key")
    entry = context.corpus.entry_for_doc(did)
    return {"doc_id": did, "doc_name": entry.get("doc_name", ""), "point_number": parts[0]["payload"].get("point_number"),
            "point_identity_key": identity, "text": assembly["text"], "complete": assembly["complete"],
            "source_check": assembly["source_check"], "chunk_ids": assembly["all_point_chunk_ids"]}


def _expand_references(context: CorpusPointSearchContext, direct: list[dict], cfg: FindAllConfig) -> tuple[list[dict], list[dict], dict[str, Any]]:
    refs: list[dict] = []
    points: dict[tuple[str, str], dict] = {}
    direct_by_key = {(x["doc_id"], x["point_identity_key"]): x for x in direct}
    assembly_cache: dict[tuple[str, str], dict | None] = {}
    edge_cache: dict[tuple[str, str, str], list[dict]] = {}
    resolver_lookup_cache: dict[str, Any] = {}
    counts = {"resolver_calls": 0, "resolver_cache_hits": 0, "assembly_calls": 0, "assembly_cache_hits": 0,
              "direct_lookup_count": 0, "direct_lookup_cache_hits": 0}

    def edges_for(item: dict) -> list[dict]:
        key = (str(item.get("doc_id")), str(item.get("point_identity_key")), str(item.get("text") or ""))
        counts["resolver_calls"] += 1
        if cfg.reference_cache and key in edge_cache:
            counts["resolver_cache_hits"] += 1
            return copy.deepcopy(edge_cache[key])
        if "request_cache" in inspect.signature(extract_references).parameters:
            found = extract_references(context.corpus, item, context,
                                       request_cache=resolver_lookup_cache if cfg.reference_cache else None)
        else:  # Preserve injected resolvers used by offline callers and tests.
            found = extract_references(context.corpus, item, context)
        if cfg.reference_cache:
            edge_cache[key] = copy.deepcopy(found)
        return found

    def materialize(target: dict) -> dict | None:
        key = (str(target.get("doc_id")), str(target.get("identity")))
        counts["assembly_calls"] += 1
        if cfg.reference_cache and key in assembly_cache:
            counts["assembly_cache_hits"] += 1
            return copy.deepcopy(assembly_cache[key])
        full = _assemble_target(context, target)
        if cfg.reference_cache:
            assembly_cache[key] = copy.deepcopy(full)
        return full

    def gather(sources: list[tuple[dict, int]]) -> list[tuple[dict, dict, int, tuple[int, int, int]]]:
        candidates = []
        for source_rank, (source, depth) in enumerate(sources):
            for edge_offset, edge in enumerate(edges_for(source)):
                # Capture the parser outcome before applying external, depth, or budget limits.
                edge.setdefault("resolution_status", edge.get("status"))
                edge.setdefault("resolution_reason", edge.get("reason"))
                # A parser-level target cap is legitimate only after the locator
                # resolved. Ambiguous/unresolved target lists remain diagnostic evidence.
                resolved_status = (edge.get("status") == "resolved"
                                   and edge.get("resolution_status") == "resolved") or (
                    edge.get("status") == "reference_target_limit"
                    and (edge.get("resolution_status") == "resolved"
                         or (edge.get("type") == "range"
                             and edge.get("resolution_status") == "reference_target_limit"
                             and edge.get("resolution_reason") == "range_target_cap")))
                if not resolved_status:
                    if edge.get("targets"):
                        edge["traversal_status"] = "resolution_unverified"
                        edge["resolution_skipped_target_count"] = len(edge["targets"])
                        edge["resolution_skip_reason"] = edge.get("status") or "missing_resolution_status"
                    edge["_source_depth"] = depth
                    refs.append(edge)
                    continue
                if edge.get("target_doc_id") and edge["target_doc_id"] != source["doc_id"] and not cfg.follow_external:
                    edge["status"] = "external_not_followed"
                    edge["reason"] = "follow_external_false"
                    edge["omitted_target_count"] = len(edge["targets"])
                    edge["targets"] = []
                if len(edge["targets"]) > cfg.max_targets_per_reference:
                    edge["omitted_target_count"] = len(edge["targets"]) - cfg.max_targets_per_reference
                    edge["status"] = "reference_target_limit"
                    edge["reason"] = "per_reference_target_cap"
                    edge["targets"] = edge["targets"][:cfg.max_targets_per_reference]
                edge["_source_depth"] = depth
                refs.append(edge)
                for target_offset, target in enumerate(edge.get("targets", [])):
                    target["depth"] = depth + 1
                    target["via_reference_id"] = edge["reference_id"]
                    candidates.append((edge, target, depth, (source_rank, edge_offset, target_offset)))
        return candidates

    frontier = [(row, 0) for row in direct]
    direct_incoming: dict[tuple[str, str], list[str]] = defaultdict(list)
    while frontier:
        depth = frontier[0][1]
        candidates = gather(frontier)
        if depth >= cfg.reference_depth:
            for edge, _target, _d, _ord in candidates:
                edge["traversal_status"] = "depth_limited"
                edge["depth_limited"] = True
            break
        exact_types = {"point", "point_list", "point_range", "range"}
        candidates.sort(key=lambda c: ((0 if c[0].get("type") in exact_types else 1)
                                      if cfg.reference_priority == "exact_first" else 0, c[3],
                                      str(c[1].get("doc_id")), str(c[1].get("identity"))))
        next_frontier: list[tuple[dict, int]] = []
        expanded: set[tuple[str, str]] = set()
        for edge, target, _d, _ord in candidates:
            key = (str(target.get("doc_id")), str(target.get("identity")))
            counts["direct_lookup_count"] += 1
            if key in direct_by_key:
                counts["direct_lookup_cache_hits"] += 1
                row = direct_by_key[key]
                row["roles"] = ["direct", "reference"]
                if edge["reference_id"] not in direct_incoming[key]:
                    direct_incoming[key].append(edge["reference_id"])
                target["role"] = "direct+reference"
                continue
            if key in points:
                incoming = points[key].setdefault("incoming_reference_ids", [])
                if edge["reference_id"] not in incoming:
                    incoming.append(edge["reference_id"])
                target["role"] = "reference"
                edge["traversal_status"] = "cycle_or_already_expanded"
                continue
            if len(points) >= cfg.max_reference_points:
                edge["status"] = "reference_target_limit"
                edge["reason"] = "global_reference_point_budget"
                edge.setdefault("budget_limited_targets", []).append({"doc_id": key[0], "identity": key[1]})
                continue
            full = materialize(target)
            if full is None:
                edge.setdefault("unmaterialized_targets", []).append({"doc_id": key[0], "identity": key[1], "reason": "assembly_failed"})
                continue
            full.update({"roles": ["reference"], "depth": depth + 1, "incoming_reference_ids": [edge["reference_id"]]})
            points[key] = full
            target.update({"depth": depth + 1, "role": "reference"})
            if key not in expanded:
                expanded.add(key)
                next_frontier.append((full, depth + 1))
        frontier = next_frontier
    for key, row in direct_by_key.items():
        if direct_incoming.get(key):
            row["incoming_reference_ids"] = direct_incoming[key]
    for edge in refs:
        edge.pop("_source_depth", None)
        edge["target_roles"] = [t.get("role") for t in edge.get("targets", [])]
    return list(points.values()), refs, {"resolved_reference_point_count": len(points),
        "reference_edge_count": len(refs),
        "unresolved_edge_count": sum(r["status"] not in {"resolved", "document_only"} for r in refs),
        "reference_cache": {**counts, "resolver_lookup_calls": resolver_lookup_cache.get("lookup_calls", 0),
                            "resolver_lookup_cache_hits": resolver_lookup_cache.get("lookup_cache_hits", 0),
                            "resolver_cached_addresses": len(resolver_lookup_cache.get("doc_resolution", {})),
                            "resolver_cached_point_labels": len(resolver_lookup_cache.get("point_resolution", {}))}}


def rerank_direct_candidates(query: str, candidates: list[dict], context: CorpusPointSearchContext,
                             cfg: FindAllConfig, query_plan: dict[str, Any] | None = None) -> tuple[list[dict], dict[str, Any]]:
    """Run the production ranking implementation on a saved complete candidate list.

    This is also the offline snapshot evaluation entry point. It does no retrieval,
    planning, corpus writes, or reference expansion.
    """
    reranked, rerank_diag = _rerank_windowed(query, candidates, context, cfg)
    if cfg.ranking_mode != "aspect_interleave":
        return reranked, rerank_diag
    base_keys = [(x["doc_id"], x["point_identity_key"]) for x in reranked]
    if len(base_keys) != len(candidates) or len(set(base_keys)) != len(base_keys):
        raise RuntimeError("original_reranker_changed_candidate_identity_cardinality")
    if any(not isinstance(x.get("reranker_score"), (int, float)) or
           not math.isfinite(float(x["reranker_score"])) for x in reranked):
        raise RuntimeError("original_reranker_returned_invalid_score")
    aspect_queries = [q for q in (query_plan or {}).get("queries", []) if q.get("origin") != "original"]
    if not aspect_queries:
        rerank_diag["aspect_interleave"] = {"status": "skipped", "reason": "no_accepted_aspects", "elapsed_sec": 0.0}
        return reranked, rerank_diag
    original_by_key = {(x["doc_id"], x["point_identity_key"]): x for x in reranked}
    original_order = [(x["doc_id"], x["point_identity_key"]) for x in reranked]
    aspect_lists, aspect_metrics = [], []
    aspect_started = time.perf_counter()
    try:
        for qspec in aspect_queries[:cfg.max_aspects]:
            # Accepted plan queries retain the full original question.
            reranked_aspect, diag = _rerank_windowed(qspec["query"], candidates, context, cfg)
            by_key = {(x["doc_id"], x["point_identity_key"]): x for x in reranked_aspect}
            if len(by_key) != len(candidates) or set(by_key) != set(original_by_key):
                raise RuntimeError("aspect_reranker_changed_candidate_identity_cardinality")
            if any(not isinstance(v.get("reranker_score"), (int, float)) or
                   not math.isfinite(float(v["reranker_score"])) for v in by_key.values()):
                raise RuntimeError("aspect_reranker_returned_invalid_score")
            aspect_lists.append((qspec, [(x["doc_id"], x["point_identity_key"]) for x in reranked_aspect], by_key, diag))
        selected, cursor = set(), [0] * (1 + len(aspect_lists))
        lists = [original_order] + [x[1] for x in aspect_lists]
        out_keys = []
        while len(out_keys) < len(original_order):
            moved = False
            for li, values in enumerate(lists):
                while cursor[li] < len(values) and values[cursor[li]] in selected:
                    cursor[li] += 1
                if cursor[li] < len(values):
                    key = values[cursor[li]]
                    cursor[li] += 1
                    selected.add(key)
                    out_keys.append(key)
                    moved = True
            if not moved:
                raise RuntimeError("aspect_interleave_lost_candidate")
        original_ranks = {key: i + 1 for i, key in enumerate(original_order)}
        for qspec, values, by_key, diag in aspect_lists:
            ranks = {key: i + 1 for i, key in enumerate(values)}
            aspect_metrics.append({"query_id": qspec["query"], "elapsed_sec": diag.get("elapsed_sec"),
                                   "ranks": ranks, "scores": {k: by_key[k]["reranker_score"] for k in ranks}})
        interleaved = []
        for rank, key in enumerate(out_keys, 1):
            row = dict(original_by_key[key])
            row["original_rank"] = original_ranks[key]
            row["original_score"] = row.get("reranker_score")
            row["aspect_rank_score"] = [{"query_id": a["query_id"], "rank": a["ranks"][key], "score": a["scores"][key]}
                                        for a in aspect_metrics]
            row["rank"] = rank
            interleaved.append(row)
        rerank_diag["aspect_interleave"] = {"status": "applied", "elapsed_sec": round(time.perf_counter()-aspect_started, 4),
            "additional_reranks": len(aspect_lists),
            "aspects": [{"query_id": a["query_id"], "elapsed_sec": a["elapsed_sec"], "candidate_count": len(a["ranks"])}
                        for a in aspect_metrics]}
        return interleaved, rerank_diag
    except Exception as exc:
        fallback = [dict(x, original_rank=i + 1, original_score=x.get("reranker_score")) for i, x in enumerate(reranked)]
        rerank_diag["aspect_interleave"] = {"status": "fallback", "reason": f"{type(exc).__name__}: {exc}",
                                             "elapsed_sec": round(time.perf_counter()-aspect_started, 4)}
        return fallback, rerank_diag


def find_all(query: str, doc_id: str | None = None, *, context: CorpusPointSearchContext,
             config: FindAllConfig | dict | None = None, aspects: list[str] | None = None,
             planner_adapter: Callable[[str], Any] | None = None,
             saved_query_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Retrieve broad direct point candidates, assemble whole points, then expand normative links."""
    started = time.perf_counter()
    cfg = FindAllConfig(**config) if isinstance(config, dict) else config or FindAllConfig()
    if not isinstance(cfg, FindAllConfig):
        raise TypeError("config must be FindAllConfig or dict")
    cfg.validate()
    if not isinstance(query, str) or not query.strip():
        return {"status": "needs_clarification", "reason": "empty_query", "query": query}
    if cfg.offset > 0 and saved_query_plan is None:
        return {"status": "invalid_pagination" if cfg.fingerprint else "invalid_query_plan",
                "reason": "missing_saved_query_plan",
                "query": query}
    if doc_id is not None and (not isinstance(doc_id, str) or not doc_id.strip() or doc_id not in context.corpus.docs):
        return {"status": "invalid_scope", "reason": "unknown_canonical_doc_id", "doc_id": doc_id}
    if doc_id is None and not context.scope_verified:
        return {"status": "needs_review", "reason": "global_live_scope_not_verified"}
    allowed = {doc_id} if doc_id else set(context.corpus.docs)
    unverified = allowed - set(context.corpus.qdrant_verified_doc_ids)
    if unverified:
        return {"status": "needs_review", "reason": "live_scope_not_verified", "doc_ids": sorted(unverified)}
    try:
        qplan = _query_plan(query, aspects, cfg, planner_adapter, saved_query_plan)
    except ValueError as exc:
        return {"status": "invalid_query_plan", "reason": "saved_query_plan_tampered",
                "detail": str(exc), "query": query}
    queries = qplan["queries"]
    provenance: dict[str, dict[str, list[dict]]] = {"bm25": defaultdict(list), "vector": defaultdict(list)}
    rejected: list[dict] = []
    failures: list[dict] = []
    docs: dict[str, dict[str, Any]] = {}
    seeds: set[str] = set()
    t0 = time.perf_counter()
    try:
        for spec in queries:
            for hit in context.bm25(doc_id).search(spec["query"], cfg.discovery_top_k):
                c = hit["chunk"]
                if c["payload"].get("doc_id") in allowed:
                    cid = c["chunk_id"]
                    provenance["bm25"][cid].append({"query": spec["query"], "rank": hit["rank"], "score": hit["score"], "stage": "global_discovery"})
                    seeds.add(cid)
            try:
                dense, bad = _validate_dense(context, _dense_search(context, spec["query"], doc_id, allowed, cfg.discovery_top_k), allowed, spec["query"])
                rejected.extend(bad)
                for hit in dense:
                    cid = hit["chunk"]["chunk_id"]
                    provenance["vector"][cid].append({"query": spec["query"], "rank": hit["rank"], "score": hit["score"], "stage": "global_discovery"})
                    seeds.add(cid)
            except Exception as exc:
                failures.append({"stage": "global_dense", "query": spec["query"], "error": f"{type(exc).__name__}: {exc}"})
        if failures:
            return {"status": "partial", "reason": "dense_retrieval_failed", "failed_stages": failures,
                    "query": query, "query_plan": qplan, "diagnostics": {"rejected_dense_hits": rejected}}
        for cid in seeds:
            c = context.chunk_by_id[cid]
            did = str(c["payload"].get("doc_id"))
            rows = [*provenance["bm25"].get(cid, []), *provenance["vector"].get(cid, [])]
            # Rank fusion uses ranks, never sums unlike score scales.
            best = min((int(x["rank"]) for x in rows), default=10**9)
            old = docs.get(did)
            candidate = {"best_rank": best, "seed_count": 1}
            if old:
                old["best_rank"] = min(old["best_rank"], best)
                old["seed_count"] += 1
            else:
                docs[did] = candidate
        # Mandatory full-scope BM25 control sweep; retain each document's best positive rank.
        bm_control: dict[str, dict] = {}
        bm_control_cap = cfg.discovery_top_k
        for spec in queries:
            scan_limit = (len(context.corpus.docs[doc_id]) if doc_id else
                          sum(len(context.corpus.docs[d]) for d in allowed))
            rows = context.bm25(doc_id).search(spec["query"], max(bm_control_cap, scan_limit))
            # BM25Index.search currently materializes all positives before slicing; scan every chunk
            # directly with an over-limit chosen from the scope size to avoid losing outside docs.
            for hit in rows:
                c = hit["chunk"]
                did = str(c["payload"].get("doc_id") or "")
                if did not in allowed:
                    continue
                row = {"best_rank": hit["rank"], "score": hit["score"],
                       "query": spec["query"], "chunk_id": c["chunk_id"]}
                if did not in bm_control or (hit["rank"], c["chunk_id"], spec["query"]) < (
                        bm_control[did]["best_rank"], bm_control[did]["chunk_id"], bm_control[did]["query"]):
                    bm_control[did] = row
        for did, row in bm_control.items():
            # R3 adds one representative global lexical hit per document. Other
            # positive BM25 chunks enter the direct pool only via regular top-k
            # discovery or the selected document's deep search.
            provenance["bm25"][row["chunk_id"]].append({**row, "rank": row["best_rank"], "stage": "global_control"})
            seeds.add(row["chunk_id"])
            if did not in docs:
                docs[did] = {"best_rank": row["best_rank"], "seed_count": 0, "bm25_control": True}
            else:
                docs[did]["best_rank"] = min(docs[did]["best_rank"], row["best_rank"])
                docs[did]["bm25_control"] = True
    except Exception as exc:
        return {"status": "partial", "reason": "bm25_or_discovery_failed", "error": f"{type(exc).__name__}: {exc}"}
    discovery_sec = time.perf_counter() - t0
    doc_ranked = sorted(docs, key=lambda did: (docs[did]["best_rank"], did))
    deep_docs = doc_ranked[:cfg.max_deep_docs] if doc_id is None else [doc_id]
    deep_started = time.perf_counter()
    # retain global seeds even when their document falls outside deep-doc cap
    chunk_sources: dict[str, list[dict]] = defaultdict(list)
    for source, mapping in provenance.items():
        for cid, rows in mapping.items():
            chunk_sources[cid].extend([{**x, "branch": source} for x in rows])
    if doc_id:
        deep_docs = [doc_id]
    try:
        for did in deep_docs:
            for spec in queries:
                for hit in context.bm25(did).search(spec["query"], cfg.deep_top_k):
                    c = hit["chunk"]
                    chunk_sources[c["chunk_id"]].append({"branch": "bm25", "query": spec["query"], "rank": hit["rank"], "score": hit["score"], "stage": "deep_document"})
                dense, bad = _validate_dense(context, _dense_search(context, spec["query"], did, {did}, cfg.deep_top_k), {did}, spec["query"])
                rejected.extend(bad)
                for hit in dense:
                    chunk_sources[hit["chunk"]["chunk_id"]].append({"branch": "vector", "query": spec["query"], "rank": hit["rank"], "score": hit["score"], "stage": "deep_document"})
    except Exception as exc:
        return {"status": "partial", "reason": "deep_retrieval_failed", "failed_stage": "deep_search",
                "error": f"{type(exc).__name__}: {exc}", "query": query, "query_plan": qplan,
                "diagnostics": {"deep_searched_doc_ids": deep_docs, "rejected_dense_hits": rejected}}
    direct_cids = set(chunk_sources)
    deep_search_sec = time.perf_counter() - deep_started
    # Canonical point identity grouping; retain unresolved identities for diagnostics.
    assembly_started = time.perf_counter()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved = []
    for cid in direct_cids:
        chunk = context.chunk_by_id[cid]
        did = str(chunk["payload"].get("doc_id") or "")
        identity, basis = _point_identity(chunk, context)
        if not identity:
            unresolved.append({"chunk_id": cid, "doc_id": did, "reason": "unresolved_identity"})
            continue
        key = (did, identity)
        entry = context.corpus.entry_for_doc(did)
        matched_best_rank = min((int(x.get("rank", 10**9)) for x in chunk_sources[cid]), default=10**9)
        row = groups.setdefault(key, {"doc_id": did, "doc_name": entry.get("doc_name", ""),
            "chunk_id": identity,
            "registry_key": entry.get("registry_key"), "point_identity_key": identity,
            "point_number": chunk["payload"].get("point_number"), "identity_basis": basis,
            "best_chunk_rank": matched_best_rank,
            "matched_chunk_ids": [], "provenance": {}})
        row["best_chunk_rank"] = min(row["best_chunk_rank"], matched_best_rank)
        row["matched_chunk_ids"].append(cid)
        for prov in chunk_sources[cid]:
            row["provenance"].setdefault(prov["branch"], []).append({"chunk_id": cid, **prov})
    direct = []
    for (did, identity), item in groups.items():
        assembly = assemble_point_by_identity(context, did, identity, item["identity_basis"])
        item.update({"text": assembly["text"], "complete": assembly["complete"],
                     "source_check": assembly["source_check"], "all_point_chunk_ids": assembly["all_point_chunk_ids"],
                     "chunk_ids": assembly["all_point_chunk_ids"],
                     "part_count": assembly.get("part_count"), "total_parts": assembly.get("total_parts"),
                     "roles": ["direct"], "relevance_status": "candidate"})
        direct.append(item)
    direct.sort(key=lambda x: (x["best_chunk_rank"], x["doc_id"], x["point_identity_key"]))
    excluded = direct[cfg.max_direct_points:]
    direct = direct[:cfg.max_direct_points]
    assembly_sec = time.perf_counter() - assembly_started
    rerank_started = time.perf_counter()
    try:
        reranked, rerank_diag = rerank_direct_candidates(query, direct, context, cfg, qplan)
    except Exception as exc:
        return {"status": "partial", "reason": "reranker_failed", "failed_stage": "point_rerank",
                "error": f"{type(exc).__name__}: {exc}", "query": query, "query_plan": qplan,
                "coverage": {"semantic_complete": False, "direct_point_candidate_count": len(direct),
                             "deep_searched_doc_ids": deep_docs},
                "diagnostics": {"rejected_dense_hits": rejected, "unresolved_identity_hits": unresolved}}
    rerank_sec = time.perf_counter() - rerank_started
    before_threshold = len(reranked)
    if cfg.min_reranker_score is not None:
        reranked = [r for r in reranked if r["reranker_score"] >= cfg.min_reranker_score]
    for i, row in enumerate(reranked, 1):
        row["rank"] = i
    # All direct candidates are expanded before pagination.
    reference_started = time.perf_counter()
    ref_points, references, ref_diag = _expand_references(context, reranked, cfg)
    reference_sec = time.perf_counter() - reference_started
    window_truncated = any(bool(row.get("rerank_windows", {}).get("unprocessed_token_range")) for row in reranked)
    reference_truncated = any(row.get("status") == "reference_target_limit" for row in references)
    deep_doc_truncated = doc_id is None and len(doc_ranked) > cfg.max_deep_docs
    actual_truncation = bool(excluded or window_truncated or reference_truncated or deep_doc_truncated)
    fingerprint_config = {k: v for k, v in asdict(cfg).items()
                          if k not in {"tokenizer", "model_path", "offset", "fingerprint"}}
    fingerprint_config["model_path"] = str(cfg.model_path) if cfg.model_path is not None else "default_local_reranker"
    fingerprint_config["tokenizer"] = str(getattr(cfg.tokenizer, "name_or_path", "default_for_model"))
    fingerprint_base = {"query": query, "doc_id": doc_id, "query_plan": qplan,
                        "config": fingerprint_config,
                        "corpus": _canonical_fingerprint(context.corpus)}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_base, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    if cfg.fingerprint and cfg.fingerprint != fingerprint:
        return {"status": "invalid_pagination", "reason": "fingerprint_mismatch", "expected_fingerprint": fingerprint,
                "query": query}
    page = reranked[cfg.offset:cfg.offset+cfg.page_size]
    has_more = cfg.offset + cfg.page_size < len(reranked)
    all_doc_ids = set(context.corpus.docs)
    coverage_limits = []
    if qplan["planner"]["status"] == "fallback":
        coverage_limits.append("aspect_planner_fallback")
    if len(doc_ranked) > cfg.max_deep_docs and not doc_id:
        coverage_limits.append("max_deep_docs")
    if excluded:
        coverage_limits.append("max_direct_points")
    if window_truncated:
        coverage_limits.append("max_windows_per_point")
    if reference_truncated:
        coverage_limits.append("reference_target_limit")
    if cfg.min_reranker_score is not None and len(reranked) < before_threshold:
        coverage_limits.append("min_reranker_score")
    status = "needs_review" if rejected or qplan["planner"]["status"] == "fallback" else "ok"
    return {"status": status, "query": query, "scope": {"type": "global" if doc_id is None else "document",
            "doc_id": doc_id, "follow_external": cfg.follow_external}, "query_plan": qplan,
        "effective_config": {**{k: v for k, v in asdict(cfg).items() if k not in {"tokenizer", "model_path"}},
                             "model_path": str(cfg.model_path) if cfg.model_path is not None else "default_local_reranker",
                             "tokenizer": str(getattr(cfg.tokenizer, "name_or_path", "default_for_model"))},
        "results": page, "reference_points": ref_points, "references": references,
        "pagination": {"offset": cfg.offset, "page_size": cfg.page_size, "total_direct_candidates": len(reranked),
            "has_more": has_more, "next_offset": cfg.offset+cfg.page_size if has_more else None, "fingerprint": fingerprint},
        "truncated": actual_truncation,
        "coverage": {"semantic_complete": False, "relevance_status": "candidate", "truncated": actual_truncation,
            "canonical_doc_count": len(context.corpus.docs), "discovered_doc_ids": doc_ranked,
            "deep_searched_doc_ids": deep_docs,
            "unsearched_doc_ids": sorted((all_doc_ids if doc_id is None else allowed)-set(deep_docs))
                                    if doc_id is None else [],
            "inspected_direct_point_count": len(groups), "returned_page_point_count": len(page),
            "excluded_direct_point_ids": [x["point_identity_key"] for x in excluded],
            "reference_point_count": len(ref_points), "reference_edge_count": len(references),
            "unresolved_reference_count": ref_diag["unresolved_edge_count"], "limits": coverage_limits,
            "failures": ([qplan["planner"]["failure"]] if qplan["planner"].get("failure") else [])},
        "diagnostics": {"planner_sec": qplan["planner"].get("elapsed_sec", 0.0),
            "discovery_sec": round(discovery_sec, 4), "deep_search_sec": round(deep_search_sec, 4),
            "assembly_sec": round(assembly_sec, 4), "reranker_sec": round(rerank_sec, 4),
            "reference_expansion_sec": round(reference_sec, 4),
            "total_sec": round(time.perf_counter()-started, 4), "seed_chunk_count": len(seeds),
            "chunk_candidate_count": len(direct_cids), "direct_point_count_before_cap": len(groups),
            "direct_point_count_after_reranker": len(reranked), "threshold_removed_count": before_threshold-len(reranked),
            "deep_doc_ranking": [{"doc_id": did, **docs[did]} for did in doc_ranked],
            "rejected_dense_hits": rejected, "unresolved_identity_hits": unresolved,
            "excluded_direct_candidates": [{"doc_id": x["doc_id"], "point_identity_key": x["point_identity_key"]} for x in excluded],
            "reranker": rerank_diag, **ref_diag}}
