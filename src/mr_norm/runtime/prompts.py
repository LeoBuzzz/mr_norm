from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROMPT_PACK_REQUIRED_FIELDS = ("name", "version", "role", "inputs", "output_contract", "prompt")
PROMPT_PACK_ROLES = frozenset({"planner", "reranker", "final_answer"})
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "config" / "prompts"


class PromptPackError(ValueError):
    """Raised when a prompt pack asset is missing or invalid."""


def iter_prompt_pack_paths(*, prompts_dir: Path | None = None) -> list[Path]:
    directory = prompts_dir or PROMPTS_DIR
    return sorted(directory.glob("*.json"))


def validate_prompt_pack(payload: dict[str, Any], *, source: str = "prompt pack") -> dict[str, Any]:
    for field_name in PROMPT_PACK_REQUIRED_FIELDS:
        if field_name not in payload:
            raise PromptPackError(f"{source} missing field: {field_name}")

    role = payload["role"]
    if role not in PROMPT_PACK_ROLES:
        raise PromptPackError(f"{source} has unknown role: {role}")

    if not isinstance(payload["inputs"], dict):
        raise PromptPackError(f"{source} inputs must be an object")

    output_contract = payload["output_contract"]
    if not isinstance(output_contract, dict):
        raise PromptPackError(f"{source} output_contract must be an object")

    required_fields = output_contract.get("required_fields")
    if not isinstance(required_fields, list) or not required_fields:
        raise PromptPackError(f"{source} output_contract.required_fields must be a non-empty list")

    schema_version = output_contract.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise PromptPackError(f"{source} output_contract.schema_version must be a non-empty string")

    return payload


def load_prompt_pack(path: Path | str) -> dict[str, Any]:
    pack_path = Path(path)
    if not pack_path.is_file():
        raise PromptPackError(f"prompt pack not found: {pack_path}")
    payload = json.loads(pack_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PromptPackError(f"prompt pack must be a JSON object: {pack_path.name}")
    return validate_prompt_pack(payload, source=pack_path.name)


def list_prompt_packs(*, prompts_dir: Path | None = None) -> list[dict[str, Any]]:
    return [load_prompt_pack(path) for path in iter_prompt_pack_paths(prompts_dir=prompts_dir)]


def load_prompt_pack_by_role(role: str, *, prompts_dir: Path | None = None) -> dict[str, Any]:
    if role not in PROMPT_PACK_ROLES:
        raise PromptPackError(f"unknown prompt pack role: {role}")

    matches = [
        pack
        for pack in list_prompt_packs(prompts_dir=prompts_dir)
        if pack["role"] == role
    ]
    if not matches:
        raise PromptPackError(f"no prompt pack found for role: {role}")
    if len(matches) > 1:
        names = ", ".join(sorted(pack["name"] for pack in matches))
        raise PromptPackError(f"multiple prompt packs found for role {role}: {names}")
    return matches[0]


def load_prompt_pack_by_name(name: str, *, prompts_dir: Path | None = None) -> dict[str, Any]:
    directory = prompts_dir or PROMPTS_DIR
    for path in iter_prompt_pack_paths(prompts_dir=directory):
        pack = load_prompt_pack(path)
        if pack["name"] == name:
            return pack
    raise PromptPackError(f"no prompt pack found with name: {name}")
