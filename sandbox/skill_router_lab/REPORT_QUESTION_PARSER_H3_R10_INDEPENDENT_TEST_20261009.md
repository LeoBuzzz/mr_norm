# H3 r10 independent targeted test — 2026-10-09

Verdict: **PASS**.

Scope: targeted independent GPT-5.5 Sol medium test of `router-v3-h3-tuning-20261009-r10`. Correct manifest SHA-256 verified: `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.

No product code was changed. No live calls, sampling, holdout, Polza, Qdrant, production, bot, or eval workers were used.

## Commands run

- `python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r10_target_tmp sandbox/skill_router_lab/test_router_h3_r10_critical_identity.py sandbox/skill_router_lab/test_router_h3_r9_acceptance.py` — **10 passed**.
- `python -m py_compile sandbox/skill_router_lab/freeze_router_v3_h3_r10.py sandbox/skill_router_lab/replay_router_h2_snapshot_r10.py sandbox/skill_router_lab/router_subscription_lifecycle_h3_r10.py sandbox/skill_router_lab/evaluate_router_v3_h3_replay_r10.py sandbox/skill_router_lab/evaluate_router_v3_h3_semantic_replay_r10.py sandbox/skill_router_lab/test_router_h3_r10_critical_identity.py` — passed.
- `python sandbox/skill_router_lab/replay_router_h2_snapshot_r10.py` — passed; historical H2 replay reported first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20.
- `python -m pytest -q -p no:cacheprovider --basetemp sandbox/skill_router_lab/.pytest_h3r10_relevant_tmp sandbox/skill_router_lab/test_router_h3_r10_critical_identity.py sandbox/skill_router_lab/test_router_h3_r9_acceptance.py sandbox/skill_router_lab/test_router_h3_r8_provenance.py sandbox/skill_router_lab/test_router_h3_r7.py sandbox/skill_router_lab/test_router_h3_r6.py sandbox/skill_router_lab/test_router_h3_r5.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py` — **202 passed**.
- Independent integrity scripts for canonical omission identity, r9/r10 frozen provenance, r10 mutation/path negatives, H2 snapshot, gates, and controlled lifecycle path — passed.

## Confirmed r10 fix

`_regressions_against` now compares canonical critical omission record identities with multiset counts instead of code-only sets. The canonical identity keeps stable fields such as `code`, task/dependency indexes, and normalized `failed_fields`, while ignoring transient `closest_actual_task_index`.

Independent positives/negatives confirmed:

- Same code on a new `task_index` is detected as a new critical regression.
- Same code with changed normalized `failed_fields` is detected as a new critical regression.
- Dependency edge identity uses `source_task_index` and `target_task_index`; changed target is detected.
- Reordered omission lists and reordered `failed_fields` do not create a false regression.
- Changed `closest_actual_task_index` does not create a false regression.
- Duplicate count is handled as a multiset: one reference record versus two identical new records creates one added critical record.

## Confirmed preserved behavior

- r10 manifest identity, r9 parent lineage, and no-receipts implementation-freeze state are valid.
- Empty r10 replay fails closed with 20 missing attempts, `receipt_count=0`, final technical 0/20, useful/full gates false.
- Semantic replay consumes only frozen r10 evaluation gold/baseline resources and emits their hashes in provenance.
- Working-tree/global scorer path mutation has no effect on results or consumed-resource provenance.
- Explicit gold path substitution, snapshot directory substitution, and v2 source substitution are rejected.
- Frozen mutations are rejected for gold, baseline, v2 manifest, H2 manifest, H2 parent manifest, `historical_h2_runtime/router_v2_runtime.py`, and `historical_h2_runtime/evaluate_router_v3_semantic_replay.py`.
- r9 gate behavior is unchanged for the named false-pass negatives: two unnecessary clarifications fail full gate; H2-only regression fails useful and full gates.
- Historical H2 snapshot remains 15/20 -> 20/20, B=11/20, H2 semantic=5/20.
- A controlled real H3 lifecycle path on copied r10 run (`S02`) reaches `FileSubscriptionRunnerH3 -> prompt/envelope/receipt -> assemble_plan_h3 -> replay_h3`; it produced a valid `SkillQueryPlan` and replayed the generated receipt.

No blockers found in the r10 targeted scope.
