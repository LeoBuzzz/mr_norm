from __future__ import annotations

import json
from pathlib import Path

import pytest

PROMPT_PACK_REQUIRED_FIELDS = ("name", "version", "role", "inputs", "output_contract", "prompt")
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src" / "mr_norm" / "config" / "prompts"


def iter_prompt_pack_paths() -> list[Path]:
    return sorted(PROMPTS_DIR.glob("*.json"))


@pytest.mark.parametrize("path", iter_prompt_pack_paths(), ids=lambda path: path.name)
def test_prompt_pack_has_required_fields(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in PROMPT_PACK_REQUIRED_FIELDS:
        assert field in payload, f"{path.name} missing field: {field}"
    assert isinstance(payload["inputs"], dict)
    assert isinstance(payload["output_contract"], dict)
