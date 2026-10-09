import json
from pathlib import Path

from router import route_query


def test_fixture_gold_is_not_encoding_corrupted():
    fixtures = json.loads((Path(__file__).with_name("fixtures.json")).read_text(encoding="utf-8"))["fixtures"]
    for fixture in fixtures:
        assert "???" not in fixture["question"]
        assert "\ufffd" not in fixture["question"]
        hint = fixture["expected_doc_hint"]
        assert hint is None or ("???" not in hint and "\ufffd" not in hint)
        assert all("???" not in value and "\ufffd" not in value for value in fixture["expected_negative_scope"])


def test_fixture_ids_and_questions_are_unique_across_splits():
    fixtures = json.loads((Path(__file__).with_name("fixtures.json")).read_text(encoding="utf-8"))["fixtures"]
    ids = [fixture["id"] for fixture in fixtures]
    assert len(ids) == len(set(ids))
    normalized = [" ".join(fixture["question"].casefold().split()) for fixture in fixtures]
    assert len(normalized) == len(set(normalized))
    tuning = {q for q, fixture in zip(normalized, fixtures) if fixture["split"] == "tuning"}
    holdout = {q for q, fixture in zip(normalized, fixtures) if fixture["split"] == "holdout"}
    assert tuning.isdisjoint(holdout)


def test_exact_point_extracts_document_and_point():
    result = route_query("Как звучит пункт 6.25 из правил по охране труда")
    assert result.primary == "exact_point"
    assert result.point_ref == "6.25"
    assert result.explicit_doc_hint == "Правила по охране труда"


def test_followup_preserves_context_and_point():
    result = route_query("А пункт 2.1?", dialog_context="Как звучит пункт 6.25 из правил по охране труда")
    assert result.followup
    assert result.primary == "exact_point"
    assert result.point_ref == "2.1"
    assert result.explicit_doc_hint == "Правила по охране труда"


def test_all_mentions_requires_a_document_and_broad_scope_uses_point_search():
    within = route_query("Вытащи все про наведенное напряжение из правил по охране труда")
    across = route_query("В каких документах указаны параметры надежности и качества?")
    assert within.routes == ("all_mentions",)
    assert across.routes == ("corpus_point_search",)


def test_applicability_and_negative_scope_are_kept():
    result = route_query("Посмотри в ПУЭ. Какие требования к помещениям на подстанции (не электростанции) существуют")
    assert result.primary == "applicability"
    assert "электростанции" in result.negative_constraints
    assert result.explicit_doc_hint == "ПУЭ"


def test_correction_can_multi_route_and_flag_uncertainty():
    result = route_query(
        "Добавь к постановлению 861, где должны разработать информационную модель?",
        dialog_context="Какие требования к информационной модели существуют?",
    )
    assert result.routes == ("applicability",)
    assert result.explicit_doc_hint == "Постановление Правительства РФ № 861"
    assert result.followup


def test_ambiguous_short_document_mention_is_high_uncertainty():
    result = route_query("Какие условия прописаны в пункте 33 постановления 86?")
    assert result.uncertainty == "high"
    assert "требуется уточнение" in result.explicit_doc_hint
    assert "861" not in result.explicit_doc_hint
    assert result.point_ref == "33"


def test_cross_document_question_does_not_invoke_applicability_without_document():
    result = route_query("В каких документах определено, кто должен устанавливать резервный источник?")
    assert result.routes == ("corpus_point_search",)
    assert result.uncertainty == "high"


def test_curated_fixture_primary_routes_match_gold():
    fixtures = json.loads((Path(__file__).with_name("fixtures.json")).read_text(encoding="utf-8"))["fixtures"]
    for fixture in fixtures:
        result = route_query(fixture["question"], dialog_context=fixture.get("context_question"))
        expected = {"corpus_point_search" if route == "deferred_broad_search" else route
                    for route in fixture["expected_types"]}
        assert result.primary in expected, fixture["id"]


def test_tuning_extraction_fields_match_gold():
    fixtures = json.loads((Path(__file__).with_name("fixtures.json")).read_text(encoding="utf-8"))["fixtures"]
    for fixture in fixtures:
        if fixture["split"] != "tuning":
            continue
        result = route_query(fixture["question"], dialog_context=fixture.get("context_question"))
        expected_negative = {item.removeprefix("не ") for item in fixture["expected_negative_scope"]}
        expected = {"corpus_point_search" if route == "deferred_broad_search" else route
                    for route in fixture["expected_types"]}
        assert expected.issubset(result.routes), fixture["id"]
        assert result.point_ref == fixture["expected_point"], fixture["id"]
        assert result.explicit_doc_hint == fixture["expected_doc_hint"], fixture["id"]
        assert set(result.negative_constraints) == expected_negative, fixture["id"]


def test_explicit_all_mentions_without_a_document_uses_point_search():
    result = route_query("Расскажи всё о работе с оперативным персоналом")
    assert result.primary == "all_mentions"
    assert result.uncertainty == "low"


def test_point_number_before_point_word_is_extracted():
    result = route_query("Дай текст 118 пункта правил переключений")
    assert result.primary == "exact_point"
    assert result.point_ref == "118"
    assert result.explicit_doc_hint == "Правила переключений"
