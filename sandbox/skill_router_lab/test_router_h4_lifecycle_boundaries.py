from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from freeze_router_v3_h4 import RUN_DIR
from router_subscription_lifecycle_h4 import FileSubscriptionRunnerH4


def _copy_run(tmp_path: Path) -> Path:
    target = tmp_path / "h4_run"
    shutil.copytree(RUN_DIR, target)
    return target


def _cases(run_dir: Path) -> dict[str, dict]:
    return {
        row["case_id"]: row
        for row in map(json.loads, (run_dir / "inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines())
    }


def _ref(row: dict) -> str:
    count = len(row["source_manifest"]["tokens"]["current"])
    return f"current@1:{count}"


def _draft(row: dict, *, operation: str = "search", uncertain: dict | None = None) -> str:
    draft = {"dialog": "new", "tasks": [{"operation": operation, "source": [_ref(row)], "topic": row["question"]}]}
    if uncertain:
        draft["uncertain"] = [uncertain]
    return json.dumps(draft, ensure_ascii=False)


def _run(run_dir: Path, row: dict, complexity: str, draft: str):
    calls = []
    outputs = iter([json.dumps({"complexity": complexity}), draft])

    def dispatch(envelope):
        calls.append(envelope)
        return next(outputs)

    result = FileSubscriptionRunnerH4(run_dir, dispatch).run_case(row)
    return result, calls


@pytest.mark.parametrize("case_id", ["V01", "V03", "V06"])
def test_h4_alias_families_are_ready_without_clarification_and_keep_request_details(tmp_path, case_id):
    run_dir = _copy_run(tmp_path)
    row = _cases(run_dir)[case_id]
    ref = _ref(row)
    draft = {
        "dialog": "new",
        "tasks": [{
            "operation": "search", "source": [ref], "topic": row["question"],
            "terms": [{"source": ref, "meaning": row["candidates"]["terms"][0]}],
            "conditions": [
                {"source": ref, "kind": "object", "polarity": "include"},
                {"source": ref, "kind": "quantity", "polarity": "include"},
                {"source": ref, "kind": "exclusion", "polarity": "exclude"},
            ],
        }],
    }
    result, calls = _run(run_dir, row, "simple", json.dumps(draft, ensure_ascii=False))
    assert [call["role"] for call in calls] == ["classifier", "simple_parser"]
    assert result["status"] == "valid"
    assert result["plan"]["status"] == "ready"
    assert result["plan"]["clarification"] is None
    task = result["plan"]["tasks"][0]
    assert task["concepts"][0]["surface"] == row["question"]
    assert {constraint["kind"] for constraint in task["constraints"]} >= {"object", "quantity", "exclusion"}
    assert any(constraint["polarity"] == "exclude" for constraint in task["constraints"])


def test_h4_unsupported_define_with_alias_family_is_deferred_without_clarification(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = _cases(run_dir)["U03"]
    result, calls = _run(run_dir, row, "complex", _draft(row, operation="define"))
    assert [call["role"] for call in calls] == ["classifier", "complex_parser"]
    assert result["status"] == "valid"
    assert result["plan"]["status"] == "deferred"
    assert result["plan"]["clarification"] is None
    assert any(issue["code"] == "unsupported_intent" for issue in result["plan"]["issues"])


@pytest.mark.parametrize(("case_id", "field", "complexity"), [("A02", "document", "complex"), ("A03", "term", "complex")])
def test_h4_genuine_ambiguities_preserve_exact_candidate_options(tmp_path, case_id, field, complexity):
    run_dir = _copy_run(tmp_path)
    row = _cases(run_dir)[case_id]
    candidates = [item["doc_id"] for item in row["candidates"]["documents"]] if field == "document" else row["candidates"]["terms"]
    ambiguous = {"field": field, "source": _ref(row), "task": 1, "candidates": candidates}
    result, calls = _run(run_dir, row, complexity, _draft(row, uncertain=ambiguous))
    assert [call["role"] for call in calls] == ["classifier", "complex_parser"]
    assert result["status"] == "valid"
    clarification = result["plan"]["clarification"]
    assert result["plan"]["status"] == "needs_clarification"
    assert clarification["field"] == field
    expected = []
    if field == "document":
        expected = [{"id": item["doc_id"], "label": item["doc_name"]} for item in row["candidates"]["documents"]]
    else:
        expected = [{"id": item, "label": item} for item in row["candidates"]["terms"]]
    assert clarification["options"] == expected


@pytest.mark.parametrize(("case_id", "field"), [("A02", "document"), ("A03", "term")])
def test_h4_unknown_candidate_ids_fail_closed(tmp_path, case_id, field):
    run_dir = _copy_run(tmp_path)
    row = _cases(run_dir)[case_id]
    candidates = [item["doc_id"] for item in row["candidates"]["documents"]] if field == "document" else row["candidates"]["terms"]
    uncertain = {"field": field, "source": _ref(row), "task": 1, "candidates": [*candidates, "unknown_candidate"]}
    invalid = _draft(row, uncertain=uncertain)
    calls = []
    outputs = iter([json.dumps({"complexity": "complex"}), invalid, invalid])

    def dispatch(envelope):
        calls.append(envelope)
        return next(outputs)

    result = FileSubscriptionRunnerH4(run_dir, dispatch).run_case(row)
    assert [(call["role"], call["attempt"]) for call in calls] == [
        ("classifier", 1), ("complex_parser", 1), ("complex_parser", 2),
    ]
    assert result["status"] == "model_failure"
    assert "uncertain candidates must be IDs from prepared manifest" in result["final_error"]


def test_h4_dispatches_at_most_one_repair_and_keeps_failure_terminal(tmp_path):
    run_dir = _copy_run(tmp_path)
    row = _cases(run_dir)["V01"]
    calls = []
    outputs = iter([json.dumps({"complexity": "simple"}), "not-json", "still-not-json"])

    def dispatch(envelope):
        calls.append(envelope)
        return next(outputs)

    result = FileSubscriptionRunnerH4(run_dir, dispatch).run_case(row)
    assert result["status"] == "model_failure"
    assert [(call["role"], call["attempt"]) for call in calls] == [
        ("classifier", 1), ("simple_parser", 1), ("simple_parser", 2),
    ]
    assert len(result["parser_attempts"]) == 2
