"""Route one question with a model. Code only checks ids and abbreviation pairs.

The model chooses the operation, the database query, the document id and the
expansion. The document table is the full corpus. Abbreviation rows are only
the pairs written in the question. Neither table decides the operation.
A comparison, conclusion, or summary stays out of the skill list: `final`
is the instruction for the model that writes the answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from question_parser.prompts import SYSTEM_PROMPT, build_user_message

TABLES = Path(__file__).resolve().parent / "tables"
TOKEN_RE = re.compile(r"[A-Za-zА-ЯЁа-яё0-9-]{2,}")
OPERATIONS = {"text", "search", "all", "define", "compare", "conclude", "summarize", "unknown"}
SKILLS = {"text": "exact_point", "search": "corpus_point_search", "all": "find_all", "define": "definition_search"}
ALLOWED_SKILLS = set(SKILLS.values()) | {"vector_search"}
ANSWER_OPERATIONS = {"compare", "conclude", "summarize"}


def load_abbreviations(path: Path | None = None) -> list[dict[str, Any]]:
    return json.loads((path or TABLES / "abbreviations.json").read_text(encoding="utf-8"))


def load_documents(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or TABLES / "documents.json").read_text(encoding="utf-8"))


def abbreviation_candidates(query: str, table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows whose abbreviation is written in the question.

    Two- and three-letter rows count only when the question writes them in
    capitals. A lowercase «по» is a preposition, not the abbreviation ПО.
    """
    tokens = set()
    for token in TOKEN_RE.findall(query):
        if len(token) <= 3 and token != token.upper():
            continue
        tokens.add(token.casefold().replace("ё", "е"))
    found = []
    for row in table:
        key = str(row["abbreviation"]).casefold().replace("ё", "е")
        if key in tokens:
            found.append({"abbreviation": row["abbreviation"], "values": list(row["values"])})
    return found


def judge(expect: dict[str, Any], checked: dict[str, Any]) -> list[str]:
    """Compare a checked model plan with the expected shape of one test question."""
    reasons = []
    if checked.get("operation") != expect.get("operation"):
        reasons.append("operation")
    tools = checked.get("tools") or []
    if expect.get("point_ref") and not any(tool.get("point_ref") == expect["point_ref"] for tool in tools):
        reasons.append("point")
    doc_ids = [tool.get("doc_id") for tool in tools if tool.get("doc_id")]
    field = (checked.get("clarification") or {}).get("field")
    if expect.get("doc_id") and expect["doc_id"] not in doc_ids:
        if not (expect.get("allow_clarify") and field == expect["allow_clarify"]):
            reasons.append("doc")
    if expect.get("clarify") and field != expect["clarify"]:
        reasons.append("clarify")
    blob = " ".join(tool.get("query") or "" for tool in tools).casefold().replace("ё", "е")
    needle = expect.get("query_contains")
    if needle and needle.casefold().replace("ё", "е") not in blob:
        reasons.append("query")
    wanted = expect.get("expansion")
    if wanted:
        values = [item["value"].casefold() for tool in tools for item in tool.get("expansions") or []]
        if wanted.casefold() not in values and wanted.casefold() not in blob:
            reasons.append("expansion")
    if checked.get("problems"):
        reasons.append("problems")
    return reasons


def route_question(
    query: str,
    complete: Callable[[str, str], str],
    *,
    abbreviations: list[dict[str, Any]] | None = None,
    documents: dict[str, Any] | None = None,
) -> dict[str, Any]:
    abbreviations = abbreviations if abbreviations is not None else load_abbreviations()
    documents = documents if documents is not None else load_documents()
    candidates = abbreviation_candidates(query, abbreviations)
    user = build_user_message(query, documents["documents"], candidates)
    raw = complete(SYSTEM_PROMPT, user)
    parsed = _parse_json(raw)
    if parsed is None:
        return {"status": "technical_error", "code": "model_json", "message": "model did not return JSON", "raw": raw}
    checked = _check(parsed, documents, candidates)
    checked["raw_model"] = parsed
    checked["abbreviation_candidates"] = candidates
    return checked


def _skill_for(operation: str, raw: Any, problems: list[str]) -> str | None:
    """Keep a skill the model named. A missing skill falls back to the operation."""
    if isinstance(raw, str) and raw in ALLOWED_SKILLS:
        return raw
    if raw in (None, "", "null"):
        return SKILLS.get(operation)
    problems.append("unknown_skill")
    return SKILLS.get(operation)


def _is_answer_step(operation: str, tool: dict[str, Any]) -> bool:
    """The closing step is written by the answering model, not by a skill."""
    role = tool.get("role") or tool.get("kind")
    if role == "final":
        return True
    raw = tool.get("skill")
    if raw == "final":
        return True
    return raw in (None, "", "null") and operation in ANSWER_OPERATIONS


def _remap_after(raw_after: Any, index_map: dict[int, int], origin: int, problems: list[str]) -> list[int]:
    after = []
    for earlier in raw_after or []:
        mapped = index_map.get(earlier) if isinstance(earlier, int) and not isinstance(earlier, bool) else None
        if mapped is not None and earlier < origin:
            after.append(mapped)
        else:
            problems.append("bad_after")
    return after


def _instruction_text(source: Any) -> str | None:
    if isinstance(source, str) and source.strip() and source.strip() != "null":
        return source.strip()
    if not isinstance(source, dict):
        return None
    for key in ("instruction", "query"):
        text = source.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _compose_final(
    raw: Any,
    lifted: dict[str, Any] | None,
    lifted_index: int | None,
    index_map: dict[int, int],
    problems: list[str],
) -> dict[str, Any] | None:
    """Read the answer instruction. It is never turned into a skill call."""
    instruction = _instruction_text(raw)
    if instruction is None and lifted is not None:
        instruction = _instruction_text(lifted)
    if isinstance(raw, dict) and "after" in raw:
        after_raw = raw.get("after")
        origin = 10**9
    elif lifted is not None:
        after_raw = lifted.get("after") or []
        origin = lifted_index or 10**9
    else:
        after_raw = []
        origin = 10**9
    if instruction is None:
        if lifted is not None or isinstance(raw, dict) or (isinstance(raw, str) and raw.strip() == ""):
            problems.append("empty_final")
        return None
    return {"instruction": instruction, "after": _remap_after(after_raw or [], index_map, origin, problems)}


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _check(payload: dict[str, Any], documents: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    problems = []
    operation = payload.get("operation")
    if operation not in OPERATIONS:
        problems.append("bad_operation")
        operation = "unknown"
    known_docs = {item["id"] for item in documents["documents"]}
    known_values = {row["abbreviation"].casefold(): [value.casefold() for value in row["values"]] for row in candidates}
    records = []
    for index, tool in enumerate(payload.get("tools") or [], 1):
        if not isinstance(tool, dict):
            problems.append(f"tool_{index}_not_object")
            continue
        doc_id = tool.get("doc_id")
        if doc_id is not None and doc_id not in known_docs:
            problems.append("unknown_doc_id")
            doc_id = None
        expansions = []
        for expansion in tool.get("expansions") or []:
            if not isinstance(expansion, dict):
                problems.append("bad_expansion")
                continue
            abbreviation = str(expansion.get("abbreviation") or "")
            value = str(expansion.get("value") or "")
            allowed = known_values.get(abbreviation.casefold())
            if allowed is None or value.casefold() not in allowed:
                problems.append("expansion_not_in_table")
                continue
            expansions.append({"abbreviation": abbreviation, "value": value})
        records.append({"index": index, "tool": tool, "doc_id": doc_id, "expansions": expansions})
    index_map: dict[int, int] = {}
    tools = []
    answer_records = []
    next_step = 1
    for record in records:
        tool = record["tool"]
        if _is_answer_step(operation, tool):
            answer_records.append(record)
            continue
        skill = _skill_for(operation, tool.get("skill"), problems)
        raw_query = tool.get("query")
        if skill == "exact_point":
            query = None
        elif not isinstance(raw_query, str) or not raw_query.strip():
            problems.append("empty_query")
            query = ""
        else:
            query = raw_query.strip()
        index_map[record["index"]] = next_step
        tools.append(
            {
                "step": next_step,
                "skill": skill,
                "query": query,
                "doc_id": record["doc_id"],
                "point_ref": tool.get("point_ref") if isinstance(tool.get("point_ref"), str) else None,
                "expansions": record["expansions"],
                "after": _remap_after(tool.get("after") or [], index_map, record["index"], problems),
            }
        )
        next_step += 1
    if len(answer_records) > 1:
        problems.append("multiple_final")
    lifted = answer_records[-1] if answer_records else None
    final = _compose_final(
        payload.get("final"),
        lifted["tool"] if lifted else None,
        lifted["index"] if lifted else None,
        index_map,
        problems,
    )
    clarification = payload.get("clarification")
    if clarification is not None:
        if not isinstance(clarification, dict) or clarification.get("field") not in {"document", "term"}:
            problems.append("bad_clarification")
            clarification = None
        else:
            options = []
            for option in clarification.get("option_ids") or []:
                if clarification["field"] == "document" and option not in known_docs:
                    problems.append("unknown_clarification_doc")
                    continue
                if clarification["field"] == "term":
                    allowed_terms = {value.casefold() for values in known_values.values() for value in values}
                    if str(option).casefold() not in allowed_terms and option not in known_docs:
                        problems.append("unknown_clarification_term")
                        continue
                options.append(option)
            clarification = {"field": clarification["field"], "question": clarification.get("question") or "", "option_ids": options}
    if final is None and operation in ANSWER_OPERATIONS and tools and clarification is None:
        problems.append("missing_final")
    if operation == "search" and clarification is None:
        skills = {tool.get("skill") for tool in tools}
        if "vector_search" not in skills:
            problems.append("missing_vector_search")
        if "corpus_point_search" not in skills:
            problems.append("missing_text_search")
    executable = any(tool.get("skill") for tool in tools)
    status = "technical_error" if "bad_operation" in problems or "model_json" in problems else "needs_clarification" if clarification else "ready"
    if operation in ANSWER_OPERATIONS and not clarification and not executable:
        status = "deferred"
    return {
        "status": status,
        "complexity": payload.get("complexity") if payload.get("complexity") in {"simple", "complex"} else None,
        "operation": operation,
        "tools": tools,
        "final": final,
        "clarification": clarification,
        "problems": problems,
    }
