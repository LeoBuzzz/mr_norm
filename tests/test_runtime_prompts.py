from __future__ import annotations

import json
from pathlib import Path

import pytest

from mr_norm.runtime.prompts import (
    PROMPTS_DIR,
    PromptPackError,
    iter_prompt_pack_paths,
    list_prompt_packs,
    load_prompt_pack,
    load_prompt_pack_by_name,
    load_prompt_pack_by_role,
    validate_prompt_pack,
)


def test_iter_prompt_pack_paths_includes_reranker_asset() -> None:
    names = {path.name for path in iter_prompt_pack_paths()}

    assert "planner_evidence_v1.json" in names
    assert "final_answer_v1.json" in names
    assert "reranker_evidence_v1.json" in names


def test_list_prompt_packs_loads_all_roles() -> None:
    packs = list_prompt_packs()

    roles = {pack["role"] for pack in packs}
    assert roles == {"planner", "reranker", "final_answer", "query_understanding", "query_planning"}


def test_load_prompt_pack_by_role_returns_expected_schema_versions() -> None:
    planner = load_prompt_pack_by_role("planner")
    reranker = load_prompt_pack_by_role("reranker")
    final_answer = load_prompt_pack_by_role("final_answer")

    assert planner["output_contract"]["schema_version"] == "mr_planner_plan_v1"
    assert reranker["output_contract"]["schema_version"] == "mr_rerank_v1"
    assert final_answer["output_contract"]["schema_version"] == "mr_final_answer_v1"


def test_load_prompt_pack_by_name() -> None:
    pack = load_prompt_pack_by_name("reranker_evidence")

    assert pack["role"] == "reranker"
    assert pack["output_contract"]["required_fields"] == ["ranked_chunk_ids"]


def test_load_prompt_pack_by_role_unknown_role_raises() -> None:
    with pytest.raises(PromptPackError, match="unknown prompt pack role"):
        load_prompt_pack_by_role("unknown")


def test_validate_prompt_pack_rejects_missing_required_field(tmp_path: Path) -> None:
    payload = {
        "name": "broken",
        "version": "1",
        "role": "planner",
        "inputs": {},
        "output_contract": {"schema_version": "mr_planner_plan_v1", "required_fields": ["selected_tools"]},
    }

    with pytest.raises(PromptPackError, match="missing field: prompt"):
        validate_prompt_pack(payload, source="broken.json")


def test_load_prompt_pack_reads_file_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "custom_planner.json"
    path.write_text(
        json.dumps(
            {
                "name": "custom_planner",
                "version": "1",
                "role": "planner",
                "inputs": {"query": "string"},
                "output_contract": {
                    "schema_version": "mr_planner_plan_v1",
                    "required_fields": ["selected_tools"],
                },
                "prompt": "test",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = load_prompt_pack(path)

    assert pack["name"] == "custom_planner"


def test_builtin_prompt_packs_directory_matches_runtime_loader() -> None:
    assert PROMPTS_DIR.name == "prompts"
    assert PROMPTS_DIR.is_dir()
