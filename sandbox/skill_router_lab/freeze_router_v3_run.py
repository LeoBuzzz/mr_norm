"""Freeze v3 H2 inputs and prompts; no gold, receipts, or transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router_draft import SEMANTIC_DRAFT_SCHEMA
from router_prompts_v3 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_subscription_eval import freeze_files


HERE = Path(__file__).resolve().parent
SOURCE_RUN = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
PARENT_RUN_DIR = HERE / "router_subscription_run_tuning_v3_h2_r1_20261008"
RUN_DIR = HERE / "router_subscription_run_tuning_v3_h2_r2_20261008"
RUN_ID = "router-v3-h2-tuning-20261008-r2"


def _check_inherited_runtime(source_manifest: dict) -> dict[str, str]:
    inherited = source_manifest.get("code_files_sha256") or {}
    current = {}
    for name, expected in inherited.items():
        path = HERE / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"inherited v2 dependency changed since source freeze: {name}")
        current[name] = actual
    return current


def freeze() -> dict:
    source_manifest = json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    inherited_v2_hashes = _check_inherited_runtime(source_manifest)
    parent_manifest = json.loads((PARENT_RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    parent_manifest_sha256 = hashlib.sha256((PARENT_RUN_DIR / "manifest.json").read_bytes()).hexdigest()
    if source_manifest["case_count"] != 20 or source_manifest["split"] != "tuning":
        raise ValueError("source must be the frozen 20-case tuning run")
    inputs_bytes = (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes()
    source_bytes = (SOURCE_RUN / "source_manifests.json").read_bytes()
    schema_bytes = (SOURCE_RUN / "schema.json").read_bytes()
    if json.loads(schema_bytes) != SEMANTIC_DRAFT_SCHEMA:
        raise ValueError("semantic draft schema bytes differ from frozen v2")
    if hashlib.sha256(inputs_bytes).hexdigest() != source_manifest["files_sha256"]["inputs/input_manifest.jsonl"]:
        raise ValueError("input bytes do not match frozen v2 manifest")
    files = {
        "inputs/input_manifest.jsonl": inputs_bytes,
        "source_manifests.json": source_bytes,
        "schema.json": schema_bytes,
        "prompts/classifier.txt": CLASSIFIER_V3_SYSTEM.encode("utf-8"),
        "prompts/parser.txt": PARSER_V3_SYSTEM.encode("utf-8"),
        "prompts/repair.txt": REPAIR_V3_SYSTEM.encode("utf-8"),
    }
    # During implementation the v3 directory may be regenerated before any
    # subscription receipt exists. Once sampling begins, every frozen byte is
    # immutable.
    if (RUN_DIR / "receipts.jsonl").exists():
        hashes = freeze_files(RUN_DIR, files)
    else:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        for relative, payload in files.items():
            path = RUN_DIR / relative
            if path.exists() and path.read_bytes() != payload:
                path.write_bytes(payload)
        hashes = freeze_files(RUN_DIR, files)
    executable_v2_files = ["router_contracts.py", "router_draft.py", "router_planning.py",
                           "router_prompts_v2.py", "router_subscription_eval.py", "router_v2_runtime.py",
                           "run_router_v2_offline_gate.py", "evaluate_router_v2_tuning.py"]
    v3_files = ["router_prompts_v3.py", "router_subscription_eval_v3.py", "router_request_envelope_v3.py",
                "router_subscription_lifecycle_v3.py", "evaluate_router_v3_file_replay.py", "freeze_router_v3_run.py"]
    code_hashes = dict(inherited_v2_hashes)
    code_hashes.update({name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in executable_v2_files + v3_files})
    manifest = {
        "schema_version": "codex_subscription_file_replay.v1",
        "experiment_mode": "codex_subscription_file_replay",
        "run_id": RUN_ID,
        "lineage": {"parent_run_id": parent_manifest["run_id"],
                    "parent_manifest_sha256": parent_manifest_sha256,
                    "reason": "offline gate now validates exact frozen envelope payloads and complete coverage"},
        "split": "tuning",
        "case_count": 20,
        "case_order": source_manifest["case_order"],
        "tokenizer_version": source_manifest.get("tokenizer_version", "source_tokens.v1"),
        "locale": "ru-RU",
        "requested_effort": "medium",
        "requested_models": source_manifest["requested_models"],
        "supports_json_schema": False,
        "transport": "Codex subscription workers; no sampling in H2 implementation",
        "files_sha256": hashes,
        "code_files_sha256": code_hashes,
        "inherited_v2_code_files_sha256": inherited_v2_hashes,
        "parity": {
            "source_run_id": source_manifest["run_id"],
            "input_manifest_sha256_same": hashes["inputs/input_manifest.jsonl"] == source_manifest["files_sha256"]["inputs/input_manifest.jsonl"],
            "schema_sha256_same": hashes["schema.json"] == source_manifest["files_sha256"]["schema.json"],
            "case_order_same": True,
        },
        "gold_included": False,
        "receipts_included": False,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    manifest_path = RUN_DIR / "manifest.json"
    if manifest_path.exists() and manifest_path.read_bytes() != manifest_bytes and (RUN_DIR / "receipts.jsonl").exists():
        raise ValueError("refusing to change v3 manifest after receipts exist")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_bytes)
    return manifest


def validate_frozen(run_dir: Path = RUN_DIR) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files_sha256"].items():
        actual = hashlib.sha256((run_dir / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen input hash mismatch: {relative}")
    for relative, expected in manifest["code_files_sha256"].items():
        actual = hashlib.sha256((HERE / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"frozen code hash mismatch: {relative}")
    if manifest.get("inherited_v2_code_files_sha256") != json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8")).get("code_files_sha256"):
        raise ValueError("inherited v2 dependency hashes differ from source manifest")
    source_manifest = json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    _check_inherited_runtime(source_manifest)
    if manifest.get("inherited_v2_code_files_sha256") != source_manifest.get("code_files_sha256"):
        raise ValueError("inherited v2 dependency hashes differ from source manifest")
    lineage = manifest.get("lineage") or {}
    parent_path = PARENT_RUN_DIR / "manifest.json"
    if not parent_path.is_file() or lineage.get("parent_manifest_sha256") != hashlib.sha256(parent_path.read_bytes()).hexdigest():
        raise ValueError("v3 run lineage does not match its parent manifest")
    if (run_dir / "inputs/input_manifest.jsonl").read_bytes() != (SOURCE_RUN / "inputs/input_manifest.jsonl").read_bytes():
        raise ValueError("v3 input bytes differ from frozen v2")
    if (run_dir / "schema.json").read_bytes() != (SOURCE_RUN / "schema.json").read_bytes():
        raise ValueError("v3 schema bytes differ from frozen v2")
    rows = [json.loads(line) for line in (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 20 or [row["case_id"] for row in rows] != manifest["case_order"]:
        raise ValueError("v3 case count/order is invalid")
    if any({"expected", "gold", "split"} & set(row) for row in rows):
        raise ValueError("gold information leaked into v3 model inputs")
    if (run_dir / "gold.json").exists():
        raise ValueError("gold file appeared in frozen v3 run directory")
    if manifest["case_order"] != source_manifest["case_order"]:
        raise ValueError("v3 case order differs from v2")
    return {"status": "v3_frozen_inputs_valid", "run_id": manifest["run_id"],
            "case_count": len(rows), "receipts_present": (run_dir / "receipts.jsonl").exists(), "gold_present": False,
            "input_sha256_same": True, "schema_sha256_same": True}


if __name__ == "__main__":
    print(json.dumps({"manifest": freeze(), "validation": validate_frozen()}, ensure_ascii=False, indent=2))
