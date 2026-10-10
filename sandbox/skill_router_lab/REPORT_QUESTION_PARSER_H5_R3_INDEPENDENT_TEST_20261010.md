# H5 r3 independent test — 2026-10-10

Verdict: **PASS for the H5 r3 offline identity, lineage, prompt, lifecycle, and replay checks.** The combined legacy regression command is not green: 12 H4 tests retain an empty-run fixture assumption after H4 acquired receipts. This is proven fixture debt, not an r3 failure; it remains a literal exception to the H5.4 all-suite-PASS wording and must not be represented as a green combined suite. This report is not model-quality evidence or `accept_offline`.

Tested `router-v3-h5-tuning-20261009-r3` at `router_subscription_run_tuning_v3_h5_r3_20261009`. This role did not modify implementation, prompts, frozen runs, manifests, specs, decisions, or checkpoint. No live LLM, sampling, Polza, holdout, Qdrant, production, bot, or network use. Disposable negative-test copies were made and removed under the lab directory; this report is the only persistent artifact.

## Frozen identity and inherited bytes

From `sandbox/skill_router_lab`, a read-only Python hash/inventory check and `validate_frozen()` passed. Actual manifest SHA-256 values:

| Run | SHA-256 |
| --- | --- |
| H5 r3 | `2617500135233dd4d0903e8e6c3bda7e668acc55d993f3d6fe64ff17f428bdf7` |
| Direct H5 r2 parent | `0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194` |
| H5 r1 | `c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd` |
| Actual H4 | `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa` |
| H2 | `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862` |
| H3 r10 | `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc` |

The r3 manifest has the exact run ID and records r2 as direct parent plus the actual H4, H2, and r10 hashes. All r3 frozen resources are byte-identical to r2. All r2 resources except its parser prompt are byte-identical to r1. Every r1/r2 frozen resource and historical runtime file still matches its own manifest hash inventory; their manifest hashes equal the values above and neither has receipts, envelopes, or bindings. `validate_frozen()` checked r3 implementation, test inventory, frozen resources, historical runtime, case order, and exact manifest. Its result was `h5_r3_frozen_inputs_valid`, 20 cases, `receipts_present=false`. R3 has no receipt, envelope, or binding JSONL. `empty_replay_summary()` returned coverage `0/20`, `complete=false`, `overall_gate=false`, and 20 missing cases.

The classifier and parser contain none of the 20 tuning case IDs. The parser contains exactly one `Before returning JSON, self-check` block. Frozen classifier, parser, and repair prompts are byte-identical to r2. The focused tests cover synthetic alias/ambiguity, inherited source, restrictions, correction/continuation, technical validation, one repair, terminal failure, wrong role, and tamper cases; these injected-output tests do not show how a model will respond.

## Exact manifest and lineage negatives

Command: a read-only `python -` script used `TemporaryDirectory(dir=HERE)`, `shutil.copytree(RUN_DIR, temporary_case)`, changed only `manifest.json` transport text or one lineage field, and coherently updated `hash_inventory.json.manifest_sha256`. For each of six variants (transport plus `parent_run_id`, `parent_manifest_sha256`, `h4_manifest_sha256`, `h2_manifest_sha256`, `h3_r10_manifest_sha256`), `validate_frozen`, `H5SubscriptionCoordinator`, and `FileSubscriptionRunnerH5` all rejected the copy: **18/18 expected rejections**. The validator reported `H5 r3 exact manifest SHA mismatch`; coordinator and lifecycle rejected the unpinned exact manifest.

To exercise the lineage checks behind that exact pin, the same script was rerun for all five lineage fields with the three imported `EXPECTED_MANIFEST_SHA256` constants temporarily patched in process to each substituted SHA. Inventory remained coherent. Every entry point then rejected with `H5 r3 lineage mismatch: <field>`: **15/15 expected lineage rejections**. These patches were in process only; no tracked or frozen file changed. The focused coordinator tests independently cover resource substitution and wrong parser role binding.

## Tests and historical replay

Commands and results, from `sandbox/skill_router_lab`:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_r3_independent_focused test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
19 passed in 2.02s

python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_r3_independent_regression test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_subscription_h4_coordinator.py test_router_h3.py test_router_h3_r5.py test_router_h3_r6.py test_router_h3_r7.py test_router_h3_r8_provenance.py test_router_h3_r9_acceptance.py test_router_h3_r10_critical_identity.py test_router_draft_v2.py test_router_v2_replay_eval.py
12 failed, 129 passed in 9.86s

python -m py_compile router_prompts_v3_h5.py router_subscription_lifecycle_h5.py prepare_router_subscription_h5.py freeze_router_v3_h5.py test_router_h5_offline.py test_router_h5_lifecycle_boundaries.py test_router_subscription_h5_coordinator.py
exit 0
```

All 12 combined-suite failures are in H4 tests. One asserts `receipts_present is False`; the other 11 start copied H4 runs and encounter duplicate role/attempt or nonempty-initial-receipt refusals. I independently counted **40 receipts, 40 pre-dispatch envelopes, and 40 bindings** in the actual H4 run. Thus the tests' assumed empty fixture is false before any r3 execution. The 129 passing tests include all 19 H5 tests. Python emitted a nonfatal `Failed to find real location` warning; commands executed normally.

H2 and H3 r10 were replayed twice using `replay_h2_snapshot()` and `semantic_replay_h3()`, comparing `canonical_json_bytes` of each pair. H4 was replayed twice read-only by binding the r10 technical and semantic replay functions in process to the H4 validator, run identity, and H4 `validate_raw_h3`/`assemble_plan_h3`; its historical H2 reference remained pinned. Every pair was byte-identical:

```text
H2: first technical 15/20, final technical 20/20, baseline 11/20, semantic 5/20
r10: final technical 20/20, semantic 13/20, classifier 17/20, status 16/20
H4: final technical 20/20, semantic 14/20, classifier 19/20, status 19/20
```

The historical outputs include their real failures. No H5 semantic score is claimed; r3 has zero receipts. The older H4 fixture debt is the only observed regression-suite exception. Exact r3 hashes, corrected lineage, negative gates, focused suite, compilation, and deterministic historical replays pass.
