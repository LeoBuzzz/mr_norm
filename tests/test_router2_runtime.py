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
    expanded_query_variants,
    lexical_fallback_variants,
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


def test_search_hit_is_hydrated_by_exact_point_identity(monkeypatch):
    import question_parser.router2_runtime as runtime

    captured = []
    identity = "48::Раздел::100:20"

    def fake_point_tool(request, _config):
        captured.append(request.filters)
        return SimpleNamespace(items=[
            RetrievedItem(
                chunk_id="part-1", doc_id="doc-1", doc_name="Правила",
                point_number="48", point_identity_key=identity,
                text="48. Начало ", part_index=0, total_parts=2,
                is_split=True, is_complete_point=False,
            ),
            RetrievedItem(
                chunk_id="part-2", doc_id="doc-1", doc_name="Правила",
                point_number="48", point_identity_key=identity,
                text="и продолжение.", part_index=1, total_parts=2,
                is_split=True, is_complete_point=False,
            ),
        ], warnings=[])

    monkeypatch.setattr(runtime, "run_point_tool", fake_point_tool)
    backend = QdrantSkills(None, load_documents())
    items, warnings = backend.hydrate_full_points([RetrievedItem(
        chunk_id="search-hit", doc_id="doc-1", doc_name="Правила",
        point_number="48", point_identity_key=identity,
        text="обрезанный фрагмент", score=0.91, source_tool="vector",
        part_index=1, total_parts=2, is_split=True, is_complete_point=False,
    )])

    assert captured == [{"point_identity_key": identity}]
    assert items[0].text == "48. Начало и продолжение."
    assert items[0].score == 0.91
    assert items[0].source_tool == "vector"
    assert items[0].is_complete_point is True
    assert warnings == []


def test_expansion_searches_full_value_and_abbreviation(monkeypatch):
    import question_parser.router2_runtime as runtime

    step = {
        "skill": "find_all", "query": "обходная система шин",
        "doc_id": "gostr_59279_24122020",
        "expansions": [{"abbreviation": "ОСШ", "value": "обходная система шин"}],
    }
    queries = []

    def fake_payload_tool(request, _config, *, search_fields):
        queries.append(request.query)
        common = RetrievedItem(
            chunk_id="definition", point_identity_key="3::terms", point_number="3",
            text="ОСШ - обходная система шин",
        )
        if request.query == "ОСШ":
            return SimpleNamespace(items=[
                common,
                RetrievedItem(
                    chunk_id="usage", point_identity_key="4.8::usage",
                    point_number="4.8", text="Схема с ОСШ.",
                ),
            ], warnings=[])
        return SimpleNamespace(items=[common], warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    backend = QdrantSkills(None, load_documents())
    items, _warnings = backend.run(step)

    assert expanded_query_variants(step) == ["обходная система шин", "ОСШ"]
    assert queries == ["обходная система шин", "ОСШ"]
    assert [item.chunk_id for item in items] == ["definition", "usage"]


def test_find_all_keeps_more_than_default_evidence_limit():
    raw = {
        "complexity": "simple", "operation": "all",
        "tools": [{
            "skill": "find_all", "query": "ОСШ", "doc_id": None,
            "expansions": [], "after": [],
        }],
        "final": None, "clarification": None,
    }

    class ManySkills:
        def run(self, _step):
            return [RetrievedItem(
                chunk_id=f"c{index}", point_identity_key=f"point-{index}",
                point_number=str(index), text=f"Пункт {index}",
            ) for index in range(20)], []

    seen = []

    def answer_provider(_request, evidence, _pack):
        seen.append(len(evidence))
        return {"answer": "Все пункты", "citations": [{"chunk_id": "c0"}]}

    result = run_router2(
        "Все упоминания ОСШ", route_complete=lambda _s, _u: json.dumps(raw),
        skill_backend=ManySkills(), answer_provider=answer_provider,
    )

    assert result.status == "answered"
    assert len(result.evidence) == 20
    assert seen == [20]


def test_find_all_uses_user_question_as_semantic_fallback(monkeypatch):
    import question_parser.router2_runtime as runtime

    vector_queries = []

    def fake_payload_tool(_request, _config, *, search_fields):
        return SimpleNamespace(items=[], warnings=[])

    def fake_vector_tool(request, _config):
        vector_queries.append(request.query)
        return SimpleNamespace(items=[RetrievedItem(
            chunk_id="contract-13", point_identity_key="13::contract",
            point_number="13", text="Договор должен содержать...",
        )], warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    monkeypatch.setattr(runtime, "run_vector_tool", fake_vector_tool)
    backend = QdrantSkills(None, load_documents())
    items, warnings = backend.run({
        "skill": "find_all", "query": "договор на передачу электроэнергии",
        "doc_id": None, "expansions": [],
        "_user_question": "что указывается в договоре на передачу электроэнергии. назови всё.",
    })

    assert vector_queries == ["что указывается в договоре на передачу электроэнергии. назови всё."]
    assert [item.chunk_id for item in items] == ["contract-13"]
    assert "find_all text search returned no hits; used semantic fallback" in warnings


def test_lexical_fallback_preserves_inflected_phrase_from_question():
    variants = lexical_fallback_variants({
        "query": "простая наглядная схема электроустановки особенности",
        "_user_question": (
            "в правилах по охране труда - какие особенности электроустановок "
            "с простой наглядной схемой?"
        ),
    })

    assert "простой наглядной схемой" in variants


def test_empty_corpus_search_retries_original_inflected_phrase(monkeypatch):
    import question_parser.router2_runtime as runtime

    queries = []

    def fake_payload_tool(request, _config, *, search_fields):
        queries.append(request.query)
        items = []
        if request.query == "простой наглядной схемой":
            items = [RetrievedItem(chunk_id="p57", point_number="5.7", text="Нужный пункт")]
        return SimpleNamespace(items=items, warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    backend = QdrantSkills(None, load_documents())
    items, warnings = backend.run({
        "skill": "corpus_point_search",
        "query": "простая наглядная схема электроустановки особенности",
        "doc_id": None,
        "_user_question": (
            "в правилах по охране труда - какие особенности электроустановок "
            "с простой наглядной схемой?"
        ),
    })

    assert "простой наглядной схемой" in queries
    assert [item.chunk_id for item in items] == ["p57"]
    assert "corpus text search returned no hits; used original phrase fallback" in warnings


def test_empty_definition_search_uses_corpus_and_vector_fallback(monkeypatch):
    import question_parser.router2_runtime as runtime

    payload_queries = []
    vector_queries = []

    def fake_definition_search(**_kwargs):
        return {"hits": []}

    def fake_payload_tool(request, _config, *, search_fields):
        payload_queries.append(request.query)
        return SimpleNamespace(items=[RetrievedItem(
            chunk_id="scheme-table", text="Эксплуатационный критерий: простая и наглядная.",
        )], warnings=[])

    def fake_vector_tool(request, _config):
        vector_queries.append(request.query)
        return SimpleNamespace(items=[], warnings=[])

    monkeypatch.setattr(runtime, "definition_search", fake_definition_search)
    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    monkeypatch.setattr(runtime, "run_vector_tool", fake_vector_tool)
    backend = QdrantSkills(None, load_documents())
    items, warnings = backend.run({
        "skill": "definition_search", "query": "простая наглядная схема",
        "doc_id": None, "_user_question": "Что такое простая наглядная схема?",
    })

    assert payload_queries[0] == "простая наглядная схема"
    assert vector_queries == ["Что такое простая наглядная схема?"]
    assert [item.chunk_id for item in items] == ["scheme-table"]
    assert "definition catalog returned no hits; used corpus fallback" in warnings


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


def test_router_switches_to_fallback_model(monkeypatch):
    import question_parser.router2_runtime as runtime

    raw = {
        "complexity": "simple", "operation": "search",
        "tools": [
            {"skill": "vector_search", "query": "заземление", "doc_id": None, "after": []},
            {"skill": "corpus_point_search", "query": "заземление", "doc_id": None, "after": []},
        ],
        "final": None, "clarification": None,
    }
    built = []

    class FakeClient:
        def __init__(self, model):
            self.model = model

        def chat(self, _request):
            if self.model == "router-primary":
                raise RuntimeError("temporarily unavailable")
            return SimpleNamespace(content=json.dumps(raw))

    def fake_build(_provider, model):
        built.append(model)
        return FakeClient(model)

    monkeypatch.setattr(runtime, "build_chat_client", fake_build)
    result = run_router2(
        "заземление", router_model="router-primary",
        router_fallback_models=("router-backup",), skill_backend=FakeSkills(),
        answer_provider=lambda _request, _items, _pack: {
            "answer": "Ответ", "citations": [{"chunk_id": "c1"}],
        },
    )

    assert built == ["router-primary", "router-backup"]
    assert result.router_model_used == "router-backup"
    assert result.model_failures[0].startswith("router:router-primary:RuntimeError")
    assert "router model fallback: router-primary -> router-backup" in result.warnings


def test_final_answer_switches_to_fallback_model(monkeypatch):
    import question_parser.router2_runtime as runtime

    raw = {
        "complexity": "simple", "operation": "define",
        "tools": [{"skill": "definition_search", "query": "термин", "doc_id": None, "after": []}],
        "final": None, "clarification": None,
    }

    class FakeClient:
        def __init__(self, model):
            self.model = model

        def chat(self, _request):
            if self.model == "answer-primary":
                raise RuntimeError("temporarily unavailable")
            return SimpleNamespace(content='{"answer":"Резервный ответ", "citations":[{"chunk_id":"c1"}]}')

    monkeypatch.setattr(runtime, "build_chat_client", lambda _provider, model: FakeClient(model))
    result = run_router2(
        "Что такое термин?", route_complete=lambda _s, _u: json.dumps(raw),
        skill_backend=FakeSkills(), answer_model="answer-primary",
        answer_fallback_models=("answer-backup",),
    )

    assert result.status == "answered"
    assert result.answer == "Резервный ответ"
    assert result.answer_model_used == "answer-backup"
    assert result.model_failures[0].startswith("answer:answer-primary:RuntimeError")
    assert "answer model fallback: answer-primary -> answer-backup" in result.warnings
