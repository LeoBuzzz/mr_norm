import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

from question_parser.catalog import load_catalog
from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.run import dry_run


def test_point_in_pue_asks_which_document_and_does_not_guess():
    result = dry_run("пункт 1.2.18 ПУЭ")
    assert isinstance(result, SkillQueryPlan)
    assert result.complexity == "complex"
    assert result.status == "needs_clarification"
    assert result.clarification["field"] == "document"
    assert result.tasks[0]["doc_id"] is None
    assert result.tasks[0]["point_ref"] == "1.2.18"
    assert result.tasks[0]["skill"] == "exact_point"
    labels = " ".join(item["label"] for item in result.clarification["options"])
    assert "ПУЭ" in labels
    assert len(result.clarification["options"]) > 1


def test_unique_gost_point_is_ready():
    result = dry_run("пункт 2 ГОСТ 19431")
    assert isinstance(result, SkillQueryPlan)
    assert result.status == "ready"
    assert result.tasks[0]["skill"] == "exact_point"
    assert result.tasks[0]["point_ref"] == "2"
    assert "19431" in result.tasks[0]["document_mention"]


def test_definition_request_is_deferred_not_a_search():
    result = dry_run("что такое электроэнергетика")
    assert isinstance(result, SkillQueryPlan)
    assert result.status == "deferred"
    assert result.tasks[0]["skill"] is None
    assert any(issue["code"] == "unsupported_intent" for issue in result.issues)
    assert result.tasks[0]["concepts"][0]["surface"].lower().startswith("электроэнергетик")


def test_one_alias_is_not_two_meanings():
    result = dry_run("все упоминания ЛЭП, то есть линия электропередачи, кроме кабельных")
    assert isinstance(result, SkillQueryPlan)
    assert result.status == "ready"
    assert result.tasks[0]["skill"] == "find_all"
    assert len(result.tasks[0]["concepts"]) == 1
    variants = " ".join(result.tasks[0]["concepts"][0]["variants"]).lower()
    assert "лэп" in variants
    assert any(item["kind"] == "exclusion" and item["polarity"] == "exclude" for item in result.tasks[0]["constraints"])


def test_locator_in_topic_is_a_technical_error():
    draft = {
        "dialog": "new",
        "tasks": [
            {
                "operation": "search",
                "source": ["current@1:1"],
                "topic": "тема current@1:1",
                "document": "corpus",
            }
        ],
    }
    result = dry_run("заземление", draft=draft)
    assert isinstance(result, TechnicalFailure)
    assert result.code == "TOPIC_MUST_BE_LITERAL_TEXT"


def test_quantity_is_not_accepted_as_a_point_number():
    draft = {
        "dialog": "new",
        "tasks": [
            {
                "operation": "text",
                "source": ["current@1:1"],
                "point": "current@2:3",
                "document": "unresolved",
            }
        ],
    }
    result = dry_run("напряжение 110 кВ", draft=draft)
    assert isinstance(result, TechnicalFailure)
    assert "measured quantity" in result.message


def test_dependency_is_deferred_on_stage_1():
    draft = {
        "dialog": "new",
        "tasks": [
            {"operation": "search", "source": ["current@1:1"], "topic": "заземление", "document": "corpus"},
            {"operation": "search", "source": ["current@1:1"], "topic": "сеть", "document": "corpus", "after": [1]},
        ],
    }
    result = dry_run("заземление сети", draft=draft)
    assert isinstance(result, SkillQueryPlan)
    assert result.status == "deferred"
    assert any(issue["code"] == "dependency_execution_not_in_stage1" for issue in result.issues)


def test_catalog_covers_definition_files():
    catalog = load_catalog()
    assert len(catalog.documents) >= 30
    assert len(catalog.meanings) >= 600
