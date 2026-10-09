# H3 r3 independent test report — 2026-10-08

Verdict: PASS for the changed H3 r3 implementation/freeze area. Product code was not changed. No live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.

Correct run checked: `router-v3-h3-tuning-20261008-r3`.
Manifest SHA-256 checked: `509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5`.

Changed area verified:

- `router_subscription_lifecycle_h3.py` uses frozen H3 prompts from the run directory, refuses non-H3 run identity/policy, and validates parser/repair output through manifest-selected `topic_literal_guard.v1`.
- `evaluate_router_v3_h3_replay.py` reconstructs exact frozen classifier/parser/repair envelopes, validates bindings, rejects coherent rehash tampering, computes empty replay denominators as 20, and reports missing attempts without sampling.
- Frozen prompt/policy integrity: classifier prompt remains H2-identical, parser/repair prompt hashes match H3 r3 manifest, schema/input/order parity with H2 is preserved, and r1/r2 are listed as superseded unreceipted runs.
- `TOPIC_MUST_BE_LITERAL_TEXT` is raised on initial parser locator topic, repair receives the exact raw response and recomputed validation error, a valid repair succeeds, and a second locator after repair fails after exactly one repair.
- Literal topic passes without repair and assembles to a ready `corpus_point_search` query unchanged.
- Exact point SourceRef passes and assembly extracts `point_ref=6.25` with the frozen document ID.
- Composite point request passes as two exact point tasks and assembly extracts `6.25` and `6.26` separately.
- Ambiguity with supplied document candidate IDs survives lifecycle validation and assembles to `needs_clarification` with exact option IDs.
- Empty H3 r3 replay has `receipt_count=0`, denominator 20, `coverage_complete=false`, `overall_gate_pass=false`, and 20 missing attempts.
- Tamper negatives reject coherently rehashed classifier envelope content and forged repair `validation_errors`.
- H2 immutability remains: H2 manifest SHA `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`, H2 receipts SHA `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`, 45 receipt rows.

Commands run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r3_independent_tmp sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py
# 28 passed in 0.48s
```

```powershell
python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r3_full_tmp sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 160 passed in 0.74s
```

```powershell
python -m py_compile sandbox/skill_router_lab/router_subscription_lifecycle_h3.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay.py sandbox/skill_router_lab/freeze_router_v3_h3_run.py sandbox/skill_router_lab/router_draft_h3.py sandbox/skill_router_lab/router_prompts_v3_h3.py sandbox/skill_router_lab/router_validation_policy.py sandbox/skill_router_lab/test_router_h3_lifecycle.py sandbox/skill_router_lab/test_router_h3.py
# passed
```

```powershell
python sandbox/skill_router_lab/run_router_v2_offline_gate.py
# {"case_count":20,"live_workers_started":false,"receipt_state":"receipts_validated","run_id":"router-v2-tuning-20261007","status":"offline_frozen_inputs_valid"}
```

Independent scenario script result:

```json
{
  "run_id": "router-v3-h3-tuning-20261008-r3",
  "manifest_sha256": "509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5",
  "empty_replay_missing": 20,
  "scenarios": [
    "locator_repair",
    "locator_repair_fails",
    "literal_topic",
    "exact_point",
    "composite_points",
    "ambiguity",
    "tamper_envelope",
    "tamper_repair"
  ],
  "h2_receipt_count": 45
}
```

Note: the first attempted pytest run without `--basetemp` errored during pytest `tmp_path` setup because `C:\Users\buleo\AppData\Local\Temp\pytest-of-l_buzz` was not readable. The same tests passed when basetemp was placed inside the writable workspace. This was an environment temp-permission issue, not an H3 failure.

Defects found in H3 r3 changed area: none.
