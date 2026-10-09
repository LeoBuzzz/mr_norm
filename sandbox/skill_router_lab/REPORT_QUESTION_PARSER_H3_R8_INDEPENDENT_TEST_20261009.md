# H3 r8 independent test report — 2026-10-09

Verdict: PASS for final H3 r8 changed provenance scope. Product code was not changed. No live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.

Correct run checked: `router-v3-h3-tuning-20261009-r8`.
Manifest SHA-256 checked: `ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`.

Verified scope:

- r8 launcher is bound to the r8 manifest and r8 `historical_h2_runtime` snapshot identity.
- Frozen evaluation resources are hash-checked before load: gold, reconciled baseline, H2 r2 manifest, H2 r1 parent manifest, v2 manifest, and H2/v2 resource inventories.
- Historical H2 replay reproduces exact metrics: first technical 15/20, final technical 20/20, reconciled baseline B=11/20, H2 semantic=5/20.
- Successful launcher evidence includes r8 run ID, r8 manifest SHA, snapshot identity/inventory hash, H2 manifest/receipts SHA, v2 manifest SHA, gold SHA, and baseline SHA.
- Transport label is r8-specific: `Codex subscription file replay; r8 implementation freeze only, no sampling`.
- Drift negatives reject tampering of gold, baseline, v2 manifest, H2 manifest, H2 parent manifest, snapshot code, and snapshot semantic replay resource. Snapshot directory substitution is rejected.
- Gates/scorer boundary behavior remained consistent with r7 expectations.
- One real H3 end-to-end path still passes: S02 lifecycle -> frozen prompt -> H3 policy -> assembly -> technical replay -> semantic replay.

Commands run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r8_independent_tmp sandbox/skill_router_lab/test_router_h3_r8_provenance.py
# 8 passed in 0.60s
```

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r8_relevant_tmp sandbox/skill_router_lab/test_router_h3_r8_provenance.py sandbox/skill_router_lab/test_router_h3_r7.py sandbox/skill_router_lab/test_router_h3_r6.py sandbox/skill_router_lab/test_router_h3_r5.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 192 passed in 2.83s
```

```powershell
python -m py_compile sandbox/skill_router_lab/freeze_router_v3_h3_r8.py sandbox/skill_router_lab/replay_router_h2_snapshot_r8.py sandbox/skill_router_lab/router_subscription_lifecycle_h3_r8.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay_r8.py sandbox/skill_router_lab/evaluate_router_v3_h3_semantic_replay_r8.py sandbox/skill_router_lab/test_router_h3_r8_provenance.py
# passed
```

```powershell
python sandbox/skill_router_lab/replay_router_h2_snapshot_r8.py
# first_technical_valid=15, final_technical_valid=20, baseline matched=11, semantic_matched=5
```

Independent scenario result:

```json
{
  "run_id": "router-v3-h3-tuning-20261009-r8",
  "manifest_sha256": "ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50",
  "transport": "Codex subscription file replay; r8 implementation freeze only, no sampling",
  "historical": {"first": 15, "final": 20, "B": 11, "H2": 5},
  "drift_negatives": 7,
  "e2e": "S02",
  "gold_sha256": "386e7d682cf5153dd9e5bba266e348eb621fc6cfd6378b2af51c62e4609c14fe",
  "baseline_sha256": "48cff410dfc9fc61f1cf4b4e4c39285f66a796188239711c44833a8487253929"
}
```

Defects found in H3 r8 changed provenance scope: none.
