from __future__ import annotations

from mr_norm.retrieval.intent_boost import rerank_items_for_intent
from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.query_intent import detect_query_intent, intent_search_terms


def test_detect_document_lookup_intent() -> None:
    assert detect_query_intent("Какой документ устанавливает общие правила?") == "document_lookup"
    assert detect_query_intent("требования пункта 1.7.1") == "point_lookup"


def test_detect_frequency_intent() -> None:
    assert detect_query_intent("как часто надо осматривать ВЛ") == "frequency"
    assert detect_query_intent("как часто надо осматривать воздушные линии") == "frequency"


def test_intent_search_terms_for_energy_law() -> None:
    terms = intent_search_terms(
        "Какой документ устанавливает общие правила работы электроэнергетической системы РФ?",
        "document_lookup",
    )
    assert any("электроэнергетик" in term for term in terms)
    assert any("35-фз" in term for term in terms)


def test_intent_search_terms_for_regulation_scope() -> None:
    terms = intent_search_terms(
        "Что регулирует Правила технической эксплуатации электрических станций и сетей?",
        "regulation_scope",
    )
    assert any("технической эксплуатации" in term for term in terms)


def test_intent_boost_prefers_legal_act_over_gost() -> None:
    items = [
        RetrievedItem(
            chunk_id="gost",
            doc_name="ГОСТ Р 57114-2022",
            text="релейная защита",
            score=0.9,
            source_tool="vector",
        ),
        RetrievedItem(
            chunk_id="law",
            doc_name="Федеральный закон от 26.03.2003 №35-ФЗ",
            text="Об электроэнергетике",
            score=0.7,
            source_tool="vector",
        ),
    ]
    ranked = rerank_items_for_intent(
        items,
        "Какой документ устанавливает общие правила работы электроэнергетической системы РФ?",
        limit=2,
    )
    assert ranked[0].chunk_id == "law"


def test_frequency_intent_prefers_periodicity_over_worker_requirements() -> None:
    items = [
        RetrievedItem(
            chunk_id="workers",
            doc_name="Правила по охране труда",
            point_number="38.74",
            text="осмотр ВЛ должны выполнять не менее двух работников, имеющих группу II; при обходах должна быть связь с диспетчером",
            score=0.95,
        ),
        RetrievedItem(
            chunk_id="frequency",
            doc_name="Правила технической эксплуатации",
            point_number="549",
            text="При эксплуатации ВЛ должны быть организованы их периодические и внеочередные осмотры",
            score=0.40,
        ),
    ]

    ranked = rerank_items_for_intent(
        items,
        "как часто надо осматривать ВЛ",
        limit=2,
    )

    assert [item.chunk_id for item in ranked] == ["frequency", "workers"]
