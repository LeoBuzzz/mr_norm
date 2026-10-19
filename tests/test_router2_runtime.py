import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "sandbox")]

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.config.indexing import IndexingConfig
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


def test_document_filters_use_registry_key_only_for_rtf_v2_collections():
    documents = load_documents()
    filters = document_filters(
        "pr_630_03082018",
        documents,
        collection_name="mr_norm_docs_bge_m3_rtf_v2_20261011",
    )

    assert filters == {"doc_id": "pr_630_03082018"}


def test_qdrant_skills_uses_rtf_v2_document_filter(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    captured = []

    def fake_payload_tool(request, _config, *, search_fields):
        captured.append(dict(request.filters))
        return SimpleNamespace(items=[], warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    backend = QdrantSkills(
        IndexingConfig(collection_name="mr_norm_docs_bge_m3_rtf_v2_20261011"),
        documents,
    )
    backend.run({
        "skill": "corpus_point_search",
        "query": "обычный запрос",
        "doc_id": "pr_630_03082018",
    })

    assert captured == [{"doc_id": "pr_630_03082018"}]


def test_qdrant_skills_v2_table_search_uses_exact_payload_label_filter(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    doc = next(item for item in documents["documents"] if item["id"] == "pr_630_03082018")
    row = RetrievedItem(
        chunk_id="pr_630_03082018:table:13:row:2",
        doc_id="pr_630_03082018",
        filename=doc["filename"],
        text="2.1 Отключение одной СШ — группы III | II | II",
    )
    requests = []

    def fake_payload_tool(request, _config, *, search_fields):
        if request.filters.get("kind") == "table_row":
            requests.append((request.query, dict(request.filters)))
            return SimpleNamespace(items=[row], warnings=[])
        return SimpleNamespace(items=[], warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    backend = QdrantSkills(
        IndexingConfig(collection_name="mr_norm_docs_bge_m3_rtf_v2_20261011"),
        documents,
    )
    items, warnings = backend.run({
        "skill": "corpus_point_search",
        "query": "содержание таблицы 2 приложения по устойчивости",
        "doc_id": doc["id"],
        "_user_question": "Дай содержание таблицы 2 приложения к методическим указаниям по устойчивости",
    })

    assert requests == [(
        "",
        {"doc_id": "pr_630_03082018", "kind": "table_row", "table_label": "2"},
    )]
    assert [item.chunk_id for item in items] == [row.chunk_id]
    assert items[0].matched["structural_target"] == "таблица 2"
    assert warnings == []


def test_qdrant_skills_exact_point_uses_rtf_v2_registry_doc_id(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    doc = next(item for item in documents["documents"] if item["id"] == "pr_630_03082018")
    captured = []

    def fake_point_tool(request, _config):
        captured.append(dict(request.filters))
        return SimpleNamespace(items=[RetrievedItem(
            chunk_id="point-6", doc_id="pr_630_03082018", point_number="6",
            text="6. Текст пункта.",
        )], warnings=[])

    monkeypatch.setattr(runtime, "run_point_tool", fake_point_tool)
    backend = QdrantSkills(
        IndexingConfig(collection_name="mr_norm_docs_bge_m3_rtf_v2_20261011"),
        documents,
    )
    items, warnings = backend.run({
        "skill": "exact_point", "doc_id": doc["id"], "point_ref": "6",
    })

    assert captured == [{"doc_id": "pr_630_03082018", "point_number": "6"}]
    assert items[0].text == "6. Текст пункта."
    assert warnings == []


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


@pytest.mark.parametrize(
    "question",
    ["Спасибо!", "Большое спасибо 😊", "Благодарю за помощь.", "Спс!"],
)
def test_gratitude_answers_without_router_search_or_final_model(question):
    class NoSkills:
        def run(self, _step):
            raise AssertionError("gratitude must not search")

    def no_router(_system, _user):
        raise AssertionError("gratitude must not call router model")

    def no_answer(_request, _items, _pack):
        raise AssertionError("gratitude must not call final model")

    result = run_router2(
        question, route_complete=no_router, skill_backend=NoSkills(),
        answer_provider=no_answer,
    )

    assert result.status == "answered"
    assert result.plan["operation"] == "gratitude"
    assert result.calls == []
    assert result.citations == []
    assert any(emoji in result.answer for emoji in "😊🙌📚🙂⚡")


def test_gratitude_with_a_new_question_is_not_intercepted():
    raw = {
        "complexity": "simple", "operation": "search",
        "tools": [
            {"skill": "vector_search", "query": "заземление", "doc_id": None, "after": []},
            {"skill": "corpus_point_search", "query": "требования к заземлению", "doc_id": None, "after": []},
        ],
        "final": None, "clarification": None,
    }
    skills = FakeSkills()

    result = run_router2(
        "Спасибо! А какие требования к заземлению?",
        route_complete=lambda _s, _u: json.dumps(raw), skill_backend=skills,
        answer_provider=lambda _request, _items, _pack: {
            "answer": "Ответ", "citations": [{"chunk_id": "c1"}],
        },
    )

    assert result.plan["operation"] == "search"
    assert skills.calls == ["vector_search", "corpus_point_search"]


def test_off_topic_answers_without_search_or_final_model():
    raw = {
        "complexity": "simple", "operation": "off_topic", "tools": [],
        "final": None, "clarification": None,
    }

    class NoSkills:
        def run(self, _step):
            raise AssertionError("off-topic request must not search")

    def no_answer(_request, _items, _pack):
        raise AssertionError("off-topic request must not call final model")

    result = run_router2(
        "Какая сегодня погода?", route_complete=lambda _s, _u: json.dumps(raw),
        skill_backend=NoSkills(), answer_provider=no_answer,
    )

    assert result.status == "answered"
    assert result.plan["operation"] == "off_topic"
    assert result.calls == []
    assert "норматив" in result.answer.casefold() or "документ" in result.answer.casefold()
    assert any(emoji in result.answer for emoji in "📚⚡🙂📖🔎😊🧭📄")


def test_direct_answers_are_varied_but_stable():
    gratitude_answers = {
        run_router2(question, route_complete=lambda _s, _u: "").answer
        for question in [
            "Спасибо", "Большое спасибо", "Огромное спасибо", "Благодарю",
            "Благодарю за ответ", "Спасибо за помощь", "Мерси", "Спс",
        ]
    }

    first = run_router2("Спасибо", route_complete=lambda _s, _u: "").answer
    second = run_router2("Спасибо", route_complete=lambda _s, _u: "").answer

    assert len(gratitude_answers) >= 3
    assert first == second


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


def test_qdrant_skills_searches_structural_label_and_attaches_bounded_legacy_body(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    doc = next(item for item in documents["documents"] if item["id"] == "pr_630_03082018")
    expected_filters = document_filters(doc["id"], documents)
    doc_id = expected_filters["doc_id"]
    filename = expected_filters["filename"]
    header = RetrievedItem(
        chunk_id="legacy-table-header", doc_id=doc_id, filename=filename,
        doc_name=doc["title"], point_number="6", chunk_index=61,
        text="6. Контекст пункта. Таблица 2 — Показатель | Режим 1",
    )
    raw_body = RetrievedItem(
        chunk_id="legacy-table-body", doc_id=doc_id, filename=filename,
        doc_name=doc["title"], point_number="2", chunk_index=62,
        text="2.1 Отключение одной СШ — группы III | II | II. Таблица 3 — другой набор.",
    )
    noise = RetrievedItem(
        chunk_id="point-21", doc_id=doc_id, filename=filename,
        point_number="21", text="Пункт 21.",
    )
    queries = []
    neighbor_filters = []

    def fake_payload_tool(request, _config, *, search_fields):
        queries.append(request.query)
        if "chunk_index" in request.filters:
            neighbor_filters.append(dict(request.filters))
            items = [raw_body] if request.filters["chunk_index"] == 62 else []
        elif request.query == "таблица 2":
            items = [header]
        else:
            items = [noise]
        return SimpleNamespace(items=items, warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    question = "Дай содержание таблицы 2 приложения к методическим указаниям по устойчивости"
    backend = QdrantSkills(None, documents)
    items, warnings = backend.run({
        "skill": "corpus_point_search",
        "query": "содержание таблицы 2 приложения по устойчивости",
        "doc_id": doc["id"],
        "_user_question": question,
    })

    assert queries == ["таблица 2", "содержание таблицы 2 приложения по устойчивости", ""]
    assert neighbor_filters == [{**expected_filters, "chunk_index": 62}]
    assert [item.chunk_id for item in items] == [header.chunk_id, raw_body.chunk_id, noise.chunk_id]
    assert items[0].matched["structural_target"] == "таблица 2"
    assert items[1].matched["structural_parent_chunk_id"] == header.chunk_id
    assert "Отключение одной СШ" in items[1].text
    assert "Таблица 3" not in items[1].text
    assert warnings == []


def test_qdrant_skills_ordinary_corpus_search_does_not_add_structural_queries(monkeypatch):
    import question_parser.router2_runtime as runtime

    queries = []

    def fake_payload_tool(request, _config, *, search_fields):
        queries.append(request.query)
        return SimpleNamespace(items=[RetrievedItem(chunk_id="point-5", text="Пункт 5.")], warnings=[])

    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    backend = QdrantSkills(None, load_documents())
    items, warnings = backend.run({
        "skill": "corpus_point_search", "query": "заземление защитное",
        "doc_id": None, "_user_question": "Как выполнить защитное заземление?",
    })

    assert queries == ["заземление защитное"]
    assert [item.chunk_id for item in items] == ["point-5"]
    assert warnings == []


def test_qdrant_skills_vector_search_attaches_structural_neighbor(monkeypatch):
    import question_parser.router2_runtime as runtime

    documents = load_documents()
    doc = next(item for item in documents["documents"] if item["id"] == "pr_630_03082018")
    filters = document_filters(doc["id"], documents)
    header = RetrievedItem(
        chunk_id="vector-table-header", doc_id=filters["doc_id"],
        filename=filters["filename"], chunk_index=61,
        text="Таблица 2 — Показатель | Режим 1",
    )
    body = RetrievedItem(
        chunk_id="vector-table-body", doc_id=filters["doc_id"],
        filename=filters["filename"], chunk_index=62,
        text="2.1 Отключение одной СШ — группы III | II | II. Таблица 3 — следующий набор.",
    )

    def fake_vector_tool(request, _config):
        item = header if request.query == "таблица 2" else RetrievedItem(
            chunk_id="vector-noise", doc_id=filters["doc_id"], text="Пункт 21.",
        )
        return SimpleNamespace(items=[item], warnings=[])

    def fake_payload_tool(request, _config, *, search_fields):
        item = body if request.filters.get("chunk_index") == 62 else None
        return SimpleNamespace(items=[item] if item else [], warnings=[])

    monkeypatch.setattr(runtime, "run_vector_tool", fake_vector_tool)
    monkeypatch.setattr(runtime, "run_payload_tool", fake_payload_tool)
    question = "Дай содержание таблицы 2 приложения к методическим указаниям по устойчивости"
    items, warnings = QdrantSkills(None, documents).run({
        "skill": "vector_search",
        "query": "содержание таблицы 2 приложения по устойчивости",
        "doc_id": doc["id"],
        "_user_question": question,
    })

    assert [item.chunk_id for item in items[:2]] == [header.chunk_id, body.chunk_id]
    assert items[1].matched["structural_parent_chunk_id"] == header.chunk_id
    assert "Таблица 3" not in items[1].text
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

    def fake_build(_provider, model, **kwargs):
        built.append((model, kwargs.get("keys_path")))
        return FakeClient(model)

    monkeypatch.setattr(runtime, "build_chat_client", fake_build)
    result = run_router2(
        "заземление", router_model="router-primary",
        router_fallback_models=("router-backup",), skill_backend=FakeSkills(),
        keys_path=Path("custom-keys"),
        answer_provider=lambda _request, _items, _pack: {
            "answer": "Ответ", "citations": [{"chunk_id": "c1"}],
        },
    )

    assert built == [
        ("router-primary", Path("custom-keys")),
        ("router-backup", Path("custom-keys")),
    ]
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
