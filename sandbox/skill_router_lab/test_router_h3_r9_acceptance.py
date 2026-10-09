from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import evaluate_router_v3_h3_semantic_replay_r9 as semantic
from freeze_router_v3_h3_r9 import RUN_DIR, validate_frozen


def _passing_gate_metrics() -> dict:
    return {"coverage_complete": True, "coverage_denominator": 20,
            "first_technical_valid": 19, "first_technical_denominator": 20,
            "final_technical_valid": 20, "final_technical_denominator": 20,
            "locator_free_accepted_plans": True, "semantic_matched": 18,
            "no_new_critical_regressions_vs_baseline": True,
            "no_new_critical_regressions_vs_h2": True,
            "unnecessary_clarifications": 0, "zero_critical_ready_losses": True,
            "classification_correct": 18, "all_expected_complex_correct": True,
            "status_matched": 18, "all_ambiguities_correct": True,
            "paired_v03_v06_passed": True}


def test_r9_full_gate_rejects_two_unnecessary_clarifications():
    metrics = _passing_gate_metrics()
    metrics["unnecessary_clarifications"] = 2
    gates = semantic.compute_h3_gates(metrics)
    assert gates["unnecessary_clarifications_at_most_1"] is False
    assert gates["full_tuning_gate_pass"] is False


def test_r9_gates_compare_critical_regressions_to_h2_separately():
    metrics = _passing_gate_metrics()
    metrics["no_new_critical_regressions_vs_h2"] = False
    gates = semantic.compute_h3_gates(metrics)
    assert gates["no_new_critical_regressions_vs_baseline"] is True
    assert gates["no_new_critical_regressions_vs_h2"] is False
    assert gates["useful"] is False
    assert gates["full_tuning_gate_pass"] is False


def test_r9_semantic_replay_ignores_mutable_working_tree_resource_globals(tmp_path, monkeypatch):
    before = semantic.semantic_replay_h3()
    changed_gold = tmp_path / "working-tree-gold.json"
    changed_baseline = tmp_path / "working-tree-baseline.json"
    changed_gold.write_text('{"cases":[]}', encoding="utf-8")
    changed_baseline.write_text('{"case_scores":[]}', encoding="utf-8")
    monkeypatch.setattr(semantic, "GOLD_PATH", changed_gold)
    monkeypatch.setattr(semantic, "BASELINE_PATH", changed_baseline)
    after = semantic.semantic_replay_h3()
    assert before["metrics"] == after["metrics"]
    assert before["case_results"] == after["case_results"]
    assert before["provenance"]["consumed_resources"] == after["provenance"]["consumed_resources"]
    assert after["provenance"]["consumed_resources"]["gold_path"].endswith(
        "evaluation\\router_plan_cases_tuning_v2.json")


def test_r9_rejects_arbitrary_gold_path_even_when_supplied(tmp_path):
    alternate = tmp_path / "gold.json"
    alternate.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unchecked gold_path substitution"):
        semantic.semantic_replay_h3(gold_path=alternate)


@pytest.mark.parametrize("relative", [
    "evaluation/router_plan_cases_tuning_v2.json",
    "evaluation/baseline_reconciled_v3_20261008.json",
])
def test_r9_rejects_mutated_frozen_scorer_resource(tmp_path, relative):
    copied = tmp_path / "r9-copy"
    shutil.copytree(RUN_DIR, copied)
    resource = copied / relative
    resource.write_bytes(resource.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_frozen(copied)


def test_r9_provenance_identifies_frozen_resources_and_h2_reference():
    report = semantic.semantic_replay_h3()
    provenance = report["provenance"]
    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert provenance["r9_run_id"] == manifest["run_id"]
    assert provenance["r9_manifest_sha256"] == validate_frozen()["manifest_sha256"]
    assert provenance["consumed_resources"]["gold_sha256"] == manifest["evaluation_data_sha256"][
        "evaluation/router_plan_cases_tuning_v2.json"]
    assert provenance["consumed_resources"]["baseline_sha256"] == manifest["evaluation_data_sha256"][
        "evaluation/baseline_reconciled_v3_20261008.json"]
    assert provenance["h2_reference_manifest_sha256"] == manifest["external_resources"]["h2_run"]["manifest_sha256"]
