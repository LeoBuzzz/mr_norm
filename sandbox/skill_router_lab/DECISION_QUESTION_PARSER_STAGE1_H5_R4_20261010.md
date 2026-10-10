# H5 r4 — correction decision after independent r3 critique

2026-10-10. Verdict: **spec_ready** for bounded offline implementation only.
R3 remains **changes_required**. Sampling is prohibited. This decision creates
no acceptance anchor and grants no model dispatch authorization.

The three P1 findings are substantiated by the r3 files. The exact manifest SHA
is supplied by an unpinned freeze module; patching that module defeats the
claimed manifest boundary. The combined regression result is 12 failed/129
passed, so the report's scoped PASS cannot satisfy H5.4. The D03 assertion checks
prompt text without executing the required condition/scorer contrast.

H5.6, appended to SPEC_QUESTION_PARSER_STAGE1.md, is normative. It closes these
findings without reducing semantic, critical, technical or provenance gates:

1. Use an externally retained reviewer/acceptance record containing exact
   manifest, release-lock and launcher SHA-256. A trusted host verifies the
   launcher before executing it; that stdlib-only launcher verifies the lock,
   implementation closure and evidence before candidate imports. Validator,
   coordinator and lifecycle are all covered. Code never authenticates itself;
   no embedded manifest-hash/code-hash fixed point is required. A local copy of
   acceptance is not the authority. The trusted host, interpreter/dependencies,
   external review record and no-concurrent-mutation assumption are explicit.
   Coherent substitution of manifest, inventory, candidate constants/code/lock
   or launcher must fail against the unchanged external anchor before side effects.
2. Permit a fixture-only refactor of the three named H4 test files. Keep every
   existing test and behavioral assertion; remove the three mutable evidence
   streams only from verified disposable copies for synthetic tests. Validate
   original populated H4 evidence by exact hashes and 40/40/40 records, retain
   duplicate/nonempty-start refusals, and archive original test bytes. No test
   deletion, skip, xfail, deselection or PASS exception is authorized.
3. Require D03 positive/negative injected drafts through the actual r4 lifecycle,
   unchanged assembler and existing compare_case scorer. A preserved scope
   condition/topic/evidence passes; omission of only the condition remains
   technically valid but fails semantic scoring. Task-local historical-source
   omission also fails. Execute D04 and correction/new-request contrasts; prompt
   substring tests provide no substitute. A required runtime redesign returns
   changes_required rather than widening this patch.

The P2 preservation finding is addressed by externally anchored full-tree
inventories for r1/r2/r3/H4, checked at every release preflight. Old r1/r2 manifests
and inventories remain untouched, including their erroneous lineage fields.
The report must distinguish archived verified originals from unavailable old
live-code generations: current r3 files cannot authenticate historical r1/r2
code hashes. Those rejected revisions are historical data, not executable trust
roots. All runtime actually executed for r4 or historical replay must have
available, pinned bytes. Current r3 code/tests and pre-refactor H4 tests are
archived before implementation. No original run is rewritten.

The historical H4 replay becomes a versioned, pinned offline adapter with an
exact CLI, explicit binding map, full canonical output and two fresh-process
digests. It may bind existing r10 functions to H4 identity/validation/assembly;
it may not change scorer logic. It must reproduce H4 technical20/semantic14/
classifier19/status19 and real failures, with B11, H2 semantic5/technical15→20
and r10 semantic13/technical20 also reproduced twice. Reproducibility is a gate,
not a score forecast or new model-quality result.

The new identity is `router-v3-h5-tuning-20261010-r4` in
`router_subscription_run_tuning_v3_h5_r4_20261010`. Its direct parent is r3 SHA
`2617500135233dd4d0903e8e6c3bda7e668acc55d993f3d6fe64ff17f428bdf7`.
H5.6.4 records exact r2/r1/H4/r10/H2 hashes and corrected actual ancestry. Inputs,
order, prompts, schema, evaluation resources and H2 snapshot stay byte-identical
to r3. There are no receipts, request envelopes or bindings in the offline r4 run.

Exact expanded allowlist (lab-relative):

- Create `freeze_router_v3_h5_r4.py`, `router_subscription_lifecycle_h5_r4.py`,
  `prepare_router_subscription_h5_r4.py`, `release_router_h5_r4.py`,
  `replay_router_h4_for_h5_r4.py` for the specified bindings/trust boundary only.
- Create `test_router_h5_r4_offline.py`, `test_router_h5_r4_lifecycle_boundaries.py`,
  `test_router_subscription_h5_r4_coordinator.py`, `test_router_h5_r4_trust.py`.
- Refactor only fixtures/the stale empty assertion in `test_router_h4_offline.py`,
  `test_router_h4_lifecycle_boundaries.py`, `test_router_subscription_h4_coordinator.py`.
- Create the r4 run directory with frozen resources/manifest/hash_inventory,
  release_lock.json and the exact evidence subpaths specified by H5.6.5.
- Append the spec; create this decision and the named r4 independent-test,
  critique and Astra acceptance documents in H5.6.5. Coordinator may separately
  append its previously authorized checkpoint. No earlier evidence is edited.

Every other existing implementation/test/prompt/scorer/gold/runtime/schema file
is excluded. Versioned r4 files preserve r3 live-code pins. The full r3 regression
selection plus all new r4 suites must pass without exclusions. Tests PASS,
critique PASS and independent Astra accept_offline for the exact manifest/lock/
launcher triple are all required. Luna medium implements, Sol 5.5 medium tests,
Sol 5.6 medium critiques, Astra medium accepts; unavailable roles are blockers.

This specification role changed only this decision and appended H5.6. No tests,
runtime, prompt, frozen artifact, receipt, report or checkpoint were changed.
No sampling, Polza, holdout, Qdrant, production or bot action was performed.
