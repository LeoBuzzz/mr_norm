import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

from question_parser.definitions import definition_search, load_definitions
from question_parser.model_route import load_documents, route_question

ROOT = Path(__file__).resolve().parents[1]


def test_every_definition_carries_the_file_document_id():
    by_filename = {item["filename"]: item["id"] for item in load_documents()["documents"]}
    cards = load_definitions()
    assert cards
    for card in cards:
        for item in card["definitions"]:
            assert item["doc_id"] == by_filename[item["file"]]


def test_operational_staff_definition_is_the_glossary_entry():
    result = definition_search(query="оперативного персонала")
    assert result["status"] == "ok"
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["doc_id"] == "gostr_57114_29122022"
    assert hit["point_ref"] == "107"
    assert hit["term"] == "оперативный персонал"
    assert hit["text"].startswith("Работники субъектов электроэнергетики")


def test_grid_facility_returns_every_definition():
    result = definition_search(query="объекта электросетевого хозяйства")
    assert result["status"] == "ok"
    assert result["text"] is None
    ids = [hit["doc_id"] for hit in result["hits"]]
    assert ids == ["z_250_04112007", "z_35_26032003"]
    assert {hit["point_ref"] for hit in result["hits"]} == {"статья 3"}
    assert len({hit["text"] for hit in result["hits"]}) == 1
    assert result["hits"][0]["text"].startswith("линии электропередачи")


def test_document_filter_drops_a_definition_from_another_file():
    result = definition_search(query="оперативного персонала", doc_id="gost_19431_24102023")
    assert result["status"] == "not_found"
    assert result["hits"] == []


def test_define_route_calls_the_definition_skill():
    raw = {
        "complexity": "complex",
        "operation": "define",
        "tools": [{"skill": None, "query": "оперативный персонал", "doc_id": None, "point_ref": None, "expansions": []}],
        "clarification": None,
    }
    result = route_question("Дай определение оперативного персонала", lambda _system, _user: json.dumps(raw, ensure_ascii=False))
    assert result["problems"] == []
    assert result["status"] == "ready"
    assert result["operation"] == "define"
    assert result["tools"][0]["skill"] == "definition_search"
    assert result["final"] is None
    assert result["tools"][0]["query"] == "оперативный персонал"
    assert result["tools"][0]["doc_id"] is None
    assert result["tools"][0]["point_ref"] is None
