# H3 r4 independent test report — 2026-10-08

Verdict: PASS for H3 r4 changed scope, with one explicit H2-verifier caveat described below. Product code was not changed. No live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.

Correct run checked: `router-v3-h3-tuning-20261008-r4`.
Manifest SHA-256 checked: `5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`.

Changed scope verified:

- Legacy V3 runner rejects an H3 r4 directory before envelope creation, dispatch, receipt writing, or binding writing.
- H3 lifecycle dispatches frozen H3 prompt bytes, applies `validation_policy=topic_literal_guard.v1` on initial and repair attempts, and returns assembled `SkillQueryPlan` through `assemble_plan_h3`.
- H3 technical replay reconstructs exact envelopes from frozen prompts/input, validates receipt/envelope bindings, rejects coherent tampering, and exposes `actual_plan` only after policy-valid final drafts.
- H3 semantic replay consumes `replay_h3` output, applies the reconciled scorer/gates, uses denominator 20, and does not use placeholder semantic results.
- Empty replay fails closed: no receipts, 20 missing attempts, complete false, overall gate false, semantic denominator 20.
- Five scenario end-to-end path covered: literal search (`S02`), exact point (`S01`), two-point composite (`C02`), locator initial + repair + assembly (`S03`), and ambiguity/clarification (`A02`). The independent scenario run produced 11 receipts and semantic scores for all five actual plans.
- Envelope/repair binding and tamper negatives were covered by both tests and independent scenario script.

Commands run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r4_independent_tmp sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py
# 30 passed in 0.62s
```

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r4_full_tmp sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 162 passed in 0.95s
```

```powershell
python -m py_compile sandbox/skill_router_lab/router_subscription_lifecycle_h3.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay.py sandbox/skill_router_lab/evaluate_router_v3_h3_semantic_replay.py sandbox/skill_router_lab/freeze_router_v3_h3_run.py sandbox/skill_router_lab/router_draft_h3.py sandbox/skill_router_lab/router_prompts_v3_h3.py sandbox/skill_router_lab/router_validation_policy.py sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py
# passed
```

```powershell
python sandbox/skill_router_lab/run_router_v2_offline_gate.py
# {"case_count":20,"live_workers_started":false,"receipt_state":"receipts_validated","run_id":"router-v2-tuning-20261007","status":"offline_frozen_inputs_valid"}
```

Independent scenario script result:

```json
{
  "run_id": "router-v3-h3-tuning-20261008-r4",
  "manifest_sha256": "5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0",
  "empty_missing": 20,
  "five_case_receipts": 11,
  "five_cases": ["A02", "C02", "S01", "S02", "S03"],
  "legacy_bypass_rejected_before_dispatch": true,
  "semantic_scores_present": true
}
```

H2 verifier failures assessed:

- `freeze_router_v3_run.validate_frozen()` fails with `ValueError: frozen code hash mismatch: router_subscription_lifecycle_v3.py`.
- `evaluate_router_v3_file_replay.replay_v3()` fails for the same reason.
- `evaluate_router_v3_semantic_replay.semantic_replay()` fails for the same reason because it calls the technical replay.

These failures are an expected consequence of changing the shared checkout file `router_subscription_lifecycle_v3.py` to add the mandatory H3 pre-dispatch guard. H2 manifest, receipts, saved reports, and the H2 historical runtime snapshots in H3 r3/r4 were not rewritten. That means the H2 artifacts themselves were not corrupted.

However, direct current-checkout H2 verification no longer passes. If the requirement is that historical H2 replay must be runnable through the current checkout verifier, then this is a historical reproducibility violation. If the requirement is artifact immutability plus a frozen historical-runtime snapshot, then it is acceptable only with an explicit test criterion that proves the snapshot path, not the changed checkout, is the authority.

Minimal test criterion without changing product code: a gate should fail unless all of these are true:

1. H2 manifest, receipts, and saved semantic report hashes equal the recorded immutable hashes.
2. The H2 verifier failure set is exactly the known live-checkout hash drift for `router_subscription_lifecycle_v3.py`; no prompt/input/schema/receipt/hash drift is allowed.
3. The frozen `historical_h2_runtime/router_subscription_lifecycle_v3.py` hash inside H3 r4 equals the hash recorded in the H2 manifest.
4. H2 metrics are recoverable from the immutable saved report or a snapshot-based replay path: first technical 15/20, final technical 20/20, semantic 5/20, B=11/20.
5. Any claim that H2 historical replay passes must name the snapshot-based verifier; the current-checkout `freeze_router_v3_run.validate_frozen()` must not be reported as passing while it fails.

Defects found in H3 r4 changed scope: none. The remaining caveat is the H2 verifier criterion above.
