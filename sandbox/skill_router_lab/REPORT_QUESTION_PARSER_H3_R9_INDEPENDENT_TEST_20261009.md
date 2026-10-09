# H3 r9 independent acceptance test — 2026-10-09

Verdict: **FAIL / changes_required**.

Scope: independent GPT-5.5 Sol medium test of `router-v3-h3-tuning-20261009-r9` after acceptance fixes. Correct manifest SHA-256 was verified: `6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`.

No product code was changed. No live calls, sampling, holdout, Polza, Qdrant, production, bot, or eval workers were used.

## Commands run

- `python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r9_independent_tmp sandbox/skill_router_lab/test_router_h3_r9_acceptance.py` — **7 passed**.
- `python -m py_compile sandbox/skill_router_lab/freeze_router_v3_h3_r9.py sandbox/skill_router_lab/replay_router_h2_snapshot_r9.py sandbox/skill_router_lab/router_subscription_lifecycle_h3_r9.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay_r9.py sandbox/skill_router_lab/evaluate_router_v3_h3_semantic_replay_r9.py sandbox/skill_router_lab/test_router_h3_r9_acceptance.py` — passed.
- `python sandbox/skill_router_lab/replay_router_h2_snapshot_r9.py` — passed; historical H2 replay reported first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20.
- `python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r9_relevant_tmp sandbox/skill_router_lab/test_router_h3_r9_acceptance.py sandbox/skill_router_lab/test_router_h3_r8_provenance.py sandbox/skill_router_lab/test_router_h3_r7.py sandbox/skill_router_lab/test_router_h3_r6.py sandbox/skill_router_lab/test_router_h3_r5.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py` — **199 passed**.
- Independent integrity script over r9 frozen resources, H2 snapshot, gate false-pass negatives, and controlled lifecycle path — passed except the blocker below.
- Direct same-code/different-task critical omission regression check — **failed acceptance expectation**.

## Confirmed pass areas

- Correct r9 run identity and manifest SHA are bound.
- r9 is an implementation freeze with `receipts_included=false`; empty H3 replay fails closed with 20 missing attempts, `receipt_count=0`, final technical 0/20, and gates false.
- Semantic replay consumes only `router_subscription_run_tuning_v3_h3_r9_20261009/evaluation/router_plan_cases_tuning_v2.json` and `.../baseline_reconciled_v3_20261008.json`; provenance emits the consumed gold/baseline hashes.
- Mutating module globals `GOLD_PATH` and `BASELINE_PATH` has no effect on metrics, case results, or consumed resource provenance.
- Explicit `gold_path` substitution is rejected.
- Snapshot directory and v2 source directory substitutions are rejected.
- Frozen mutations are rejected for gold, baseline, v2 manifest, H2 manifest, H2 parent manifest, `historical_h2_runtime/router_v2_runtime.py`, and `historical_h2_runtime/evaluate_router_v3_semantic_replay.py`.
- Gate false-pass negatives are covered for two unnecessary clarifications and H2-only regression: both fail the intended gates.
- H2 snapshot remains deterministic with first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20.
- A controlled real H3 lifecycle path on copied r9 run (`S02`) reaches `FileSubscriptionRunnerH3 -> prompt/envelope/receipt -> assemble_plan_h3 -> replay_h3`; it produced a valid `SkillQueryPlan` and replayed the generated receipt.

## Blocker

`evaluate_router_v3_h3_semantic_replay_r9._regressions_against` still compares critical omissions only by `code`, not by stable omission identity. If a reference row has `{'code': 'unmatched_expected_task', 'task_index': 0}` and the new row has `{'code': 'unmatched_expected_task', 'task_index': 1}`, it reports:

```json
{
  "plan_regression_case_ids": [],
  "added_critical_codes": [],
  "no_new_critical_regressions": true
}
```

That is a false pass: the new critical omission is on a different task and must be treated as a new regression. This is the same P1 issue recorded in the previous checkpoint, and it remains in the current r9 code path.

## Required fix

Compare stable critical omission identities rather than code-only sets. Include at least `code` plus task/evidence identity fields where present, such as `task_index`, `expected_task_index`, `actual_task_index`, `field`, `doc_id`, `point_ref`, or another deterministic normalized omission key. Add a regression test for same-code/different-task so the useful/full gates cannot pass this case.
