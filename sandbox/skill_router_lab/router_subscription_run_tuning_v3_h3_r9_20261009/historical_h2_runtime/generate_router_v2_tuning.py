"""Migrate the archived tuning-only Polza report into a frozen v2 manifest.

The input report contains 20 tuning records and no holdout records. This script
refuses any split other than exactly those 20 rows and never reads v1 gold.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router_draft import TOKEN_RE, TOKENIZER_VERSION
from router_prompts_v2 import CLASSIFIER_V2_SYSTEM, PARSER_V2_SYSTEM
from router_subscription_eval import build_source_manifest, canonical_json_bytes, freeze_files

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "router_plan_live_tuning_polza_gpt6_20261007.json"
DEST = HERE / "router_plan_cases_tuning_v2.json"
SOURCES_DEST = HERE / "router_source_manifests_tuning_v2.json"
RUN_DIR = HERE / "router_subscription_run_tuning_v2_criticfix3_20261008"
CODE_FILES = ("router_contracts.py", "router_draft.py", "router_planning.py", "router_prompts_v2.py", "router_v2_runtime.py", "router_subscription_eval.py", "run_router_v2_offline_gate.py", "generate_router_v2_tuning.py")


def _ref_for_quote(quote: str, source_id: str, text: str) -> str | None:
    matches = list(TOKEN_RE.finditer(text))
    for start in range(len(matches)):
        for end in range(start + 1, len(matches) + 1):
            if text[matches[start].start():matches[end - 1].end()] == quote:
                return f"{source_id}@{start + 1}:{end}"
    return None


def migrate() -> tuple[dict, dict]:
    report = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = report.get("records", [])
    if len(records) != 20 or any(row.get("split") != "tuning" for row in records):
        raise ValueError("migration source must contain exactly 20 tuning records and no other split")
    cases, source_manifests = [], {}
    for record in records:
        input_row = record["input"]
        case = {"id": record["case_id"], "split": "tuning", "group": record["group"],
                "question": input_row["question"], "context": input_row.get("context", {}),
                "pending_clarification": input_row.get("pending_clarification"), "candidates": input_row.get("candidates", {})}
        if case["id"] in {"V03", "V06"}:
            case["metamorphic_pair"] = "V03_V06"
        source = build_source_manifest(case)
        source_manifests[case["id"]] = source
        old = record["expected"]
        expected_tasks = []
        for task in old.get("tasks", []):
            operation = {"exact_point": "text", "corpus_point_search": "search", "find_all": "all"}.get(task.get("skill"), "unknown")
            source_refs = []
            source_ids = ["current", *[str(turn.get("turn_id", f"turn_{i + 1}")) for i, turn in enumerate(case["context"].get("turns", []))]]
            source_texts = source["sources"]
            for quote in task.get("evidence", []):
                ref = next((_ref_for_quote(quote, turn_id, source_texts.get(turn_id, "")) for turn_id in source_ids if _ref_for_quote(quote, turn_id, source_texts.get(turn_id, ""))), None)
                if ref:
                    source_refs.append(ref)
            if not source_refs:
                source_refs = ["current@1:1"] if source["tokens"].get("current") else []
            expected_tasks.append({"operation": operation, "doc_id": task.get("doc_id"), "point_ref": task.get("point_ref"),
                                  "coverage": task.get("coverage"), "depends_on": task.get("depends_on", []),
                                  "required_semantic_elements": task.get("required_semantic_elements", []),
                                  "constraints": task.get("constraints", []), "source_refs": source_refs})
        clarification_options = []
        if old.get("clarification_field") == "document":
            clarification_options = [{"id": item.get("doc_id"), "labels": [item.get("doc_name", ""), *item.get("aliases", [])]}
                                     for item in case["candidates"].get("documents", []) if item.get("doc_id")]
        elif old.get("clarification_field") == "term":
            clarification_options = [{"labels": [str(item)]} for item in case["candidates"].get("terms", []) if str(item).strip()]
        case["expected"] = {"complexity": old.get("complexity"), "status": old.get("status"), "dialog_action": old.get("dialog_action"),
                            "tasks": expected_tasks, "clarification_field": old.get("clarification_field"),
                            "clarification_subject": old.get("clarification_subject"), "clarification_options": clarification_options,
                            "forbidden_substitutions": old.get("forbidden_substitutions", [])}
        cases.append(case)
    migrated = {"schema_version": "router_plan_cases.tuning_v2", "source_report": SOURCE.name, "source_report_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "tokenizer_version": TOKENIZER_VERSION, "cases": cases}
    manifests = {"schema_version": "router_source_manifests.tuning_v2", "tokenizer_version": TOKENIZER_VERSION, "cases": source_manifests}
    return migrated, manifests


def main() -> int:
    cases, sources = migrate()
    DEST.write_bytes(canonical_json_bytes(cases) + b"\n")
    SOURCES_DEST.write_bytes(canonical_json_bytes(sources) + b"\n")
    for path in (DEST, SOURCES_DEST):
        print(f"{path.name} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
    input_lines = []
    for case in cases["cases"]:
        input_lines.append(canonical_json_bytes({"case_id": case["id"], "question": case["question"],
            "context": case["context"], "pending_clarification": case.get("pending_clarification"),
            "candidates": case["candidates"], "source_manifest": sources["cases"][case["id"]]}))
    files = {
        "inputs/input_manifest.jsonl": b"\n".join(input_lines) + b"\n",
        "prompts/classifier.txt": CLASSIFIER_V2_SYSTEM,
        "prompts/parser.txt": PARSER_V2_SYSTEM,
        "schema.json": (HERE / "router_draft_schema.json").read_bytes(),
        "source_manifests.json": canonical_json_bytes(sources) + b"\n",
    }
    hashes = freeze_files(RUN_DIR, files)
    manifest = {"schema_version": "codex_subscription_file_replay.v1", "experiment_mode": "codex_subscription_file_replay",
                "run_id": "router-v2-tuning-20261007", "case_count": 20, "split": "tuning",
                "case_order": [case["id"] for case in cases["cases"]], "locale": "ru-RU",
                "requested_models": {"classifier": "gpt-6-luna", "simple_parser": "gpt-6-luna", "complex_parser": "gpt-6-sol"},
                "requested_effort": "medium", "transport": "Codex subscription workers; not yet sampled",
                "supports_json_schema": False, "capability_evidence": "file replay defines no schema-enforcement transport",
                "code_files_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in CODE_FILES},
                "files_sha256": hashes}
    freeze_files(RUN_DIR, {"manifest.json": canonical_json_bytes(manifest) + b"\n"})
    print(f"{RUN_DIR.name}/manifest.json sha256={hashlib.sha256((RUN_DIR / 'manifest.json').read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
