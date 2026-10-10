# H5 r2 independent retest — 2026-10-09

Verdict: **FAIL**.

Tested run: `router-v3-h5-tuning-20261009-r2` in
`router_subscription_run_tuning_v3_h5_r2_20261009`.

No live LLM, sampling, Polza, holdout, Qdrant, production, or bot path was
used. No implementation, prompt, frozen run, manifest, specification,
decision, or checkpoint file was changed by this test role. This report is the
only persistent artifact written by the retest.

## Runtime

The checks used `C:\Users\buleo\miniconda3\python.exe`, Python 3.13.11 and
pytest 9.0.3. The launcher printed the same non-fatal `Failed to find real
location` warning seen in r1 testing; commands and assertions executed
normally.

## Frozen identity, resources, and empty fail-closed

Direct read-only hash and inventory audit passed for these properties:

- r2 run ID is `router-v3-h5-tuning-20261009-r2`.
- r2 manifest SHA-256 is exactly
  `0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194`.
- Direct parent is r1 and its manifest SHA-256 is exactly
  `c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd`.
- The r1 frozen resource and historical-runtime hashes still match its
  manifest and inventory. R1 has no receipt-like JSONL files.
- R2 has the same 20 inputs in the same order and no receipt-like JSONL files.
  Ten r2 resources are byte-identical to r1; only `prompts/parser.txt` differs,
  as intended by the final wording revision.
- Every r2 frozen resource and historical-runtime byte hash matches its
  manifest. Inventory manifest/resource/historical maps are coherent.
- Empty r2 reports coverage `0/20`, `complete=false`, and
  `overall_gate=false` with all 20 cases missing.

`validate_frozen()` returned `h5_r2_frozen_inputs_valid`, but its lineage
validation is incomplete as described below.

## Blocking lineage defect

R2 does not preserve the required H4/H2 lineage. The actual H4 r1 manifest
SHA-256 is
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
and r1 records that value as its parent together with H2
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`.
R2 instead contains:

```json
{
  "parent_manifest_sha256": "c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd",
  "h4_manifest_sha256": "c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd",
  "h3_r10_manifest_sha256": "ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc",
  "h2_manifest_sha256": null
}
```

The direct-parent value is correct for r1, but it is not the H4 manifest hash.
The freeze code constructs `h4_manifest_sha256` from
`PARENT_MANIFEST_SHA256` and reads H2 from a nonexistent r1 top-level field,
rather than from r1's `lineage`. `validate_frozen()` checks only the direct
parent and therefore accepts this internally coherent but historically false
lineage. This violates the required H4 lineage and blocks offline acceptance.

## Focused H5 suite and r1 defect retests

Command:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_sol56_r2_retest_focused test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
```

Result: **18 passed in 1.66s**.

This confirms that the r1 checked-in `wrong_role` test defect is fixed and the
wrong parser role is rejected against the prepared exact envelope. The focused
suite also exercises the H5 lifecycle, one-repair boundary, terminal failure,
candidate fail-closed behavior, inherited document evidence, prompt loading,
and empty-run gate.

A separate disposable coherent-substitution probe changed only manifest
transport text and updated the inventory manifest hash. All entry points
rejected it:

```text
validator   ValueError: H5 r2 exact manifest SHA mismatch
coordinator ReplayInputError: coordinator rejects an unpinned exact H5 r2 manifest
lifecycle   ReplayInputError: lifecycle rejects an unpinned exact H5 r2 manifest
```

The parser prompt contains exactly one `Before returning JSON, self-check`
block. None of the 20 tuning case IDs occurs in the classifier or parser
prompt. The final parser wording is the only frozen prompt difference from r1;
classifier and repair remain byte-identical.

## Relevant regression suites and deterministic historical replay

Command:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_sol56_r2_retest_regression test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_subscription_h4_coordinator.py test_router_h3.py test_router_h3_r5.py test_router_h3_r6.py test_router_h3_r7.py test_router_h3_r8_provenance.py test_router_h3_r9_acceptance.py test_router_h3_r10_critical_identity.py test_router_draft_v2.py test_router_v2_replay_eval.py
```

Result: **12 failed, 128 passed in 9.08s**.

All 12 failures are the known stale H4 fixture debt: the H4 tests copy a run
that now contains 40 receipts, 40 bindings, and 40 request envelopes while the
tests assert an empty initial run. The failures are receipt-presence,
duplicate-role/attempt, or coordinator-initial-empty errors. No failure is in
the H5 tests or implementation. This debt does not mask the r2 lineage defect,
which is independently visible in the frozen r2 manifest and freeze source.

H2 and r10 were each replayed twice read-only. Each pair was byte-identical:

```text
H2  first technical 15/20, final technical 20/20, baseline 11/20, semantic 5/20
r10 final technical 20/20, semantic 13/20, classifier 17/20, status 16/20
```

The receipt-bearing H4 evidence remains the historical `20/20` technical,
`14/20` semantic plan, `19/20` classifier, and `19/20` status result. The stale
empty-run H4 tests cannot be rerun green against that same populated fixture;
their 12 failures are not evidence of an r2 behavioral regression.

## Compilation and allowlist

Command:

```text
python -m py_compile router_prompts_v3_h5.py router_subscription_lifecycle_h5.py prepare_router_subscription_h5.py freeze_router_v3_h5.py test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
```

Result: PASS (exit code 0).

The r2 implementation/test files represented in the frozen manifest and hash
inventory are confined to the H5 allowlist, and their actual hashes validate.
The workspace still contains pre-existing modified checkpoint and H4 receipt
files outside the H5 implementation allowlist, so a clean-tree global
allowlist claim is not possible. This retest did not modify them.

## Verdict

**FAIL.** R2 repairs all three defects reported by the r1 independent test:
focused tests pass, exact-SHA coherent substitution fails closed in validator,
coordinator, and lifecycle, and the parser has one self-check block. Identity,
20-input coverage, zero receipts, frozen hashes, empty fail-closed behavior,
historical H2/r10 replay, and compilation also pass. Acceptance remains blocked
because the immutable r2 manifest records the r1 manifest hash as the H4 hash
and drops the H2 lineage hash, while the validator does not detect either
error.
