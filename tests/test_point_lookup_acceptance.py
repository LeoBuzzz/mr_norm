from __future__ import annotations

import pytest

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.skills.point_lookup import PointLookupRequest, lookup_point


def _live_config_available() -> bool:
    try:
        config = IndexingConfig.from_env()
    except Exception:
        return False
    return bool(config.collection_name)


@pytest.mark.skipif(not _live_config_available(), reason="Qdrant collection not configured")
def test_point_lookup_live_ptf_points_3_and_99() -> None:
    config = IndexingConfig.from_env()
    result = lookup_point(
        PointLookupRequest(
            query="Дай текст пунктов 3 и 99 ПТФ",
            point_numbers=("3", "99"),
            doc_id="doc_6b2e4ec78884fb5f",
        ),
        config=config,
        project_paths=ProjectPaths.from_root(None),
    )

    assert result.found is True, result.warnings
    assert "3." in result.text
    assert "99." in result.text
    assert "chunk_" not in result.answer


@pytest.mark.skipif(not _live_config_available(), reason="Qdrant collection not configured")
@pytest.mark.parametrize(
    ("point_number", "doc_id"),
    [
        ("24", "doc_59934ce4728c9429"),
        ("98", "doc_59934ce4728c9429"),
    ],
)
def test_point_lookup_live_personnel_rules(point_number: str, doc_id: str) -> None:
    config = IndexingConfig.from_env()
    result = lookup_point(
        PointLookupRequest(
            doc_id=doc_id,
            point_number=point_number,
        ),
        config=config,
        project_paths=ProjectPaths.from_root(None),
    )

    assert result.found is True, result.warnings
    assert result.point_number == point_number
    assert result.text.strip().startswith(f"{point_number}.")
    assert "chunk_" not in result.answer
