"""Execute a checked router2 plan against the main Qdrant corpus."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.retrieval.contracts import RetrievedItem, ToolRequest
from mr_norm.retrieval.point_assemble import assemble_point_parts
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import run_point_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.final_answer import build_final_answer
from mr_norm.runtime.llm_clients import LLMRequest, build_chat_client, parse_json_object

from question_parser.definitions import definition_search
from question_parser.model_route import abbreviation_candidates, load_abbreviations, load_documents, route_question

DEFAULT_ROUTER_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_ANSWER_MODEL = "anthropic/claude-sonnet-4.6"
DEFAULT_ROUTER_FALLBACK_MODELS = (
    "google/gemini-3-flash-preview",
    "anthropic/claude-haiku-4.5",
)
DEFAULT_ANSWER_FALLBACK_MODELS = (
    "openai/gpt-5.5",
    "google/gemini-3-flash-preview",
)
MAX_STEPS = 12
MAX_EVIDENCE = 16
FINAL_JSON_ATTEMPTS = 3


@dataclass
class Router2Result:
    status: str
    question: str
    plan: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification: dict[str, Any] | None = None
    router_model_used: str = ""
    answer_model_used: str = ""
    model_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def corpus_doc_id(registry_key: str) -> str:
    """Match chunker.stable_id('doc', registry_key) without importing the chunker."""
    digest = hashlib.sha1(registry_key.encode("utf-8")).hexdigest()[:16]
    return f"doc_{digest}"


def document_filters(doc_id: str | None, documents: dict[str, Any]) -> dict[str, str]:
    if not doc_id:
        return {}
    document = next((item for item in documents["documents"] if item["id"] == doc_id), None)
    if document is None:
        raise ValueError(f"unknown router2 document: {doc_id}")
    registry_key = str(document.get("registry_key") or document["id"])
    filename = str(document.get("filename") or "")
    filters = {"doc_id": corpus_doc_id(registry_key)}
    if filename:
        filters["filename"] = Path(filename).with_suffix(".txt").name
    return filters


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("status") != "ready":
        raise ValueError(f"router status: {plan.get('status')}")
    if plan.get("problems"):
        raise ValueError("router contract: " + ", ".join(map(str, plan["problems"])))
    steps = plan.get("tools") or []
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise ValueError("router plan has no tools or exceeds step limit")
    for index, step in enumerate(steps, 1):
        if step.get("step") != index:
            raise ValueError("router steps must be consecutive")
        skill = step.get("skill")
        if skill not in {"exact_point", "corpus_point_search", "vector_search", "definition_search", "find_all"}:
            raise ValueError(f"unsupported skill: {skill}")
        if skill == "exact_point" and (not step.get("doc_id") or not step.get("point_ref")):
            raise ValueError("exact_point needs doc_id and point_ref")
        if skill != "exact_point" and not str(step.get("query") or "").strip():
            raise ValueError(f"{skill} needs query")
        after = step.get("after") or []
        if not isinstance(after, list) or any(type(value) is not int or value < 1 or value >= index for value in after):
            raise ValueError(f"invalid dependencies for step {index}")
    final = plan.get("final")
    if final is not None and (not isinstance(final, dict) or not str(final.get("instruction") or "").strip()):
        raise ValueError("invalid final instruction")


def _model_chain(primary: str, fallbacks: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(model.strip() for model in (primary, *fallbacks) if model.strip()))


class QdrantSkills:
    """Router2's five skill names backed by the existing retrieval primitives."""

    def __init__(self, config: IndexingConfig, documents: dict[str, Any]):
        self.config = config
        self.documents = documents

    def run(self, step: dict[str, Any]) -> tuple[list[RetrievedItem], list[str]]:
        skill = step["skill"]
        query = str(step.get("query") or "")
        doc_id = step.get("doc_id")
        filters = document_filters(doc_id, self.documents)
        if skill == "definition_search":
            result = definition_search(query=query, doc_id=doc_id)
            items = []
            for index, hit in enumerate(result["hits"]):
                digest = hashlib.sha1(f"{hit.get('doc_id')}|{hit.get('point_ref')}|{hit.get('text')}".encode("utf-8")).hexdigest()[:16]
                items.append(RetrievedItem(
                    chunk_id=f"definition_{digest}",
                    doc_id=str(hit.get("doc_id") or ""),
                    filename=str(hit.get("file") or ""),
                    doc_name=str(hit.get("file") or ""),
                    point_number=str(hit.get("point_ref") or ""),
                    text=str(hit.get("text") or ""),
                    source_tool=skill,
                ))
            return items, []
        if skill == "exact_point":
            point_ref = str(step["point_ref"])
            found = run_point_tool(ToolRequest(
                query=point_ref, filters={**filters, "point_number": point_ref}, limit=100,
            ), self.config)
            point_items = list(found.items)
            text_fallback = False
            if point_items and not any(item.point_number == point_ref for item in point_items):
                marker = re.compile(rf"^\s*{re.escape(point_ref)}(?:\s|[.:)])")
                leading = next((item for item in point_items if marker.match(item.text or "")), None)
                if leading is not None:
                    text_fallback = True
                    point_items = [replace(leading, point_number=point_ref, total_parts=1)]
            assembled = assemble_point_parts(point_items, point_number=point_ref)
            if assembled is None:
                return [], list(found.warnings)
            warnings = list(found.warnings)
            if text_fallback:
                warnings.append("point number is absent from metadata; returned matching text chunk")
            elif not assembled.is_complete:
                warnings.append("point text is incomplete")
            return [replace(assembled.items[0], text=assembled.text, is_complete_point=assembled.is_complete and not text_fallback)], warnings
        request = ToolRequest(query=query, filters=filters, limit=100 if skill == "find_all" else 12)
        if skill == "vector_search":
            found = run_vector_tool(request, self.config)
        else:
            found = run_payload_tool(request, self.config, search_fields=["text"])
        warnings = list(found.warnings)
        if skill == "find_all":
            warnings.append("find_all currently returns at most 100 matching chunks; result is not guaranteed exhaustive")
        return list(found.items), warnings

    def hydrate_full_points(self, items: list[RetrievedItem]) -> tuple[list[RetrievedItem], list[str]]:
        """Replace split search hits with their complete point text."""
        hydrated: list[RetrievedItem] = []
        warnings: list[str] = []
        cache: dict[str, RetrievedItem | None] = {}
        for item in items:
            if item.is_complete_point or not item.point_number:
                hydrated.append(item)
                continue
            identity = str(item.point_identity_key or "").strip()
            cache_key = identity or f"{item.doc_id}|{item.filename}|{item.point_number}"
            if cache_key not in cache:
                if identity:
                    filters = {"point_identity_key": identity}
                else:
                    filters = {
                        key: value for key, value in {
                            "doc_id": item.doc_id,
                            "filename": item.filename,
                            "point_number": item.point_number,
                        }.items() if value
                    }
                found = run_point_tool(ToolRequest(
                    query=item.point_number, filters=filters, limit=100,
                ), self.config)
                assembled = assemble_point_parts(list(found.items), point_number=item.point_number)
                if assembled is not None and assembled.is_complete:
                    cache[cache_key] = replace(
                        assembled.items[0],
                        text=assembled.text,
                        score=item.score,
                        source_tool=item.source_tool,
                        matched=item.matched,
                        is_complete_point=True,
                    )
                else:
                    cache[cache_key] = None
                    warnings.extend(found.warnings)
                    warnings.append(
                        f"full point hydration incomplete: {item.doc_name or item.doc_id}, "
                        f"point {item.point_number}"
                    )
            hydrated.append(cache[cache_key] or item)
        return hydrated, warnings


def _request_final_answer_payload(
    client: Any,
    *,
    request: RuntimeRequest,
    items: list[RetrievedItem],
    pack: dict[str, Any],
    answer_model: str,
) -> dict[str, Any]:
    payload = {
        "query": request.query,
        "evidence": [{
            "chunk_id": item.chunk_id, "doc_name": item.doc_name,
            "point_number": item.point_number, "text": item.text,
        } for item in items],
        "output_contract": pack.get("output_contract"),
    }
    base_messages = [
        {"role": "system", "content": str(pack.get("prompt") or "")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    last_error: Exception | None = None
    for attempt in range(FINAL_JSON_ATTEMPTS):
        messages = list(base_messages)
        if attempt:
            messages.append({
                "role": "system",
                "content": (
                    "Предыдущий ответ не удалось разобрать как JSON. Сформируй ответ заново. "
                    "Верни только один корректный JSON-объект с ключами answer и citations. "
                    "Обязательно экранируй двойные кавычки и переводы строк внутри answer."
                ),
            })
        response = client.chat(LLMRequest(
            messages=messages,
            model=answer_model, temperature=0.1, max_tokens=4096,
            response_format={"type": "json_object"},
        ))
        try:
            return _parse_final_answer_json(response.content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _parse_final_answer_json(content: str) -> dict[str, Any]:
    """Parse final JSON and repair raw quotes inside the answer string.

    Some OpenAI-compatible providers declare JSON mode but still return source
    quotations such as ``"Не готов"`` without JSON escaping.  The outer shape
    remains unambiguous, so recover only that narrow final-answer contract.
    """
    try:
        return parse_json_object(content)
    except json.JSONDecodeError as original:
        text = content.strip()
        answer_match = re.match(r'^\{\s*"answer"\s*:\s*"', text)
        boundaries = list(re.finditer(r'"\s*,\s*"citations"\s*:\s*', text))
        if answer_match is None or not boundaries or not text.endswith("}"):
            raise original
        boundary = boundaries[-1]
        citations_text = text[boundary.end():-1].strip()
        citations = json.loads(citations_text)
        if not isinstance(citations, list):
            raise original

        raw_answer = text[answer_match.end():boundary.start()]
        escaped: list[str] = []
        for index, char in enumerate(raw_answer):
            if char == '"':
                slash_count = 0
                cursor = index - 1
                while cursor >= 0 and raw_answer[cursor] == "\\":
                    slash_count += 1
                    cursor -= 1
                if slash_count % 2 == 0:
                    escaped.append("\\")
            escaped.append(char)
        answer = json.loads('"' + "".join(escaped) + '"')
        return {"answer": answer, "citations": citations}


def run_router2(
    question: str,
    *,
    router_model: str = DEFAULT_ROUTER_MODEL,
    answer_model: str = DEFAULT_ANSWER_MODEL,
    router_fallback_models: tuple[str, ...] = DEFAULT_ROUTER_FALLBACK_MODELS,
    answer_fallback_models: tuple[str, ...] = DEFAULT_ANSWER_FALLBACK_MODELS,
    config: IndexingConfig | None = None,
    route_complete: Callable[[str, str], str] | None = None,
    skill_backend: Any | None = None,
    answer_provider: Any | None = None,
    selected_option: str | None = None,
    prior_plan: dict[str, Any] | None = None,
) -> Router2Result:
    documents = load_documents()
    router_model_used = router_model
    model_failures: list[str] = []
    if prior_plan is not None:
        plan = dict(prior_plan)
        clarification = plan.get("clarification") or {}
        if selected_option not in clarification.get("option_ids", []):
            raise ValueError("selection is not one of the offered options")
        plan["clarification"] = None
        plan["status"] = "ready"
        if clarification.get("field") == "document":
            plan["tools"] = [{**step, "doc_id": step.get("doc_id") or selected_option} for step in plan["tools"]]
        else:
            candidates = abbreviation_candidates(question, load_abbreviations())
            source = next((row["abbreviation"] for row in candidates if selected_option in row["values"]), None)
            if source is None:
                raise ValueError("selected term has no abbreviation in the question")
            plan["tools"] = [{
                **step,
                "query": str(step.get("query") or "").replace(source, selected_option),
                "expansions": [{"abbreviation": source, "value": selected_option}],
            } for step in plan["tools"]]
    else:
        if route_complete is None:
            plan = None
            last_error: Exception | None = None
            for candidate_model in _model_chain(router_model, router_fallback_models):
                try:
                    client = build_chat_client("polza", candidate_model)

                    def candidate_complete(system: str, user: str, *, _client=client, _model=candidate_model) -> str:
                        return _client.chat(LLMRequest(
                            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                            model=_model, temperature=0.1, max_tokens=4096,
                            response_format={"type": "json_object"},
                        )).content

                    candidate_plan = route_question(question, candidate_complete, documents=documents)
                    if not (
                        candidate_plan.get("status") == "needs_clarification"
                        and not candidate_plan.get("problems")
                    ):
                        validate_plan(candidate_plan)
                    plan = candidate_plan
                    router_model_used = candidate_model
                    break
                except Exception as exc:
                    last_error = exc
                    model_failures.append(f"router:{candidate_model}:{type(exc).__name__}: {exc}")
            if plan is None:
                assert last_error is not None
                raise RuntimeError("all router models failed") from last_error
        else:
            plan = route_question(question, route_complete, documents=documents)
    result = Router2Result(
        status=str(plan.get("status")), question=question, plan=plan,
        router_model_used=router_model_used, model_failures=model_failures,
    )
    if router_model_used != router_model:
        result.warnings.append(f"router model fallback: {router_model} -> {router_model_used}")
    if plan.get("status") == "needs_clarification" and not plan.get("problems"):
        result.clarification = plan.get("clarification")
        return result
    validate_plan(plan)
    backend = skill_backend or QdrantSkills(config or IndexingConfig.from_env(), documents)
    completed: dict[int, list[RetrievedItem]] = {}
    for step in plan["tools"]:
        step_number = step["step"]
        if any(dependency not in completed for dependency in step.get("after") or []):
            raise ValueError(f"unmet dependency at step {step_number}")
        items, warnings = backend.run(step)
        completed[step_number] = items
        result.calls.append({
            "step": step_number, "skill": step["skill"], "query": step.get("query"),
            "doc_id": step.get("doc_id"), "point_ref": step.get("point_ref"),
            "hits": len(items), "warnings": warnings,
        })
        result.warnings.extend(warnings)
    seen: set[str] = set()
    evidence = []
    width = max((len(items) for items in completed.values()), default=0)
    for rank in range(width):
        for items in completed.values():
            if rank >= len(items):
                continue
            item = items[rank]
            key = item.point_identity_key or item.chunk_id or f"{item.doc_id}|{item.point_number}|{item.text}"
            if key not in seen:
                seen.add(key)
                evidence.append(item)
            if len(evidence) >= MAX_EVIDENCE:
                break
        if len(evidence) >= MAX_EVIDENCE:
            break
    hydrate = getattr(backend, "hydrate_full_points", None)
    if callable(hydrate):
        evidence, hydration_warnings = hydrate(evidence)
        result.warnings.extend(hydration_warnings)
    result.evidence = [asdict(item) for item in evidence]
    if not evidence:
        result.status = "not_found"
        result.answer = "Поиск не нашёл подтверждающих фрагментов."
        return result
    final_instruction = str((plan.get("final") or {}).get("instruction") or "").strip()
    if answer_provider is None:
        answer_state: dict[str, Any] = {"used": "", "failures": []}

        def answer_provider(request: RuntimeRequest, items: list[RetrievedItem], pack: dict[str, Any]) -> dict[str, Any]:
            last_error: Exception | None = None
            for candidate_model in _model_chain(answer_model, answer_fallback_models):
                try:
                    answer_client = build_chat_client("polza", candidate_model)
                    payload = _request_final_answer_payload(
                        answer_client, request=request, items=items, pack=pack,
                        answer_model=candidate_model,
                    )
                    answer_state["used"] = candidate_model
                    return payload
                except Exception as exc:
                    last_error = exc
                    answer_state["failures"].append(
                        f"answer:{candidate_model}:{type(exc).__name__}: {exc}"
                    )
            assert last_error is not None
            raise RuntimeError("all answer models failed") from last_error
    else:
        answer_state = {"used": answer_model, "failures": []}
    final_query = question if not final_instruction else f"{question}\n\nЗадание для ответа: {final_instruction}"
    answer = build_final_answer("prompt", provider=answer_provider).answer(
        RuntimeRequest(query=final_query, user_query=final_query, limit=MAX_EVIDENCE),
        evidence,
        limit=MAX_EVIDENCE,
    )
    result.warnings.extend(answer.warnings)
    result.answer_model_used = str(answer_state["used"] or "")
    result.model_failures.extend(answer_state["failures"])
    if result.answer_model_used and result.answer_model_used != answer_model:
        result.warnings.append(f"answer model fallback: {answer_model} -> {result.answer_model_used}")
    if any(warning.startswith("prompt final answer failed:") for warning in answer.warnings):
        result.status = "technical_error"
        result.answer = "Финальная модель не ответила; найденные фрагменты сохранены в трассе."
    else:
        result.answer = answer.answer
        result.citations = [citation.to_dict() for citation in answer.citations]
        result.status = "answered"
    return result
