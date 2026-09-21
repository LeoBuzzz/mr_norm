from __future__ import annotations

from mr_norm.retrieval.document_knowledge import QueryTermMatches
from mr_norm.runtime.prompts import load_prompt_pack_by_role
from mr_norm.runtime.query_planner import _normalize_tool_queries


def _query_planning_prompt() -> str:
    return str(load_prompt_pack_by_role("query_planning").get("prompt") or "")


def test_query_planning_prompt_requires_distinct_payload_vector() -> None:
    prompt = _query_planning_prompt()
    assert "payload and vector MUST receive DIFFERENT query strings" in prompt
    assert "payload and vector queries must differ after the first" in prompt


def test_query_planning_prompt_requires_decomposition() -> None:
    prompt = _query_planning_prompt()
    assert "DECOMPOSITION" in prompt
    assert "2–3 vector sub-queries" in prompt or "2-3 vector sub-queries" in prompt


def test_query_planning_prompt_contains_example() -> None:
    prompt = _query_planning_prompt()
    assert "требования к заземлению выше 1 кВ" in prompt
    assert "заземляющие устройства электроустановок выше 1 кВ" in prompt
    assert "EXAMPLE (one-shot)" in prompt


def test_normalize_tool_queries_preserves_vector_decomposition_when_exact_phrase() -> None:
    original = "Какое наведенное напряжение безопасно?"
    sub_one = "наведенное напряжение на воздушной линии"
    sub_two = "безопасное наведенное напряжение"
    term_matches = QueryTermMatches(exact_phrase_terms=("наведенное напряжение",))

    queries = _normalize_tool_queries(
        {
            "payload": [original, "наведенное напряжение"],
            "vector": [original, sub_one, sub_two],
            "point": [],
        },
        original,
        term_matches=term_matches,
        significant_words=["наведенное напряжение"],
        question_type="factual",
    )

    assert queries["vector"][0] == original
    assert sub_one in queries["vector"]
    assert sub_two in queries["vector"]
    assert queries["vector"].index(sub_one) < queries["vector"].index(sub_two)


def test_normalize_tool_queries_payload_keeps_exact_phrase_first() -> None:
    original = "Какое наведенное напряжение безопасно?"
    phrase = "наведенное напряжение"
    term_matches = QueryTermMatches(exact_phrase_terms=(phrase,))

    queries = _normalize_tool_queries(
        {"payload": ["напряжение"], "vector": [original], "point": []},
        original,
        term_matches=term_matches,
        significant_words=[phrase],
        question_type="factual",
    )

    assert queries["payload"][0] == original
    assert phrase in queries["payload"]
    assert queries["payload"].index(phrase) <= 1


def test_normalize_tool_queries_prefers_llm_vector_over_default_fillers() -> None:
    original = "требования к заземлению выше 1 кВ"
    paraphrase = "заземляющие устройства электроустановок выше 1 кВ"
    term_matches = QueryTermMatches()

    queries = _normalize_tool_queries(
        {
            "payload": [original, "заземление", "заземляющие устройства"],
            "vector": [original, paraphrase],
            "point": [],
        },
        original,
        term_matches=term_matches,
        significant_words=["требования", "заземлению", "выше"],
        question_type="factual",
    )

    assert queries["vector"][0] == original
    assert paraphrase in queries["vector"]
    assert queries["payload"][1:] != queries["vector"][1:]
