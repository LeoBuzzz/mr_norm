import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "sandbox")]

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.tools.chunker import stable_id
from question_parser.model_route import load_documents
from question_parser.router2_runtime import (
    _parse_final_answer_json,
    _request_final_answer_payload,
    corpus_doc_id,
    document_filters,
    run_router2,
)
from question_parser.router2_runtime import QdrantSkills


class FakeSkills:
    def __init__(self):
        self.calls = []

    def run(self, step):
        self.calls.append(step["skill"])
        return [RetrievedItem(
            chunk_id="c1", doc_id="doc-1", doc_name="ГОСТ", point_number="2",
            text="Найденный нормативный текст.", source_tool=step["skill"],
        )], []


def test_document_id_and_filename_match_corpus_contract():
    documents = load_documents()
    filters = document_filters("gost_19431_24102023", documents)
    assert filters["doc_id"] == stable_id("doc", "gost_19431_24102023")
    assert corpus_doc_id("gost_19431_24102023") == filters["doc_id"]
    assert filters["filename"].endswith(".txt")


def test_live_route_runs_sandbox_skills_and_main_final_answer():
    raw = {
        "complexity": "simple", "operation": "search",
        "tools": [
            {"skill": "vector_search", "query": "заземление", "doc_id": None, "after": []},
            {"skill": "corpus_point_search", "query": "заземление", "doc_id": None, "after": []},
        ],
        "final": None, "clarification": None,
    }
    skills = FakeSkills()
    seen = []

    def answer_provider(request, evidence, _pack):
        seen.append((request.query, [item.chunk_id for item in evidence]))
        return {"answer": "Ответ по пункту 2.", "citations": [{"chunk_id": "c1"}]}

    result = run_router2(
        "Каковы требования к заземлению?", route_complete=lambda _s, _u: json.dumps(raw),
        skill_backend=skills, answer_provider=answer_provider,
    )
    assert result.status == "answered"
    assert skills.calls == ["vector_search", "corpus_point_search"]
    assert seen[0][1] == ["c1"]
    assert result.answer == "Ответ по пункту 2."
    assert result.citations[0]["point_number"] == "2"


def test_router_contract_problem_blocks_search():
    raw = {
        "complexity": "simple", "operation": "search",
        "tools": [{"skill": "corpus_point_search", "query": "заземление", "doc_id": None}],
        "final": None, "clarification": None,
    }
    skills = FakeSkills()
    with pytest.raises(ValueError, match="missing_vector_search"):
        run_router2("заземление", route_complete=lambda _s, _u: json.dumps(raw), skill_backend=skills)
    assert skills.calls == []


def test_exact_point_limits_pue_chapter_by_filename(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    expected = document_filters("pue_1_2", documents)
    captured = []

    def fake_point_tool(request, _config):
        captured.append(request.filters)
        return SimpleNamespace(items=[RetrievedItem(
            chunk_id="part-1", doc_id=expected["doc_id"],
            filename=expected["filename"], doc_name="ПУЭ, глава 1.2",
            point_number="1.2.18", text="Текст пункта.",
        )], warnings=[])

    monkeypatch.setattr(runtime, "run_point_tool", fake_point_tool)
    backend = QdrantSkills(None, documents)
    items, warnings = backend.run({"skill": "exact_point", "doc_id": "pue_1_2", "point_ref": "1.2.18"})
    assert captured[0]["doc_id"] == expected["doc_id"]
    assert captured[0]["filename"] == expected["filename"]
    assert captured[0]["point_number"] == "1.2.18"
    assert items[0].text == "Текст пункта."
    assert warnings == []


def test_final_answer_repairs_unescaped_source_quotes():
    class FakeClient:
        def __init__(self):
            self.requests = []

        def chat(self, request):
            self.requests.append(request)
            return SimpleNamespace(content='{"answer":"текст "Не готов"", "citations":[]}')

    client = FakeClient()
    payload = _request_final_answer_payload(
        client,
        request=SimpleNamespace(query="Дай текст пункта 6"),
        items=[RetrievedItem(chunk_id="c1", text="текст")],
        pack={"prompt": "Ответь", "output_contract": {}},
        answer_model="test-model",
    )

    assert payload["answer"] == 'текст "Не готов"'
    assert len(client.requests) == 1


def test_final_answer_retries_unrecoverable_json():
    class FakeClient:
        def __init__(self):
            self.requests = []

        def chat(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return SimpleNamespace(content="not json")
            return SimpleNamespace(content='{"answer":"готово", "citations":[]}')

    client = FakeClient()
    payload = _request_final_answer_payload(
        client,
        request=SimpleNamespace(query="Вопрос"),
        items=[RetrievedItem(chunk_id="c1", text="текст")],
        pack={"prompt": "Ответь", "output_contract": {}},
        answer_model="test-model",
    )

    assert payload["answer"] == "готово"
    assert len(client.requests) == 2
    assert "экранируй двойные кавычки" in client.requests[1].messages[-1]["content"]


def test_final_answer_repair_keeps_escaped_newlines_and_citations():
    payload = _parse_final_answer_json(
        '{"answer":"строка 1\\nстрока "Не готов"",'
        '"citations":[{"chunk_id":"chunk_1"}]}'
    )

    assert payload == {
        "answer": 'строка 1\nстрока "Не готов"',
        "citations": [{"chunk_id": "chunk_1"}],
    }
