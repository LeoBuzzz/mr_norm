# H4 r1 independent test — 2026-10-09

Verdict: **pass** for offline H4 r1 acceptance to the next review stage. No live LLM, Polza, holdout, Qdrant, production, or bot path was used. Product/frozen code and sampling artifacts were not modified.

Tested run: `router-v3-h4-tuning-20261009-r1` in `router_subscription_run_tuning_v3_h4_20261009`.

Verified immutable manifest SHA-256: `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`.

## Scope Read

Read H4 materials:

- `SPEC_QUESTION_PARSER_STAGE1.md`
- `DECISION_QUESTION_PARSER_STAGE1_H4_20261009.md`
- `CHECKPOINT_QUESTION_PARSER_STAGE1_V3_20261008.md`
- `freeze_router_v3_h4.py`
- `router_subscription_lifecycle_h4.py`
- `router_prompts_v3_h4.py`
- `test_router_h4_offline.py`

The H4 scope is narrow: prompt-only alias-versus-ambiguity policy plus lifecycle/freeze wiring. It targets the artificial clarification family V01/V03/V06/U03 and explicitly leaves D03 and genuine A02/A03 ambiguity out of the revision.

## Frozen Identity and Provenance

`validate_frozen(RUN_DIR)` returned:

- status: `h4_r1_frozen_inputs_valid`
- run_id: `router-v3-h4-tuning-20261009-r1`
- manifest_sha256: `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`
- case_count: `20`
- direct_parent_run_id: `router-v3-h3-tuning-20261009-r10`
- direct_parent_manifest_sha256: `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`
- historical snapshot inventory SHA: `437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`
- receipts_present: `false`

The manifest hash computed from `manifest.json` matched the required SHA. The H4 manifest pins r10-suffixed semantic replay `evaluate_router_v3_h3_semantic_replay_r10.py` at `cc7daff56d693ba654d18b2f646764a68612b13304429d414d678e2fe967f66d`; it does not pin the legacy generic `evaluate_router_v3_h3_semantic_replay.py`.

## Offline Commands

Passed:

```text
python -m pytest -q --basetemp=sandbox\skill_router_lab\.pytest_h4_independent_tmp sandbox\skill_router_lab\test_router_h4_offline.py sandbox\skill_router_lab\test_router_h3_r10_critical_identity.py
5 passed
```

Passed:

```text
python -m py_compile sandbox\skill_router_lab\router_prompts_v3_h4.py sandbox\skill_router_lab\router_subscription_lifecycle_h4.py sandbox\skill_router_lab\freeze_router_v3_h4.py sandbox\skill_router_lab\test_router_h4_offline.py
```

Passed:

```text
python -c "from freeze_router_v3_h4 import RUN_DIR, validate_frozen; validate_frozen(RUN_DIR)"
```

Parent r10 replay evidence:

```text
python sandbox\skill_router_lab\replay_router_h2_snapshot_r10.py
```

returned r10 manifest SHA `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`, snapshot inventory SHA `437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`, first technical `15/20`, final technical `20/20`, baseline `11/20`, and H2 semantic `5/20`.

```text
python sandbox\skill_router_lab\evaluate_router_v3_h3_semantic_replay_r10.py
```

returned deterministic r10 replay with technical `20/20`, semantic plan `13/20`, classifier `17/20`, status `16/20`, ambiguity `1/3`, unnecessary clarifications V01/V03/V06/U03, and no new critical regressions or critical ready losses. This confirms the H4 parent baseline and does not claim H4 model quality.

Expected overwrite refusal:

```text
python sandbox\skill_router_lab\freeze_router_v3_h4.py
```

raised `FileExistsError: reserved H4 r1 directory already exists`, which is correct for an immutable already-frozen r1 directory.

## Real Lifecycle Probes

I exercised `FileSubscriptionRunnerH4` on a disposable copy of the real frozen H4 run with injected offline dispatch responses. This verifies the real H4 lifecycle path, frozen prompt loading, receipt/envelope mechanics, validation, and assembly without live model calls.

Targeted alias-family probes:

- V01 accepted `classifier -> simple_parser`, final status `ready`, complexity `simple`, no clarification.
- V03 accepted `classifier -> simple_parser`, final status `ready`, complexity `simple`, no clarification.
- V06 accepted `classifier -> simple_parser`, final status `ready`, complexity `simple`, no clarification.
- U03 accepted `classifier -> complex_parser`, final status `deferred`, complexity `complex`, no clarification, task scope `unresolved`.

Negative ambiguity preservation:

- A02 accepted `classifier -> complex_parser`, final status `needs_clarification`, field `document`, two separate document options preserved. Scorer comparison: status/clarification/options matched, `plan_match=false` because the retained task assembled as `scope=corpus`, not unresolved. This is the known out-of-H4 issue described by the decision note.
- A03 accepted `classifier -> complex_parser`, final status `needs_clarification`, field `term`, three separate term options preserved. Scorer comparison: status/clarification/options matched, `plan_match=false` for the same retained `scope=corpus` reason.

This supports H4's intended distinction: ordinary alias/expansion cases can proceed without artificial clarification, while genuine competing documents/meanings remain clarifications and are not collapsed.

## Legacy H3 Setup Failure Investigation

Reproduced:

```text
python -m pytest -q --basetemp=sandbox\skill_router_lab\.pytest_h3_lifecycle_independent_tmp sandbox\skill_router_lab\test_router_h3_lifecycle.py
6 failed, 4 passed
```

All six failures stopped during setup at:

```text
ValueError: H3 executable hash mismatch: evaluate_router_v3_h3_semantic_replay.py
```

Hash evidence:

- current `evaluate_router_v3_h3_semantic_replay.py`: `fbadf279728308b32f29c90b41bf9bbab315dd40492c3ef01349570f91758aaa`
- legacy H3 r4 manifest expected `evaluate_router_v3_h3_semantic_replay.py`: `1cfb33d6d806ca6a6889717545258e9cb1c96db123b973ad7b83c0aa40abc85a`
- H4 manifest pins `evaluate_router_v3_h3_semantic_replay_r10.py`: `cc7daff56d693ba654d18b2f646764a68612b13304429d414d678e2fe967f66d`

Conclusion: the six H3 setup failures are a pre-existing legacy validator/hash drift for the generic H3 replay file. H4 did not change or depend on that generic pinned file, and this does not block H4 r1 offline acceptance.

## Broader Legacy Suite Note

A broader mixed run produced 40 passed and 15 failed across r10 coordinator and v3 legacy tests. The failures are outside H4: r10 coordinator tests cloned a receipt-bearing run where empty receipts were expected, and v3 tests hit pre-existing `router_subscription_lifecycle_v3.py` frozen-code hash drift or receipt-bearing fixtures. I did not treat those as H4 blockers because H4-specific validation and real H4 lifecycle probes use the H4 frozen manifest and passed.

## Final Verdict

**pass**. H4 r1 is a valid immutable offline prompt/wiring revision over H3 r10, with correct parent provenance, no receipts, intact frozen inputs/schema/evaluation resources, targeted alias behavior exercisable through the real lifecycle, and preserved A02/A03 ambiguity. No H4 acceptance blocker found.

## P1 Lifecycle Test Correction Verification

2026-10-09 independent verification of the P1 test-coverage correction:
**pass**.

Reviewed `test_router_h4_lifecycle_boundaries.py` against
`CRITIQUE_QUESTION_PARSER_H4_R1_20261009.md` and H4.4 in
`SPEC_QUESTION_PARSER_STAGE1.md`. The checked-in tests now exercise
`FileSubscriptionRunnerH4` and the real assembler on disposable copies of the
frozen H4 run with injected offline dispatch outputs. They are not limited to
prompt substring checks.

Coverage confirmed:

- V01/V03/V06 dispatch through `classifier -> simple_parser`, validate as
  `ready`, have no clarification, preserve the request concept, and retain
  object/quantity/exclusion constraints including an exclusion polarity.
- U03 dispatches through `classifier -> complex_parser`, validates as
  `deferred`, has no clarification, and carries `unsupported_intent`.
- A02 and A03 preserve exact supplied ambiguity options for document and term
  clarification respectively.
- Unknown document/term IDs fail closed after the single allowed repair attempt.
- Invalid parser output dispatches at most one repair and remains terminal on a
  second invalid response.
- Frozen validation/provenance remain covered by `test_router_h4_offline.py`;
  `validate_frozen(RUN_DIR)` still reports manifest SHA-256
  `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
  direct parent `router-v3-h3-tuning-20261009-r10` at
  `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`,
  20 cases, and no receipts.

Commands:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_tmp_h4_boundaries_verify test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_h3_r10_critical_identity.py
14 passed in 0.92s

python -m pytest -q -p no:cacheprovider --basetemp .pytest_tmp_h4_verify test_router_h4_lifecycle_boundaries.py test_router_h4_offline.py
11 passed in 0.87s

python -m py_compile freeze_router_v3_h4.py router_subscription_lifecycle_h4.py router_prompts_v3_h4.py test_router_h4_lifecycle_boundaries.py test_router_h4_offline.py
passed

python -c "from freeze_router_v3_h4 import RUN_DIR, validate_frozen; print(validate_frozen(RUN_DIR))"
status=h4_r1_frozen_inputs_valid, 20 cases, no receipts
```

An initial pytest attempt without `--basetemp` failed during fixture setup with
`PermissionError` scanning the host temp directory
`C:\Users\buleo\AppData\Local\Temp\pytest-of-l_buzz`; no H4 test logic ran in
that attempt. The successful commands above use a writable workspace basetemp.
No live LLM, sampling, Polza, holdout, Qdrant, production, bot path, frozen
artifact modification, or implementation modification was used.
