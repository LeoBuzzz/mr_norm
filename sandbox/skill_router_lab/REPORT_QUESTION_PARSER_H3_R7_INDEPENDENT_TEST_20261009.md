# H3 r7 independent test report — 2026-10-09

Verdict: PASS for final H3 r7. Product code was not changed. No live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.

Correct run checked: `router-v3-h3-tuning-20261009-r7`.
Manifest SHA-256 checked: `53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`.

Verified scope:

- Snapshot-isolated H2 launcher reproduces exact historical metrics: first technical 15/20, final technical 20/20, reconciled baseline B=11/20, H2 semantic=5/20.
- Snapshot H2 replay is deterministic across repeated runs at the technical/semantic-metrics level.
- Snapshot H2 replay rejects coherent prompt and user tampering after envelope rehashing.
- r7 `validate_frozen`, empty technical replay, and empty semantic replay are fail-closed with denominator 20 and no receipts.
- Named H3 useful/full gates reject negative false-pass cases for coverage, first/final technical, locator-free plans, semantic threshold, critical regressions, classifier, status, ambiguity, and V03/V06 metamorphic requirements.
- Integer `task_assignment` attribution is accepted; unmatched ready task is counted as critical loss for intent, negation, number/unit, and doc_id.
- One real end-to-end H3 path was exercised through lifecycle -> frozen prompt -> H3 validation policy -> assembly -> technical replay -> semantic replay, using case S02. The lifecycle plan and replay `actual_plan` matched exactly.
- Legacy V3 runner rejects r7 before dispatch/write.
- r4/r5/r6 manifest immutability was checked by SHA-256.

Commands run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r7_independent_tmp sandbox/skill_router_lab/test_router_h3_r7.py
# 12 passed in 0.71s
```

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r7_relevant_tmp sandbox/skill_router_lab/test_router_h3_r7.py sandbox/skill_router_lab/test_router_h3_r6.py sandbox/skill_router_lab/test_router_h3_r5.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 184 passed in 2.19s
```

```powershell
python -m py_compile sandbox/skill_router_lab/freeze_router_v3_h3_r7.py sandbox/skill_router_lab/replay_router_h2_snapshot.py sandbox/skill_router_lab/router_subscription_lifecycle_h3_r7.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay_r7.py sandbox/skill_router_lab/evaluate_router_v3_h3_semantic_replay_r7.py sandbox/skill_router_lab/test_router_h3_r7.py sandbox/skill_router_lab/freeze_router_v3_h3_r6.py sandbox/skill_router_lab/freeze_router_v3_h3_r5.py
# passed
```

```powershell
python sandbox/skill_router_lab/replay_router_h2_snapshot.py
# first_technical_valid=15, final_technical_valid=20, baseline matched=11, semantic_matched=5
```

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r7_tamper_tmp sandbox/skill_router_lab/test_router_h3_r7.py::test_snapshot_h2_replay_rejects_coherently_rehashed_tampering
# 2 passed in 0.47s
```

Independent integrity script result:

```json
{
  "run_id": "router-v3-h3-tuning-20261009-r7",
  "manifest_sha256": "53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417",
  "snapshot_h2": {"first": 15, "final": 20, "B": 11, "H2": 5},
  "empty_missing": 20,
  "e2e_case": "S02",
  "r4_r5_r6_hashes_ok": true
}
```

Immutability hashes checked:

- r4 manifest: `5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`
- r5 manifest: `c5ef56602de9ed73773a1bcd9c88b3eeb88e553e7e60e896f0fcb64f9c42bf29`
- r6 manifest: `b18c1c25e5c69915f2e7ea9d92d0d8eb554720ee454a368abadc0ce6b706200a`

Caveat: a broad legacy command including `test_router_h3_lifecycle.py` produced 188 passed and 6 failed. The failures are stale r4 tests importing old `router_subscription_lifecycle_h3.py` / `freeze_router_v3_h3_run.py`; r4 freeze now rejects current checkout hash drift in `evaluate_router_v3_h3_semantic_replay.py`. This is not an r7 failure, but that old suite should not be treated as the final r7 gate unless updated to r7-specific entrypoints or converted to immutable snapshot checks.

Defects found in final H3 r7 changed scope: none.
