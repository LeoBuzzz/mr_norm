from __future__ import annotations

from pathlib import Path

from mr_norm.retrieval.knowledge_catalog_mapping import load_knowledge_catalog_mapping


def test_load_mapping_includes_35fz_energy_law() -> None:
    mapping_path = Path(__file__).resolve().parents[1] / "tmp" / "knowledge_catalog_mapping.json"
    if not mapping_path.is_file():
        return
    links = load_knowledge_catalog_mapping(mapping_path)
    link = links.get("f1e439e54e546a403a4cd3")
    assert link is not None
    assert link.catalog_id == "doc_7892b1f4e4994568"
    assert link.verified
