from __future__ import annotations

import json
from pathlib import Path

import pytest

PROMPT_PACK_REQUIRED_FIELDS = ("name", "version", "role", "inputs", "output_contract", "prompt")
PROMPT_PACK_ROLES = frozenset(
    {"planner", "reranker", "final_answer", "query_understanding", "query_planning"}
)
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src" / "mr_norm" / "config" / "prompts"


def iter_prompt_pack_paths() -> list[Path]:
    return sorted(PROMPTS_DIR.glob("*.json"))


def test_prompt_pack_directory_is_not_empty() -> None:
    paths = iter_prompt_pack_paths()
    assert paths, "expected at least one prompt pack JSON under config/prompts"


@pytest.mark.parametrize("path", iter_prompt_pack_paths(), ids=lambda path: path.name)
def test_prompt_pack_has_required_fields(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in PROMPT_PACK_REQUIRED_FIELDS:
        assert field in payload, f"{path.name} missing field: {field}"
    assert isinstance(payload["inputs"], dict)
    assert isinstance(payload["output_contract"], dict)
    assert payload["role"] in PROMPT_PACK_ROLES, f"{path.name} has unknown role: {payload['role']}"
    required_fields = payload["output_contract"].get("required_fields")
    assert isinstance(required_fields, list), f"{path.name} output_contract.required_fields must be a list"
    assert required_fields, f"{path.name} output_contract.required_fields must not be empty"
