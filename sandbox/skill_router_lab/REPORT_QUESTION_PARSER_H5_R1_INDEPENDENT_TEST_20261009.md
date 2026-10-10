# H5 r1 independent test — 2026-10-09

Verdict: **FAIL**.

Tested run: `router-v3-h5-tuning-20261009-r1` in
`router_subscription_run_tuning_v3_h5_r1_20261009`.

No live LLM, sampling, Polza, holdout, Qdrant, production, or bot path was
used. No implementation, prompt, frozen run, manifest, specification, or
checkpoint file was changed by this test role.

## Runtime

Command:

```text
python -c "import sys,pytest; print(sys.executable); print(sys.version); print(pytest.__version__)"
```

Result: executable `C:\Users\buleo\miniconda3\python.exe`, Python `3.13.11`,
pytest `9.0.3`. The launcher printed a non-fatal `Failed to find real location`
warning, but both pytest and direct Python checks executed normally.

## Frozen identity, lineage, and empty fail-closed

Command:

```text
python -c "import hashlib,json; from pathlib import Path; from freeze_router_v3_h5 import RUN_DIR,validate_frozen,empty_replay_summary; x=validate_frozen(RUN_DIR); p=RUN_DIR.parent/'router_subscription_run_tuning_v3_h4_20261009'; mh=hashlib.sha256((RUN_DIR/'manifest.json').read_bytes()).hexdigest(); ph=hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest(); e=empty_replay_summary(RUN_DIR); same=[r for r in ['inputs/input_manifest.jsonl','source_manifests.json','schema.json','prompts/repair.txt','evaluation/router_plan_cases_tuning_v2.json','evaluation/baseline_reconciled_v3_20261008.json','evaluation/h2_manifest.json','evaluation/h2_parent_manifest.json','evaluation/v2_manifest.json'] if (RUN_DIR/r).read_bytes()==(p/r).read_bytes()]; assert mh=='c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd'; assert ph=='4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa'; assert len(same)==9; assert e['coverage']==0 and e['denominator']==20 and not e['complete'] and not e['overall_gate']; print(json.dumps({'validation':x,'manifest':mh,'parent':ph,'byte_identical_parent_resources':same,'empty':e},ensure_ascii=False))"
```

Result: PASS for the checked-in bytes. H5 manifest SHA-256 is
`c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd`;
direct H4 parent manifest SHA-256 is
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`;
20 cases are present; H5 receipts are absent. The nine inherited input,
source/schema/repair/evaluation resources listed in the command are byte
identical to H4. Empty H5 reports coverage `0/20`, `complete=false`, and
`overall_gate=false`.

Command:

```text
python freeze_router_v3_h5.py
```

Result: expected immutable overwrite refusal,
`FileExistsError: immutable H5 r1 already exists`.

## Focused H5 pytest

Command:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_sol56_independent_focused test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
```

Result: **1 failed, 16 passed in 2.25s**. This is not a pytest PASS.

The failure is a checked-in test defect in
`test_router_subscription_h5_coordinator.py:82`: the test references
`wrong_role["envelope_sha256"]`, but `wrong_role` is never assigned. No H5
implementation assertion runs for that part of the test.

The resource-substitution half of the same test did execute and rejected a
modified parser prompt. A separate direct coordinator probe imported all 20
classifier finals, prepared parser envelopes, then submitted the S01
`simple_parser` envelope hash under `complex_parser`. Result: expected
`ReplayInputError: raw final does not bind to a prepared exact envelope`.
This direct probe confirms role drift fails closed, but it does not convert the
broken checked-in pytest into a PASS.

## Relevant H4/H3/r10 pytest regression

Command:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_sol56_independent_regression test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_subscription_h4_coordinator.py test_router_h3.py test_router_h3_r5.py test_router_h3_r6.py test_router_h3_r7.py test_router_h3_r8_provenance.py test_router_h3_r9_acceptance.py test_router_h3_r10_critical_identity.py test_router_draft_v2.py test_router_v2_replay_eval.py
```

Result: **12 failed, 124 passed in 8.03s**. This is not a pytest PASS.

All 12 failures are in H4 tests that assume an unreceipted H4 run. The current
H4 directory contains 40 receipt rows and 40 binding rows. The first failure is
`assert frozen["receipts_present"] is False`; lifecycle/coordinator failures
then reject duplicate role/attempts or non-empty initial receipts. This is an
existing parent-run state/fixture mismatch, not an H5 runtime exception, but it
means the required previously passing regression suite is not green on the
current workspace.

## Historical deterministic replay

Command:

```text
python -c "from router_subscription_eval import canonical_json_bytes; from replay_router_h2_snapshot_r10 import replay_h2_snapshot; from evaluate_router_v3_h3_semantic_replay_r10 import semantic_replay_h3; h1=replay_h2_snapshot(); h2=replay_h2_snapshot(); r1=semantic_replay_h3(); r2=semantic_replay_h3(); assert canonical_json_bytes(h1)==canonical_json_bytes(h2); assert canonical_json_bytes(r1)==canonical_json_bytes(r2); print('H2 deterministic PASS:',h1['technical']['first_technical_valid'],h1['technical']['final_technical_valid'],h1['semantic']['baseline_reconciled']['matched'],h1['semantic']['metrics']['semantic_plan']['matched']); print('r10 deterministic PASS:',r1['metrics']['technical']['final_technical_valid'],r1['metrics']['semantic_plan']['matched'],r1['metrics']['classification']['correct'],r1['metrics']['status']['matched'])"
```

Result:

```text
H2 deterministic PASS: 15 20 11 5
r10 deterministic PASS: 20 13 17 16
```

H4 was replayed twice read-only by adapting the r10 replay functions to the H4
run identity and H4 frozen validator while retaining the pinned historical H2
reference. The two canonical results were byte-identical. Result:

```text
H4 deterministic PASS: 20 14 19 19
```

These values are final technical, semantic plan, classifier, and status counts.

## Compilation

Command:

```text
python -m py_compile router_prompts_v3_h5.py router_subscription_lifecycle_h5.py prepare_router_subscription_h5.py freeze_router_v3_h5.py test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
```

Result: PASS (exit code 0).

## Implementation and test defects

1. **Exact-manifest tamper is not pinned by the H5 coordinator.**
   `prepare_router_subscription_h5.py:61` checks only the run ID. H4 had an
   `EXPECTED_MANIFEST_SHA256` comparison; H5 removed it. On a disposable copy I
   changed only `manifest.json` transport text and updated
   `hash_inventory.json.manifest_sha256`. Both `validate_frozen()` and
   `H5SubscriptionCoordinator()` accepted the substituted manifest SHA
   `c91ef2f3d5de8b12ec89b64f028069592159806b2f886bf1a4bd8ad864e51471`.
   This violates the exact-manifest/tamper gate even though the checked-in H5
   manifest currently has the requested SHA.

2. **The checked-in coordinator test is broken.**
   `test_router_subscription_h5_coordinator.py:82` uses undefined
   `wrong_role`, producing `NameError` rather than testing the intended
   fail-closed condition.

3. **The bounded parser self-check is duplicated verbatim.**
   `router_prompts_v3_h5.py:14` contains the full sentence beginning `Before
   returning JSON, self-check` twice in succession. H5.2 permits one short
   concluding self-check block. The focused prompt test checks presence only,
   so it does not detect the duplicate.

## Allowlist

The H5 implementation paths themselves are within the H5.3 allowlist: four
adapters, three tests, and the new H5 frozen directory. The H5 decision and
append-only specification section are permitted review/spec documents.

The workspace as a whole does not satisfy a clean exact allowlist check:
`git diff --name-only` also reports the pre-existing modified checkpoint and
H4 receipt/binding files, including paths outside the H5 implementation
allowlist. This test role did not create or edit those files. Given that state,
global absence of out-of-allowlist changes cannot be certified from this
working tree.

## Verdict

**FAIL.** The exact checked-in H5 manifest and parent hashes validate, empty H5
fails closed, compilation passes, H2/r10/H4 replays are deterministic, and 140
pytest tests passed across the two runs. Acceptance is blocked by the focused
H5 pytest failure, the reproducible coherent manifest-substitution bypass, the
duplicated prompt self-check, and the non-green H4 regression suite in the
current receipt-bearing workspace.
