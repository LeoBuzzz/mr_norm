from __future__ import annotations

import pytest

from mr_norm.config.paths import ProjectPaths
from mr_norm.skills.document_resolve import resolve_document_from_label


LIVE_ACCEPTANCE_CASES = (
    ("правила переключений минэнерго", "переключ"),
    ("инструкция по ликвидации аварий", "ликвидации нарушений"),
    ("правила оптового рынка", "оптового рынка"),
    ("правила розничного рынка", "розничн"),
    ("861 постановление", "недискриминацион"),
    ("Правила ликвидации", "ликвидации нарушений"),
    ("Правила ликвидации аварий", "ликвидации нарушений"),
    ("правила тренировок", "трениров"),
    ("правила тренировок минэнерго", "трениров"),
    ("об информации для диспетчерского управления", "предоставления информации"),
    ("фз 35", "электроэнергетике"),
    ("35-фз", "электроэнергетике"),
    ("ппрф 1340", "предоставления информации"),
    ("442 ППРФ", "розничн"),
    ("ПОТ", "охране труда при эксплуатации"),
    ("правила ОДУ", "оперативно-диспетчерского управления в электроэнергетике"),
    ("ОДУ", "оперативно-диспетчерского управления в электроэнергетике"),
)


def _live_catalog_available() -> bool:
    paths = ProjectPaths.from_root(None)
    return paths.output_dir.joinpath("document_catalog.json").is_file()


@pytest.mark.skipif(not _live_catalog_available(), reason="live document_catalog.json missing")
@pytest.mark.parametrize("label,needle", LIVE_ACCEPTANCE_CASES)
def test_resolve_document_from_label_live_acceptance(label: str, needle: str) -> None:
    paths = ProjectPaths.from_root(None)
    result = resolve_document_from_label(label, project_paths=paths)

    assert result.found is True, (
        f"expected found for {label!r}, got status={result.status}, "
        f"candidates={[item.doc_name for item in result.candidates[:3]]}"
    )
    assert needle.lower() in result.doc_name.lower()
    assert "техническом регулировании" not in result.doc_name.lower()


@pytest.mark.skipif(not _live_catalog_available(), reason="live document_catalog.json missing")
def test_pot_not_potrebleniya_substring() -> None:
    paths = ProjectPaths.from_root(None)
    result = resolve_document_from_label("ПОТ", project_paths=paths)

    assert result.found is True
    assert "охране труда" in result.doc_name.lower()
    assert "потребления активной" not in result.doc_name.lower()


@pytest.mark.skipif(not _live_catalog_available(), reason="live document_catalog.json missing")
def test_info_for_odu_not_odu_rules() -> None:
    paths = ProjectPaths.from_root(None)
    result = resolve_document_from_label(
        "об информации для диспетчерского управления",
        project_paths=paths,
    )

    assert result.found is True
    assert "предоставления информации" in result.doc_name.lower()
    assert result.doc_name.lower() != (
        "об утверждении правил оперативно-диспетчерского управления в электроэнергетике"
    )


@pytest.mark.skipif(not _live_catalog_available(), reason="live document_catalog.json missing")
def test_likvidation_label_not_tech_reg_fz() -> None:
    paths = ProjectPaths.from_root(None)
    result = resolve_document_from_label("инструкция по ликвидации аварий", project_paths=paths)

    assert result.found is True
    assert "не тсо" not in result.doc_name.lower()
