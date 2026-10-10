"""One query plan for a wide search.

The original question stays intact. Aspects come from a saved plan, from the
caller, or from one injected planner. Concepts are not split into a second
plan. The default planner mode is off: this module does not open a network
connection. A quality comparison between modes is `compare_aspect_plans`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

PLANNER_PROMPT_VERSION = "question-parser-aspect-planner-v1"
_CONSTRAINT_RE = re.compile(
    r"\d+(?:[.,]\d+)?(?:\s*(?:лет|года|год|месяц(?:а|ев)?|дн(?:я|ей)?|час(?:а|ов)?|минут(?:а|ы)?|%))?"
    r"|\b(?:не|ни|нет|без|нельзя|кроме|только|исключительно)\b[^,;.!?]*",
    re.IGNORECASE,
)
_UNSAFE_ASPECT_RE = re.compile(
    r"\b(?:игнорир\w*|не\s+учитывай\w*|убери|удали|исключи|ignore|disregard)\b"
    r".{0,100}\b(?:ограничен\w*|услови\w*|исходн\w*|вопрос\w*|запрос\w*|требован\w*|"
    r"област\w*|scope|constraints?)\b",
    re.IGNORECASE,
)


@dataclass
class FindAllConfig:
    max_aspects: int = 4
    planner_mode: str = "off"

    def validate(self) -> None:
        if isinstance(self.max_aspects, bool) or not isinstance(self.max_aspects, int) or not 0 <= self.max_aspects <= 20:
            raise ValueError("max_aspects must be an integer from 0 to 20")
        if self.planner_mode not in {"off", "llm"}:
            raise ValueError("planner_mode must be 'off' or 'llm'")


def required_constraints(query: str) -> list[str]:
    found = []
    for match in _CONSTRAINT_RE.finditer(query):
        text = " ".join(match.group(0).split())
        if text and text not in found:
            found.append(text)
    return found


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


def _queries_for(query: str, aspects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [{"query": query, "origin": "original", "aspect": None}]
    for row in aspects:
        rows.append(
            {
                "query": f"{query} [дополнительный аспект: {row['text']}]",
                "origin": row["source"],
                "aspect": row["text"],
            }
        )
    return rows


def _accept_saved(query: str, saved: dict[str, Any], cfg: FindAllConfig) -> dict[str, Any]:
    if not isinstance(saved, dict) or saved.get("original_query") != query:
        raise ValueError("saved_query_plan_query_mismatch")
    aspects = saved.get("aspects")
    constraints = saved.get("constraints")
    skipped = saved.get("skipped_aspects")
    planner = saved.get("planner")
    if not isinstance(aspects, list) or not isinstance(constraints, list) or not isinstance(skipped, list) or not isinstance(planner, dict):
        raise ValueError("saved_query_plan_invalid_structure")
    if len(aspects) > cfg.max_aspects:
        raise ValueError("saved_query_plan_exceeds_max_aspects")
    if planner.get("mode") not in {"off", "llm"} or planner.get("status") not in {"ok", "fallback", "disabled", "skipped"}:
        raise ValueError("saved_query_plan_invalid_planner_status")
    required = required_constraints(query)
    if any(not isinstance(item, str) or not item or item not in query for item in constraints):
        raise ValueError("saved_query_plan_constraint_not_verbatim")
    if any(item not in constraints for item in required):
        raise ValueError("saved_query_plan_constraint_omitted")
    seen = {" ".join(query.split()).casefold()}
    for row in aspects:
        if not isinstance(row, dict) or row.get("source") not in {"caller_aspect", "llm_aspect"} or row.get("validation") != "accepted":
            raise ValueError("saved_query_plan_invalid_aspect_provenance")
        text, error = _validate_aspect_text(row.get("text"))
        if error or text != row.get("text"):
            raise ValueError("saved_query_plan_invalid_aspect")
        key = text.casefold()
        if key in seen:
            raise ValueError("saved_query_plan_duplicate_aspect")
        seen.add(key)
    expected = _queries_for(query, aspects)
    if saved.get("queries") != expected or saved.get("executed_queries") != [item["query"] for item in expected]:
        raise ValueError("saved_query_plan_queries_do_not_match_validated_aspects")
    return {
        "original_query": query,
        "planner": dict(planner),
        "constraints": list(constraints),
        "aspects": list(aspects),
        "skipped_aspects": list(skipped),
        "queries": expected,
        "executed_queries": [item["query"] for item in expected],
    }


def build_query_plan(
    query: str,
    *,
    config: FindAllConfig | None = None,
    caller_aspects: list[str] | None = None,
    applied_constraints: list[str] | None = None,
    planner_adapter: Callable[[str], Any] | None = None,
    saved_query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the single plan used by find_all.

    `applied_constraints` are the conditions the skill must filter with, even
    when a correction made them absent from the original question text.
    """
    cfg = config or FindAllConfig()
    cfg.validate()
    if saved_query_plan is not None:
        plan = _accept_saved(query, saved_query_plan, cfg)
        plan["applied_constraints"] = list(applied_constraints if applied_constraints is not None else plan["constraints"])
        return plan
    cleaned: list[str] = []
    skipped: list[dict[str, str]] = []
    seen = {" ".join(query.split()).casefold()}
    for aspect in caller_aspects or []:
        normalized, error = _validate_aspect_text(aspect)
        if error or normalized is None:
            skipped.append({"aspect": str(aspect), "reason": error or "empty_or_non_string"})
            continue
        key = normalized.casefold()
        if key in seen:
            skipped.append({"aspect": normalized, "reason": "duplicate"})
        elif len(cleaned) >= cfg.max_aspects:
            skipped.append({"aspect": normalized, "reason": "max_aspects"})
        else:
            seen.add(key)
            cleaned.append(normalized)
    planner = {
        "mode": cfg.planner_mode,
        "backend": "disabled" if cfg.planner_mode == "off" else "injected",
        "prompt_version": PLANNER_PROMPT_VERSION,
        "status": "disabled" if cfg.planner_mode == "off" else "skipped",
        "reason": None,
        "failure": None,
        "elapsed_sec": 0.0,
    }
    aspects = [
        {"text": text, "rationale": "Передан вызывающим кодом.", "source": "caller_aspect", "validation": "accepted"}
        for text in cleaned
    ]
    constraints = required_constraints(query)
    budget = max(0, cfg.max_aspects - len(cleaned))
    if cfg.planner_mode == "llm" and budget and planner_adapter is not None:
        started = time.perf_counter()
        planner["status"] = "ok"
        try:
            raw = planner_adapter(query)
            if not isinstance(raw, dict) or not isinstance(raw.get("aspects"), list) or not isinstance(raw.get("constraints"), list):
                raise ValueError("malformed_structured_reply")
            quoted = raw["constraints"]
            if any(not isinstance(item, str) or not item or item not in query for item in quoted):
                raise ValueError("constraint_quote_not_verbatim")
            if any(item not in quoted for item in constraints):
                raise ValueError("required_constraint_omitted")
            if len(raw["aspects"]) > 4:
                raise ValueError("too_many_planned_aspects")
            for row in raw["aspects"]:
                if not isinstance(row, dict) or not {"text", "rationale"} <= set(row):
                    raise ValueError("aspect_missing_text_or_rationale")
                text, error = _validate_aspect_text(row.get("text"))
                rationale = row.get("rationale")
                if error or text is None or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 240:
                    raise ValueError("invalid_aspect_shape")
                key = text.casefold()
                if key in seen:
                    skipped.append({"aspect": text, "reason": "duplicate"})
                elif len(aspects) >= cfg.max_aspects:
                    skipped.append({"aspect": text, "reason": "max_aspects"})
                else:
                    seen.add(key)
                    aspects.append(
                        {
                            "text": text,
                            "rationale": rationale.strip(),
                            "source": "llm_aspect",
                            "validation": "accepted",
                        }
                    )
            constraints = list(quoted)
        except Exception as exc:
            aspects = [row for row in aspects if row["source"] == "caller_aspect"]
            constraints = required_constraints(query)
            planner.update({"status": "fallback", "reason": "planner_failed", "failure": f"{type(exc).__name__}: {exc}"})
        finally:
            planner["elapsed_sec"] = round(time.perf_counter() - started, 4)
    elif cfg.planner_mode == "llm":
        planner.update({"status": "skipped", "reason": "no_adapter" if planner_adapter is None else "aspect_budget_filled"})
    queries = _queries_for(query, aspects)
    applied = list(applied_constraints) if applied_constraints is not None else list(constraints)
    return {
        "original_query": query,
        "planner": planner,
        "constraints": constraints,
        "applied_constraints": applied,
        "unquoted_constraints": [item for item in applied if item not in query],
        "aspects": aspects,
        "skipped_aspects": skipped,
        "queries": queries,
        "executed_queries": [item["query"] for item in queries],
    }


def compare_aspect_plans(
    query: str,
    *,
    applied_constraints: list[str],
    planner_adapter: Callable[[str], Any],
    caller_aspects: list[str] | None = None,
) -> dict[str, Any]:
    """Run the same question with the planner off and with one adapter.

    Neither run replaces the other. Losses are the applied conditions that the
    quoted planner list does not keep.
    """
    off = build_query_plan(
        query,
        config=FindAllConfig(planner_mode="off"),
        caller_aspects=caller_aspects,
        applied_constraints=applied_constraints,
    )
    live = build_query_plan(
        query,
        config=FindAllConfig(planner_mode="llm"),
        caller_aspects=caller_aspects,
        applied_constraints=applied_constraints,
        planner_adapter=planner_adapter,
    )
    quoted = [str(item) for item in live["constraints"]]

    def covered(item: str) -> bool:
        return any(item == row or item in row or row in item for row in quoted)

    return {
        "original_query": query,
        "off_aspects": [row["text"] for row in off["aspects"]],
        "llm_aspects": [row["text"] for row in live["aspects"]],
        "llm_status": live["planner"]["status"],
        "planner_failure": live["planner"].get("failure"),
        "dropped_constraints": [item for item in applied_constraints if item in query and not covered(item)],
        "unquoted_constraints": [item for item in applied_constraints if item not in query],
        "off_queries": off["executed_queries"],
        "llm_queries": live["executed_queries"],
    }
