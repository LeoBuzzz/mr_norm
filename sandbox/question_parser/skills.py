"""Sandbox skills over local point text and the definition catalog.

A wide search keeps one query plan and does not start a second decomposition
of the topic. Definitions are read by `definition_search`.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from question_parser.corpus import LocalCorpus
from question_parser.find_all_plan import FindAllConfig, build_query_plan

_WORD_RE = re.compile(r"[A-Za-zА-ЯЁа-яё0-9-]{4,}")
_ASPECT_MARKER_RE = re.compile(r"\[дополнительный аспект:\s*(.+?)\]")


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _needles(values: list[str]) -> list[str]:
    found = []
    for value in values:
        folded = _fold(value).strip()
        if len(folded) >= 3 and folded not in found:
            found.append(folded)
    return found


def _passes(text: str, constraints: list[dict[str, Any]]) -> bool:
    folded = _fold(text)
    for item in constraints:
        quote = _fold(str(item.get("text") or ""))
        if not quote:
            continue
        present = quote in folded
        if item.get("polarity") == "exclude" and present:
            return False
        if item.get("polarity") == "include" and not present:
            return False
    return True


def _hit(point, *, branch: str) -> dict[str, Any]:
    return {
        "doc_id": point.doc_id,
        "point_ref": point.point_ref,
        "text": point.text,
        "full_point_text": True,
        "branch": branch,
    }


def search_points(
    corpus: LocalCorpus,
    *,
    needles: list[str],
    doc_id: str | None,
    constraints: list[dict[str, Any]],
    branch: str,
    match: str = "any",
) -> list[dict[str, Any]]:
    folded_needles = _needles(needles)
    hits = []
    for point in corpus.matching(doc_id):
        folded = _fold(point.text)
        if folded_needles:
            present = [needle in folded for needle in folded_needles]
            if match == "all" and not all(present):
                continue
            if match != "all" and not any(present):
                continue
        if not _passes(point.text, constraints):
            continue
        hits.append(_hit(point, branch=branch))
    return hits


def exact_point(corpus: LocalCorpus, *, doc_id: str, point_ref: str) -> dict[str, Any]:
    text = corpus.point(doc_id, point_ref)
    if text is None:
        return {
            "status": "not_found",
            "doc_id": doc_id,
            "point_ref": point_ref,
            "text": None,
            "full_point_text": False,
            "hits": [],
        }
    return {
        "status": "ok",
        "doc_id": doc_id,
        "point_ref": point_ref,
        "text": text,
        "full_point_text": True,
        "hits": [_hit_from_text(doc_id, point_ref, text, "exact_point")],
    }


def _hit_from_text(doc_id: str, point_ref: str, text: str, branch: str) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "point_ref": point_ref,
        "text": text,
        "full_point_text": True,
        "branch": branch,
    }


def corpus_point_search(
    corpus: LocalCorpus,
    *,
    needles: list[str],
    doc_id: str | None,
    constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    hits = search_points(corpus, needles=needles, doc_id=doc_id, constraints=constraints, branch="corpus_point_search")
    return {"status": "ok" if hits else "not_found", "doc_id": doc_id, "point_ref": None, "text": None, "full_point_text": False, "hits": hits}


def _aspect_text(query: str) -> str | None:
    match = _ASPECT_MARKER_RE.search(query)
    if not match:
        return None
    return match.group(1).strip()


def _branch_needles(query: str, base_needles: list[str]) -> list[str]:
    aspect = _aspect_text(query)
    if aspect is None:
        return base_needles
    words = _WORD_RE.findall(aspect)
    return words or [aspect]


def find_all(
    corpus: LocalCorpus,
    *,
    original_query: str,
    needles: list[str],
    doc_id: str | None,
    constraints: list[dict[str, Any]],
    config: FindAllConfig | None = None,
    caller_aspects: list[str] | None = None,
    planner_adapter: Callable[..., Any] | None = None,
    saved_query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied = [str(item.get("text") or "") for item in constraints if item.get("text")]
    try:
        query_plan = build_query_plan(
            original_query,
            config=config,
            caller_aspects=caller_aspects,
            applied_constraints=applied,
            planner_adapter=planner_adapter,
            saved_query_plan=saved_query_plan,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "code": str(exc),
            "doc_id": doc_id,
            "point_ref": None,
            "text": None,
            "full_point_text": False,
            "hits": [],
            "query_plan": None,
        }
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for branch_query in query_plan["executed_queries"]:
        aspect = _aspect_text(branch_query)
        if aspect is None:
            branch_needles, match = needles, "any"
        else:
            branch_needles, match = _branch_needles(branch_query, needles), "all"
        for hit in search_points(
            corpus,
            needles=branch_needles,
            doc_id=doc_id,
            constraints=constraints,
            branch=branch_query,
            match=match,
        ):
            key = (hit["doc_id"], hit["point_ref"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return {
        "status": "ok" if merged else "not_found",
        "doc_id": doc_id,
        "point_ref": None,
        "text": None,
        "full_point_text": False,
        "hits": merged,
        "query_plan": query_plan,
    }
