import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

from question_parser.model_route import abbreviation_candidates, judge, load_abbreviations, load_documents, route_question

ROOT = Path(__file__).resolve().parents[1] / "sandbox" / "question_parser"


def test_short_lowercase_word_is_not_loaded_as_an_abbreviation():
    table = load_abbreviations()
    found = abbreviation_candidates("Дай текст пункта 127 инструкции по переключениям", table)
    assert [row["abbreviation"] for row in found] == []
    avr = abbreviation_candidates("Дай определение АВР.", table)
    assert avr[0]["values"] == ["аварийно-восстановительные работы", "автоматический ввод резерва"]


def test_unknown_document_id_is_rejected_and_not_replaced():
    def complete(_system: str, _user: str) -> str:
        return json.dumps(
            {
                "complexity": "simple",
                "operation": "text",
                "tools": [{"skill": "exact_point", "query": "переключения", "doc_id": "doc-missing", "point_ref": "127", "expansions": []}],
                "clarification": None,
            },
            ensure_ascii=False,
        )

    result = route_question("Дай текст пункта 127 инструкции по переключениям", complete)
    assert result["tools"][0]["doc_id"] is None
    assert "unknown_doc_id" in result["problems"]


def test_document_table_uses_registry_ids_for_every_corpus_file():
    catalog = load_documents()
    documents = catalog["documents"]
    ids = [item["id"] for item in documents]
    assert len(ids) == 152
    assert len(set(ids)) == 152
    liquidation = next(item for item in documents if item["id"] == "pr_548_12072018")
    assert "инструкция по ликвидации" in liquidation["aliases"]
    assert liquidation["doc_type"] == "Приказ"
    assert liquidation["adoption_date"] == "2018-07-12"
    chapter = next(item for item in documents if item["id"] == "pue_1_2")
    assert chapter["registry_key"] == "pr_204_08072002"
    assert chapter["short_title"] == "ПУЭ, глава 1.2"
    switching = next(item for item in documents if item["id"] == "pr_757_13092018")
    assert switching["aliases"] == ["правила переключений"]


def test_liquidation_instruction_gets_the_registry_id():
    """Grok, full corpus table: the short name resolves to one order, not every file."""
    raw = {
        "complexity": "simple",
        "operation": "text",
        "tools": [
            {
                "skill": "exact_point",
                "query": "текст пункта 9 инструкции по ликвидации аварий",
                "doc_id": "pr_548_12072018",
                "point_ref": "9",
                "expansions": [],
            }
        ],
        "clarification": None,
    }
    result = route_question(
        "Дай текст пункта 9 инструкции по ликвидации аварий.",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["problems"] == []
    assert result["status"] == "ready"
    assert result["operation"] == "text"
    assert result["tools"][0]["doc_id"] == "pr_548_12072018"
    assert result["tools"][0]["point_ref"] == "9"
    assert result["tools"][0]["query"] is None
    assert result["clarification"] is None


def test_complex_question_keeps_definitions_and_both_corpus_queries():
    raw = {
        "complexity": "complex",
        "operation": "compare",
        "tools": [
            {"skill": "definition_search", "query": "оперативный персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": "definition_search", "query": "диспетчерский персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": "vector_search", "query": "чем отличается оперативный персонал от диспетчерского", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": "corpus_point_search", "query": "оперативный персонал диспетчерский персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": None, "query": "сравнить определения и найденные пункты", "doc_id": None, "point_ref": None, "expansions": [], "after": [1, 2, 3, 4]},
        ],
        "clarification": None,
    }
    result = route_question(
        "Чем отличается оперативный персонал от диспетчерского?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["problems"] == []
    assert result["status"] == "ready"
    assert result["operation"] == "compare"
    assert [tool["skill"] for tool in result["tools"]] == [
        "definition_search",
        "definition_search",
        "vector_search",
        "corpus_point_search",
    ]
    assert [tool["query"] for tool in result["tools"]] == [
        "оперативный персонал",
        "диспетчерский персонал",
        "чем отличается оперативный персонал от диспетчерского",
        "оперативный персонал диспетчерский персонал",
    ]
    assert [tool["after"] for tool in result["tools"]] == [[], [], [], []]
    assert result["final"] == {
        "instruction": "сравнить определения и найденные пункты",
        "after": [1, 2, 3, 4],
    }


def test_simple_search_keeps_both_vector_and_text_queries():
    raw = {
        "complexity": "simple",
        "operation": "search",
        "tools": [
            {"skill": "vector_search", "query": "вывод выключателя в ремонт", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": "corpus_point_search", "query": "вывести в ремонт выключатель", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
        ],
        "final": None,
        "clarification": None,
    }
    result = route_question(
        "Как вывести в ремонт выключатель?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["problems"] == []
    assert result["status"] == "ready"
    assert result["complexity"] == "simple"
    assert result["final"] is None
    assert [tool["skill"] for tool in result["tools"]] == ["vector_search", "corpus_point_search"]
    assert [tool["query"] for tool in result["tools"]] == [
        "вывод выключателя в ремонт",
        "вывести в ремонт выключатель",
    ]


def test_search_with_only_a_vector_query_is_incomplete():
    raw = {
        "complexity": "simple",
        "operation": "search",
        "tools": [
            {"skill": "vector_search", "query": "Как вывести в ремонт выключатель", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
        ],
        "final": None,
        "clarification": None,
    }
    result = route_question(
        "Как вывести в ремонт выключатель?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["status"] == "ready"
    assert "missing_text_search" in result["problems"]


def test_off_topic_plan_is_ready_without_tools():
    raw = {
        "complexity": "simple", "operation": "off_topic", "tools": [],
        "final": None, "clarification": None,
    }

    result = route_question(
        "Как приготовить шарлотку?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )

    assert result["status"] == "ready"
    assert result["operation"] == "off_topic"
    assert result["tools"] == []
    assert result["problems"] == []


def test_direct_response_operation_rejects_tools():
    raw = {
        "complexity": "simple", "operation": "gratitude",
        "tools": [{
            "skill": "vector_search", "query": "спасибо", "doc_id": None,
            "point_ref": None, "expansions": [], "after": [],
        }],
        "final": None, "clarification": None,
    }

    result = route_question(
        "Спасибо!", lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )

    assert "direct_response_has_tools" in result["problems"]


def test_explicit_final_is_not_a_skill_call():
    raw = {
        "complexity": "complex",
        "operation": "compare",
        "tools": [
            {"skill": "definition_search", "query": "оперативный персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
            {"skill": "definition_search", "query": "диспетчерский персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []},
        ],
        "final": {"instruction": "сравнить определения оперативного и диспетчерского персонала", "after": [1, 2]},
        "clarification": None,
    }
    result = route_question(
        "Чем отличается оперативный персонал от диспетчерского?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["problems"] == []
    assert result["status"] == "ready"
    assert len(result["tools"]) == 2
    assert all(tool["skill"] == "definition_search" for tool in result["tools"])
    assert result["final"]["instruction"] == "сравнить определения оперативного и диспетчерского персонала"
    assert result["final"]["after"] == [1, 2]
    assert "skill" not in result["final"]


def test_comparison_without_retrieval_is_not_run_as_a_skill():
    raw = {
        "complexity": "complex",
        "operation": "compare",
        "tools": [{"skill": "final", "query": "сравнить персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": []}],
        "clarification": None,
    }
    result = route_question(
        "Чем отличается оперативный персонал от диспетчерского?",
        lambda _system, _user: json.dumps(raw, ensure_ascii=False),
    )
    assert result["tools"] == []
    assert result["status"] == "deferred"
    assert result["final"]["instruction"] == "сравнить персонал"
    assert result["final"]["after"] == []


def test_a_step_cannot_depend_on_itself_or_a_later_step():
    raw = {
        "complexity": "complex",
        "operation": "compare",
        "tools": [
            {"skill": "definition_search", "query": "оперативный персонал", "doc_id": None, "point_ref": None, "expansions": [], "after": [2]},
        ],
        "clarification": None,
    }
    result = route_question("Чем отличается оперативный персонал от диспетчерского?", lambda _system, _user: json.dumps(raw, ensure_ascii=False))
    assert result["tools"][0]["after"] == []
    assert "bad_after" in result["problems"]


def test_fifty_grok_routes_match_the_expected_shape():
    questions = json.loads((ROOT / "route_questions_50.json").read_text(encoding="utf-8"))
    answers = {item["id"]: item["answer"] for item in json.loads((ROOT / "model_route_answers.json").read_text(encoding="utf-8"))}
    documents = load_documents()
    table = load_abbreviations()
    failures = []
    for item in questions:
        raw = answers[item["id"]]
        checked = route_question(item["query"], lambda _system, _user, payload=raw: json.dumps(payload, ensure_ascii=False), abbreviations=table, documents=documents)
        reasons = judge(item["expect"], checked)
        if reasons:
            failures.append({"id": item["id"], "query": item["query"], "reasons": reasons, "operation": checked["operation"], "tools": checked["tools"], "clarification": checked["clarification"]})
    assert failures == []
    assert len(questions) == 50
