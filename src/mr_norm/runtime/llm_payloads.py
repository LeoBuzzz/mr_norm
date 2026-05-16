from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.planner import ALLOWED_RUNTIME_TOOLS

STRICT_JSON_SUFFIX = (
    "Return only one JSON object. No markdown fences, no reasoning text, "
    "no prose before or after JSON, no extra keys outside the contract."
)


def build_strict_prompt(base_prompt: str) -> str:
    base = base_prompt.strip()
    if STRICT_JSON_SUFFIX in base:
        return base
    return f"{base}\n\n{STRICT_JSON_SUFFIX}"


def _coerce_string_list(value: Any, *, field_name: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
        return parts, warnings
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list or string")
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items, warnings


def normalize_planner_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    data = dict(payload)

    raw_tools = data.get("selected_tools", data.get("tools"))
    selected_tools, tool_warnings = _coerce_string_list(raw_tools, field_name="selected_tools")
    warnings.extend(tool_warnings)

    valid_tools: list[str] = []
    for tool_name in selected_tools:
        if tool_name not in ALLOWED_RUNTIME_TOOLS:
            warnings.append(f"planner ignored unknown tool: {tool_name!r}")
            continue
        if tool_name not in valid_tools:
            valid_tools.append(tool_name)

    raw_reasons = data.get("routing_reasons", data.get("reasons", []))
    routing_reasons, reason_warnings = _coerce_string_list(raw_reasons, field_name="routing_reasons")
    warnings.extend(reason_warnings)

    if not routing_reasons and valid_tools:
        routing_reasons = [f"{tool_name}: selected by planner" for tool_name in valid_tools]

    return {
        "selected_tools": valid_tools,
        "routing_reasons": routing_reasons,
    }, warnings


def normalize_reranker_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    data = dict(payload)

    raw_ids = data.get("ranked_chunk_ids", data.get("chunk_ids", data.get("ranking")))
    if raw_ids is None and isinstance(data.get("items"), list):
        raw_ids = [
            item.get("chunk_id") if isinstance(item, dict) else item
            for item in data["items"]
        ]

    ranked_chunk_ids, id_warnings = _coerce_string_list(raw_ids, field_name="ranked_chunk_ids")
    warnings.extend(id_warnings)

    return {"ranked_chunk_ids": ranked_chunk_ids}, warnings


def normalize_final_answer_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    data = dict(payload)

    answer = data.get("answer")
    if not answer and isinstance(data.get("response"), dict):
        answer = data["response"].get("answer")
    if not answer and isinstance(data.get("result"), dict):
        answer = data["result"].get("answer")
    answer_text = str(answer or "").strip()
    if not answer_text:
        raise ValueError("answer must be a non-empty string")

    raw_citations = data.get("citations", data.get("sources", data.get("references", [])))
    if isinstance(raw_citations, str):
        raw_citations = [part.strip() for part in re.split(r"[,;\n]+", raw_citations) if part.strip()]

    citations: list[dict[str, str]] = []
    if isinstance(raw_citations, list):
        for index, entry in enumerate(raw_citations):
            if isinstance(entry, str):
                chunk_id = entry.strip()
                if chunk_id:
                    citations.append({"chunk_id": chunk_id})
                else:
                    warnings.append(f"citation[{index}]: empty chunk_id string")
            elif isinstance(entry, dict):
                chunk_id = str(entry.get("chunk_id") or entry.get("id") or "").strip()
                if not chunk_id:
                    warnings.append(f"citation[{index}]: missing chunk_id")
                    continue
                citations.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_name": str(entry.get("doc_name") or "").strip(),
                        "point_number": str(entry.get("point_number") or "").strip(),
                    }
                )
            else:
                warnings.append(f"citation[{index}]: unsupported citation type")
    elif raw_citations is not None:
        raise ValueError("citations must be a list or string")

    return {"answer": answer_text, "citations": citations}, warnings


def extract_json_text(content: str) -> str:
    text = content.strip()
    if not text:
        raise ValueError("LLM response content is empty")

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        object_match = re.search(r"\{[\s\S]*\}", text)
        if object_match:
            return object_match.group(0)
        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            return array_match.group(0)
    return text


def parse_llm_payload(content: str) -> dict[str, Any]:
    payload = json.loads(extract_json_text(content))
    if isinstance(payload, list):
        return {"ranked_chunk_ids": payload}
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload
