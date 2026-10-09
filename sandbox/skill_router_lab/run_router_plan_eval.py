"""Validate frozen router-plan cases, or explicitly run a live pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from router_contracts import RouterPlanConfig
from router_planning import plan_skill_query


def _single_attempt_http_post(url: str, headers: dict[str, str], body: bytes, timeout_sec: float) -> dict:
    """Use the existing client hook while keeping one physical request per logical call."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def _case_knowledge(full_knowledge, case: dict):
    """Freeze dictionary inputs to the candidates recorded in one gold case."""
    from mr_norm.retrieval.document_knowledge import DocumentKnowledgeIndex

    supplied = [str(x) for x in case.get("candidates", {}).get("terms", [])]
    allowed = {x.casefold() for x in supplied}
    abbreviations = [row for row in full_knowledge.abbreviations if str(row.get("abbreviation", "")).casefold() in allowed or str(row.get("expansion", "")).casefold() in allowed]
    covered = {str(value).casefold() for row in abbreviations for value in (row.get("abbreviation", ""), row.get("expansion", ""))}
    terms = [{"label": value} for value in supplied if value.casefold() not in covered]
    return DocumentKnowledgeIndex(abbreviations=abbreviations, terms=terms)


def validate_cases(payload: dict) -> None:
    cases = payload.get("cases")
    if payload.get("schema_version") != "router_plan_cases.v1" or not isinstance(cases, list) or len(cases) != 40:
        raise ValueError("fixture must use router_plan_cases.v1 and contain exactly 40 cases")
    full_catalog = payload.get("catalog_documents")
    if not isinstance(full_catalog, list):
        raise ValueError("fixture must provide catalog_documents separately from per-case candidate documents")
    full_catalog_ids = {doc.get("doc_id") for doc in full_catalog if isinstance(doc, dict)}
    if any(not isinstance(doc_id, str) or re.fullmatch(r"doc_[0-9a-f]{16}", doc_id) is None for doc_id in full_catalog_ids):
        raise ValueError("catalog_documents contains an invalid doc_id")
    ids = [row.get("id") for row in cases]
    if len(set(ids)) != 40 or any(not x for x in ids):
        raise ValueError("case IDs must be present and unique")
    source_payload = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    source_cases = {str(row["id"]): row for row in source_payload.get("fixtures", [])}
    for split in ("tuning", "holdout"):
        rows = [row for row in cases if row.get("split") == split]
        if len(rows) != 20:
            raise ValueError(f"expected 20 {split} cases")
    expected_groups = {"simple_single": 8, "compound_independent": 6, "vocabulary": 6, "dialog": 8, "ambiguous": 6, "dependent_unsupported": 6}
    actual_groups = {name: sum(row.get("group") == name for row in cases) for name in expected_groups}
    if actual_groups != expected_groups:
        raise ValueError(f"invalid group distribution: {actual_groups}")
    for name, total in expected_groups.items():
        for split in ("tuning", "holdout"):
            if sum(row.get("group") == name and row.get("split") == split for row in cases) != total // 2:
                raise ValueError(f"group {name} must be split evenly between tuning and holdout")
    metamorphic: dict[str, list[dict]] = {}
    for row in cases:
        if row.get("metamorphic_pair"):
            metamorphic.setdefault(str(row["metamorphic_pair"]), []).append(row)
    if not metamorphic or any(len(rows) != 2 or {row.get("split") for row in rows} != {"tuning"} for rows in metamorphic.values()):
        raise ValueError("metamorphic pairs must contain exactly two tuning cases and no holdout case")
    for pair_id, pair in metamorphic.items():
        left_tasks = pair[0].get("expected", {}).get("tasks", [])
        right_tasks = pair[1].get("expected", {}).get("tasks", [])
        if len(left_tasks) != len(right_tasks):
            raise ValueError(f"metamorphic pair {pair_id} has incompatible task counts")
        for left, right in zip(left_tasks, right_tasks):
            if left.get("skill") != right.get("skill") or left.get("scope", "corpus") != right.get("scope", "corpus"):
                raise ValueError(f"metamorphic pair {pair_id} has incompatible skill or scope")
            left_concepts = {v.casefold() for e in left.get("required_semantic_elements", []) if e.get("kind") == "object" for v in e.get("variants", [])}
            right_concepts = {v.casefold() for e in right.get("required_semantic_elements", []) if e.get("kind") == "object" for v in e.get("variants", [])}
            if not left_concepts or not right_concepts or not left_concepts.intersection(right_concepts):
                raise ValueError(f"metamorphic pair {pair_id} has incompatible concepts")
    for row in cases:
        if row.get("split") not in {"tuning", "holdout"} or row.get("provenance") not in {"synthetic", "existing_fixture"}:
            raise ValueError(f"{row.get('id')}: invalid split or provenance")
        if row.get("provenance") == "existing_fixture":
            source = source_cases.get(str(row.get("source_case_id") or ""))
            if source is None or source.get("split") != row.get("split") or source.get("question") != row.get("question"):
                raise ValueError(f"{row.get('id')}: existing fixture provenance does not match fixtures.json")
        if not isinstance(row.get("question"), str) or not isinstance(row.get("context", {}), dict) or not isinstance(row.get("candidates", {}), dict) or not isinstance(row.get("expected", {}), dict):
            raise ValueError(f"{row.get('id')}: malformed case fields")
        expected = row["expected"]
        if expected.get("complexity") not in {"simple", "complex"} or expected.get("status") not in {"ready", "needs_clarification", "deferred", "error"}:
            raise ValueError(f"{row.get('id')}: invalid expected complexity/status")
        documents = row["candidates"].get("documents", [])
        candidate_ids = {doc.get("doc_id") for doc in documents if isinstance(doc, dict)}
        if any(not isinstance(doc_id, str) or re.fullmatch(r"doc_[0-9a-f]{16}", doc_id) is None for doc_id in candidate_ids):
            raise ValueError(f"{row.get('id')}: candidate doc_id must be a real catalog-shaped ID")
        if not candidate_ids <= full_catalog_ids:
            raise ValueError(f"{row.get('id')}: candidate doc_id is absent from catalog_documents")
        context_doc_ids = {
            turn.get("resolved_doc_id")
            for turn in row.get("context", {}).get("turns", [])
            if isinstance(turn, dict) and turn.get("resolved_doc_id")
        }
        if not context_doc_ids <= full_catalog_ids:
            raise ValueError(f"{row.get('id')}: context resolved_doc_id must exist in catalog_documents")
        for task in expected.get("tasks", []):
            required = {"skill", "doc_id", "point_ref", "coverage", "constraints", "evidence", "depends_on"}
            if not isinstance(task, dict) or not required <= set(task):
                raise ValueError(f"{row.get('id')}: malformed expected task")
            pending_source = str((row.get("pending_clarification") or {}).get("original_query") or "")
            if not isinstance(task["evidence"], list) or not task["evidence"] or any(not isinstance(x, str) or not x or x not in row["question"] and x not in pending_source and not any(x in str(turn.get("user_text", "")) for turn in row["context"].get("turns", [])) for x in task["evidence"]):
                raise ValueError(f"{row.get('id')}: expected evidence is not grounded")
            if task["doc_id"] is not None and task["doc_id"] not in full_catalog_ids:
                raise ValueError(f"{row.get('id')}: expected doc_id is absent from catalog_documents")
            if task["doc_id"] is not None and task["doc_id"] not in candidate_ids:
                raise ValueError(f"{row.get('id')}: expected doc_id is absent from frozen per-case candidates")
            semantic_elements = task.get("required_semantic_elements", [])
            if not isinstance(semantic_elements, list) or not semantic_elements:
                raise ValueError(f"{row.get('id')}: every expected task requires nonempty required_semantic_elements")
            for element in semantic_elements:
                if not isinstance(element, dict) or not {"kind", "label", "variants"} <= set(element) or not isinstance(element["label"], str) or not element["label"].strip() or not isinstance(element["variants"], list) or not element["variants"] or any(not isinstance(value, str) or not value.strip() for value in element["variants"]) or "require_concept" in element and not isinstance(element["require_concept"], bool):
                    raise ValueError(f"{row.get('id')}: malformed required semantic element")
                if any(len(re.findall(r"[\w-]+", value)) > 5 or re.search(r"[?!;:]", value) for value in element["variants"]):
                    raise ValueError(f"{row.get('id')}: semantic variants must be atomic labels, not sentence-like phrases")
                if element["kind"] not in {"point", "quantity", "intent"} and len(element["variants"]) < 2:
                    raise ValueError(f"{row.get('id')}: semantic atoms require multiple allowed variants")
                if element["kind"] not in {"point", "quantity", "intent"}:
                    pending_query = str((row.get("pending_clarification") or {}).get("original_query") or "")
                    pending_question = str((row.get("pending_clarification") or {}).get("question") or "")
                    context_source = " ".join(str(turn.get("user_text", "")) for turn in row.get("context", {}).get("turns", []) if isinstance(turn, dict))
                    source_text = " ".join([row["question"], pending_query, pending_question, context_source])
                    dictionary_terms = [str(term) for term in row.get("candidates", {}).get("terms", [])]
                    if not all(_all_significant_words_grounded(value, source_text, dictionary_terms) for value in element["variants"]):
                        raise ValueError(f"{row.get('id')}: semantic variant has ungrounded significant words")
            for mention in task.get("required_document_mentions", []):
                if not isinstance(mention, dict) or not isinstance(mention.get("variants"), list) or not mention["variants"]:
                    raise ValueError(f"{row.get('id')}: malformed required document mention")
        if expected["status"] == "needs_clarification":
            subject = expected.get("clarification_subject")
            if not isinstance(subject, dict) or not isinstance(subject.get("label"), str) or not subject["label"].strip() or not isinstance(subject.get("variants"), list) or not subject["variants"] or any(not isinstance(value, str) or not value.strip() for value in subject["variants"]):
                raise ValueError(f"{row.get('id')}: clarification cases require a labeled subject with allowed variants")
        pending = row.get("pending_clarification")
        if pending is not None:
            if not isinstance(pending, dict) or not {"question", "field", "options", "original_query", "original_task"} <= set(pending) or set(pending) - {"question", "field", "options", "original_query", "original_task", "target_task_ids"}:
                raise ValueError(f"{row.get('id')}: malformed pending clarification schema")
            original_task = pending["original_task"]
            if not isinstance(pending["original_query"], str) or not pending["original_query"].strip() or not isinstance(original_task, dict) or not {"intent_text", "skill", "coverage", "semantic_elements"} <= set(original_task) or not original_task["semantic_elements"]:
                raise ValueError(f"{row.get('id')}: pending clarification must preserve source question, task, and semantic elements")


def _task_key(task: dict) -> tuple:
    constraints = tuple(sorted((x.get("kind"), x.get("text"), x.get("polarity")) for x in task.get("constraints", [])))
    dependencies = tuple(task.get("depends_on", []))
    replacements = tuple(sorted(task.get("replaces", [])))
    scope = task.get("scope")
    if scope is None:
        scope = "document" if task.get("doc_id") else "corpus"
    return task.get("skill"), scope, task.get("doc_id"), task.get("point_ref"), task.get("coverage"), constraints, dependencies, replacements


def _contains_variant(haystack: str, variants: list[str]) -> bool:
    text = re.sub(r"\s+", " ", haystack.replace("ё", "е").casefold())
    for value in variants:
        candidate = re.sub(r"\s+", " ", str(value).replace("ё", "е").casefold()).strip()
        if candidate and re.search(rf"(?<![\w]){re.escape(candidate)}(?![\w])", text):
            return True
    return False


def _all_significant_words_grounded(variant: str, sources: str, dictionary_terms: list[str]) -> bool:
    stop = {"и", "а", "в", "во", "на", "по", "из", "для", "к", "с", "о", "об", "под", "при", "не", "ли"}
    def stem(word: str) -> str:
        for ending in ("иями", "ями", "ами", "ого", "ему", "ыми", "ими", "иях", "иях", "ией", "ией", "иям", "иях", "иях", "ией", "иях", "иях", "ией", "иев", "ами", "ями", "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ых", "их", "ым", "им", "ом", "ем", "ам", "ям", "ах", "ях", "а", "я", "ы", "и", "е", "у", "ю"):
            if len(word) > len(ending) + 2 and word.endswith(ending):
                return word[:-len(ending)]
        return word
    words = [word for word in re.findall(r"[\w-]+", variant.casefold().replace("ё", "е")) if word not in stop and not word.isdigit()]
    source_words = [word for word in re.findall(r"[\w-]+", sources.casefold().replace("ё", "е")) if word not in stop]
    candidate_words = [word for term in dictionary_terms for word in re.findall(r"[\w-]+", term.casefold().replace("ё", "е")) if word not in stop]
    if not words:
        numbers = re.findall(r"\d+(?:[._]\d+)*", variant)
        return bool(numbers) and all(number in sources for number in numbers)
    return all(
        word in source_words or any(stem(word) == stem(source_word) or min(len(stem(word)), len(stem(source_word))) >= 3 and stem(word)[:3] == stem(source_word)[:3] for source_word in source_words)
        or word in candidate_words or any(stem(word) == stem(candidate_word) or min(len(stem(word)), len(stem(candidate_word))) >= 3 and stem(word)[:3] == stem(candidate_word)[:3] for candidate_word in candidate_words)
        for word in words
    )


def _semantic_check(expected_task: dict, actual_task: dict) -> dict[str, bool]:
    query = str(actual_task.get("query") or "")
    intent = str(actual_task.get("intent_text") or "")
    concepts = actual_task.get("concepts") or []
    concept_text = " ".join(
        [str(item.get("surface") or "") for item in concepts]
        + [str(variant) for item in concepts for variant in item.get("variants", [])]
    )
    required = list(expected_task.get("required_semantic_elements", []))
    # Explicit constraints are semantic labels too, even when an older fixture
    # has not yet annotated separate intent/topic elements.
    for constraint in expected_task.get("constraints", []):
        required.append({"label": constraint.get("text", ""), "variants": constraint.get("variants", [constraint.get("text", "")]), "require_concept": False})
    query_ok = True
    intent_ok = True
    concepts_ok = True
    for element in required:
        variants = element.get("variants") or [element.get("label", "")]
        content_match = _contains_variant(query + " " + intent, variants)
        if element.get("kind") == "intent":
            intent_ok = intent_ok and _contains_variant(intent, variants)
        else:
            query_ok = query_ok and _contains_variant(query, variants)
            intent_ok = intent_ok and content_match
        if element.get("require_concept"):
            concepts_ok = concepts_ok and _contains_variant(concept_text, variants)
    for expected_concept in expected_task.get("concepts", []):
        variants = [expected_concept.get("surface", ""), *expected_concept.get("variants", [])]
        concepts_ok = concepts_ok and _contains_variant(concept_text, variants)
    docs_ok = True
    document_text = " ".join([query, intent, str(actual_task.get("document_mention") or "")])
    for mention in expected_task.get("required_document_mentions", []):
        docs_ok = docs_ok and _contains_variant(document_text, mention.get("variants", []))
    return {"query": query_ok, "intent": intent_ok, "concepts": concepts_ok, "documents": docs_ok}


def _perfect_task_matching(expected_tasks: list[dict], actual_tasks: list[dict], predicate: Any) -> bool:
    """Match repeated structural task keys without reusing an actual task."""
    if len(expected_tasks) != len(actual_tasks):
        return False
    choices = [
        [index for index, actual_task in enumerate(actual_tasks) if _task_key(actual_task) == _task_key(expected_task) and predicate(expected_task, actual_task)]
        for expected_task in expected_tasks
    ]
    order = sorted(range(len(expected_tasks)), key=lambda index: len(choices[index]))

    def visit(position: int, used: set[int]) -> bool:
        if position == len(order):
            return True
        for actual_index in choices[order[position]]:
            if actual_index not in used and visit(position + 1, used | {actual_index}):
                return True
        return False

    return visit(0, set())


def compare_case(expected: dict, actual: dict) -> dict:
    expected_tasks = expected.get("tasks", [])
    actual_tasks = actual.get("tasks", [])
    task_match = sorted((_task_key(x) for x in expected_tasks), key=repr) == sorted((_task_key(x) for x in actual_tasks), key=repr)

    def pair_checks(expected_task: dict, actual_task: dict) -> dict[str, bool]:
        required_quotes = expected_task.get("evidence", [])
        semantic = _semantic_check(expected_task, actual_task)
        return {
            "evidence": all(any(ev.get("quote") == quote for ev in actual_task.get("evidence", [])) for quote in required_quotes),
            **semantic,
        }

    evidence_match = _perfect_task_matching(expected_tasks, actual_tasks, lambda expected_task, actual_task: pair_checks(expected_task, actual_task)["evidence"])
    query_semantics_match = _perfect_task_matching(expected_tasks, actual_tasks, lambda expected_task, actual_task: pair_checks(expected_task, actual_task)["query"])
    intent_semantics_match = _perfect_task_matching(expected_tasks, actual_tasks, lambda expected_task, actual_task: pair_checks(expected_task, actual_task)["intent"])
    concept_semantics_match = _perfect_task_matching(expected_tasks, actual_tasks, lambda expected_task, actual_task: pair_checks(expected_task, actual_task)["concepts"])
    task_alignment_match = _perfect_task_matching(expected_tasks, actual_tasks, lambda expected_task, actual_task: all(pair_checks(expected_task, actual_task).values()))
    expected_field = expected.get("clarification_field")
    actual_field = (actual.get("clarification") or {}).get("field")
    clarification_match = expected_field == actual_field if expected_field else actual_field is None
    expected_subject = expected.get("clarification_subject")
    clarification_question = str((actual.get("clarification") or {}).get("question") or "")
    if expected_subject:
        variants = expected_subject.get("variants") or [expected_subject.get("label", "")]
        generic = bool(re.search(r"уточните\s+(?:вопрос|запрос)|что\s+вы\s+имеете\s+в\s+виду|поясните\s+(?:свой\s+)?вопрос", clarification_question, re.IGNORECASE))
        clarification_subject_match = bool(clarification_question) and not generic and _contains_variant(clarification_question, variants)
    else:
        clarification_subject_match = actual_field is None or bool(clarification_question.strip())
    expected_action = expected.get("dialog_action")
    dialog_action_match = expected_action is None or expected_action == actual.get("dialog_action")
    content_fields = []
    for task in actual_tasks:
        content_fields.extend(str(task.get(key) or "") for key in ("query", "intent_text", "document_mention"))
        for concept in task.get("concepts", []):
            content_fields.append(str(concept.get("surface") or ""))
            content_fields.extend(str(value) for value in concept.get("variants", []))
        for constraint in task.get("constraints", []):
            content_fields.append(str(constraint.get("text") or ""))
    clarification = actual.get("clarification") or {}
    content_fields.append(str(clarification.get("question") or ""))
    content_fields.extend(str(option.get(key) or "") for option in clarification.get("options", []) for key in ("id", "label"))
    serialized_actual = " ".join(content_fields).casefold()
    forbidden_match = True
    for forbidden in expected.get("forbidden_substitutions", []):
        token = str(forbidden).split("→")[-1].strip().casefold()
        if token and re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", serialized_actual):
            forbidden_match = False
    return {
        "classification_match": expected.get("complexity") == actual.get("complexity"),
        "status_match": expected.get("status") == actual.get("status"),
        "tasks_match": task_match,
        "evidence_match": evidence_match,
        "query_semantics_match": query_semantics_match,
        "intent_semantics_match": intent_semantics_match,
        "concept_semantics_match": concept_semantics_match,
        "task_alignment_match": task_alignment_match,
        "clarification_match": clarification_match,
        "clarification_subject_match": clarification_subject_match,
        "dialog_action_match": dialog_action_match,
        "forbidden_substitutions_match": forbidden_match,
        "plan_match": expected.get("complexity") == actual.get("complexity") and expected.get("status") == actual.get("status") and task_match and task_alignment_match and evidence_match and query_semantics_match and intent_semantics_match and concept_semantics_match and clarification_match and clarification_subject_match and dialog_action_match and forbidden_match,
    }


def compare_metamorphic_pair(expected_a: dict, actual_a: dict, expected_b: dict, actual_b: dict) -> dict[str, bool]:
    """Measure paraphrase invariance separately from per-case/holdout scores."""
    tasks_a, tasks_b = actual_a.get("tasks", []), actual_b.get("tasks", [])
    if len(tasks_a) != len(tasks_b):
        return {"task_count_match": False, "skill_scope_match": False, "concepts_compatible": False, "passed": False}
    skill_scope = all(
        left.get("skill") == right.get("skill")
        and (left.get("scope") or ("document" if left.get("doc_id") else "corpus"))
        == (right.get("scope") or ("document" if right.get("doc_id") else "corpus"))
        for left, right in zip(tasks_a, tasks_b)
    )
    concept_results = []
    for et_a, at_a, et_b, at_b in zip(expected_a.get("tasks", []), tasks_a, expected_b.get("tasks", []), tasks_b):
        allowed_a = [v for e in et_a.get("required_semantic_elements", []) if e.get("kind") == "object" for v in e.get("variants", [])]
        allowed_b = [v for e in et_b.get("required_semantic_elements", []) if e.get("kind") == "object" for v in e.get("variants", [])]
        shared_allowed = {re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip() for value in allowed_a}.intersection(
            re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip() for value in allowed_b
        )
        concepts_a = [v for c in at_a.get("concepts", []) for v in [c.get("surface", ""), *c.get("variants", [])]]
        concepts_b = [v for c in at_b.get("concepts", []) for v in [c.get("surface", ""), *c.get("variants", [])]]
        concept_results.append(bool(shared_allowed) and any(_contains_variant(" ".join(concepts_a), [x]) for x in shared_allowed) and any(_contains_variant(" ".join(concepts_b), [x]) for x in shared_allowed))
    concepts_ok = bool(concept_results) and all(concept_results)
    return {"task_count_match": True, "skill_scope_match": skill_scope, "concepts_compatible": concepts_ok, "passed": skill_scope and concepts_ok}


def _latency_summary(records: list[dict], role: str) -> dict:
    values = sorted(float(call["elapsed_sec"]) for row in records for call in row["actual"].get("trace", {}).get("calls", []) if call.get("role") == role and call.get("elapsed_sec") is not None)
    if not values:
        return {"count": 0, "p50_sec": None, "p95_sec": None}
    percentile = lambda fraction: values[round((len(values) - 1) * fraction)]
    return {"count": len(values), "p50_sec": percentile(0.50), "p95_sec": percentile(0.95)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=HERE / "router_plan_cases_v1.json")
    parser.add_argument("--split", choices=("tuning", "holdout", "all"), default="all")
    parser.add_argument("--light-model")
    parser.add_argument("--heavy-model")
    parser.add_argument("--provider")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true", help="make real LLM calls; requires explicit models and provider")
    args = parser.parse_args()
    raw_bytes = args.cases.read_bytes()
    gold_hash = hashlib.sha256(raw_bytes).hexdigest()
    hash_path = args.cases.with_suffix(".sha256")
    if hash_path.is_file() and hash_path.read_text(encoding="ascii").strip() != gold_hash:
        raise SystemExit(f"frozen fixture hash mismatch: {hash_path}; review the gold change before updating the hash")
    fixture = json.loads(raw_bytes)
    validate_cases(fixture)
    if not args.live:
        print(json.dumps({"status": "fixtures_valid", "case_count": 40, "sha256": gold_hash, "live_not_run": True}, ensure_ascii=False))
        return 0
    if args.split == "all":
        parser.error("live evaluation requires an explicit --split tuning or --split holdout; do not expose holdout during tuning")
    if not (args.light_model and args.heavy_model and args.provider and args.output):
        parser.error("--live requires --provider, --light-model, --heavy-model, and --output")

    from mr_norm.config.paths import ProjectPaths
    from mr_norm.retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
    from mr_norm.retrieval.document_knowledge import load_document_knowledge
    from mr_norm.runtime.llm_clients import build_chat_client

    cases = [x for x in fixture["cases"] if args.split == "all" or x["split"] == args.split]
    metamorphic_cases: dict[str, list[dict]] = {}
    for row in fixture["cases"]:
        if row.get("metamorphic_pair"):
            metamorphic_cases.setdefault(str(row["metamorphic_pair"]), []).append(row)
    knowledge = load_document_knowledge()
    client = build_chat_client(args.provider, args.light_model, timeout_sec=60.0, http_post=_single_attempt_http_post)
    adapter = lambda **kw: client.chat(__import__("mr_norm.runtime.llm_clients", fromlist=["LLMRequest"]).LLMRequest(messages=kw["messages"], model=kw["model"], temperature=0.0, max_tokens=kw["max_tokens"], response_format={"type": "json_object"}))
    records = []
    full_catalog = fixture["catalog_documents"]
    validation_catalog = DocumentCatalog(entries=[DocumentCatalogEntry(catalog_id=str(row["doc_id"]), doc_id=str(row["doc_id"]), doc_name=str(row["doc_name"]), aliases=tuple(row.get("aliases", ())), registry_key=str(row.get("registry_key", ""))) for row in full_catalog])
    for case in cases:
        # Runtime receives the frozen per-case candidate set. The full catalog above
        # is only the validation universe for IDs and context references.
        candidate_rows = case.get("candidates", {}).get("documents", [])
        catalog = DocumentCatalog(entries=[DocumentCatalogEntry(catalog_id=str(row["doc_id"]), doc_id=str(row["doc_id"]), doc_name=str(row["doc_name"]), aliases=tuple(row.get("aliases", ())), registry_key=str(row.get("registry_key", ""))) for row in candidate_rows])
        # Model identity is explicit and never silently changed by this CLI.
        case_knowledge = _case_knowledge(knowledge, case)
        result = plan_skill_query(case["question"], dialog_context=case.get("context"), pending_clarification=case.get("pending_clarification"), catalog=catalog, validation_catalog=validation_catalog, knowledge=case_knowledge, llm_adapter=adapter, config=RouterPlanConfig(args.light_model, args.heavy_model, args.provider))
        actual = result.to_dict()
        records.append({"case_id": case["id"], "split": case["split"], "group": case["group"], "input": {"question": case["question"], "context": case.get("context", {}), "pending_clarification": case.get("pending_clarification"), "candidates": case.get("candidates", {})}, "expected": case["expected"], "actual": actual, "comparison": compare_case(case["expected"], actual)})
    metrics = {}
    for split in ("tuning", "holdout"):
        subset = [row for row in records if row["split"] == split]
        metrics[split] = {
            "case_count": len(subset),
            "classification_correct": sum(row["comparison"]["classification_match"] for row in subset),
            "plan_correct": sum(row["comparison"]["plan_match"] for row in subset),
            "errors": sum(row["actual"]["status"] == "error" for row in subset),
            "unnecessary_clarifications": sum(row["expected"].get("status") != "needs_clarification" and row["actual"].get("status") == "needs_clarification" for row in subset),
            "ready_plan_mismatches": sum(row["actual"].get("status") == "ready" and not row["comparison"]["plan_match"] for row in subset),
            "logical_call_count": sum(row["actual"].get("trace", {}).get("logical_call_count", 0) for row in subset),
            "physical_call_count": sum(row["actual"].get("trace", {}).get("physical_call_count", 0) for row in subset),
            "latency": {role: _latency_summary(subset, role) for role in ("classifier", "simple_parser", "complex_parser")},
            "live_not_run": False,
        }
    records_by_id = {record["case_id"]: record for record in records}
    metamorphic_metrics = {}
    for pair_id, pair in metamorphic_cases.items():
        if all(case["id"] in records_by_id for case in pair):
            left, right = (records_by_id[case["id"]] for case in pair)
            metamorphic_metrics[pair_id] = compare_metamorphic_pair(left["expected"], left["actual"], right["expected"], right["actual"])
    holdout = [row for row in records if row["split"] == "holdout"]
    holdout_gate = None
    if len(holdout) == 20:
        required_complex = [row for row in holdout if row["expected"].get("complexity") == "complex"]
        ready_unsupported = sum(row["actual"].get("status") == "ready" and any(task.get("skill") is None or task.get("depends_on") for task in row["actual"].get("tasks", [])) for row in holdout)
        checks = {
            "classification_at_least_18_of_20": metrics["holdout"]["classification_correct"] >= 18,
            "plan_at_least_18_of_20": metrics["holdout"]["plan_correct"] >= 18,
            "all_required_complex_routed_complex": all(row["actual"].get("complexity") == "complex" for row in required_complex),
            "no_ready_plan_mismatches": metrics["holdout"]["ready_plan_mismatches"] == 0,
            "at_most_one_unnecessary_clarification": metrics["holdout"]["unnecessary_clarifications"] <= 1,
            "no_ready_unsupported_or_dependent_tasks": ready_unsupported == 0,
        }
        holdout_gate = {"passed": all(checks.values()), "checks": checks}
    pair_guard = all(row["passed"] for row in metamorphic_metrics.values()) if metamorphic_metrics else None
    status = "live_gate_passed" if holdout_gate and holdout_gate["passed"] else "live_gate_failed" if holdout_gate else "live_tuning_pair_failed" if pair_guard is False else "live_tuning_complete"
    report = {"status": status, "live_not_run": False, "generated_at": datetime.now(timezone.utc).isoformat(), "gold_sha256": gold_hash, "provider": args.provider, "light_model": args.light_model, "heavy_model": args.heavy_model, "price": None, "metrics": metrics, "metamorphic_pairs": metamorphic_metrics, "metamorphic_guard_passed": pair_guard, "holdout_gate": holdout_gate, "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(
        "# Router plan live evaluation\n\n"
        f"Gold SHA-256: `{gold_hash}`  \nProvider: `{args.provider}`  \nLight model: `{args.light_model}`  \nHeavy model: `{args.heavy_model}`\n\n"
        + "\n".join(f"- {split}: classification {data['classification_correct']}/{data['case_count']}, full plan {data['plan_correct']}/{data['case_count']}, technical errors {data['errors']}" for split, data in metrics.items())
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "cases": len(records), "output": str(args.output), "gold_sha256": gold_hash}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
