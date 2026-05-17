from __future__ import annotations

from mr_norm.retrieval.text_normalize import morphology_phrase_matches_query, russian_term_stem


def test_russian_term_stem_strips_common_endings() -> None:
    assert russian_term_stem("наведенное") == russian_term_stem("наведенным")
    assert russian_term_stem("напряжение") == russian_term_stem("напряжением")


def test_morphology_requires_multiple_stems() -> None:
    assert morphology_phrase_matches_query("наведенное напряжение", "наведенным напряжением")
    assert not morphology_phrase_matches_query("наведенное напряжение", "напряжением")
