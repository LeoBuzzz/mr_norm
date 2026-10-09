# Independent critique: stage 1 H3 implementation/freeze

Date: 2026-10-08  
Scope: H3 implementation and freeze only; no sampling, holdout, Polza, Qdrant,
production, or bot calls.  
Reviewed run: `router-v3-h3-tuning-20261008-r2`  
Reviewed manifest SHA-256:
`29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3`.

## Verdict

**changes_required**. H3 must not be sampled yet.

The isolated type guard and H3 prompts implement the intended field contract,
but the frozen run has no executable H3 lifecycle/replay path. The only current
subscription lifecycle still sends the H2 parser/repair prompts and validates
with the H2 validator, so it bypasses `topic_literal_guard.v1`.

## Findings

### P1 — the executable lifecycle bypasses the H3 prompt and validator

`router_subscription_lifecycle_v3.py:11-12` imports `_validate_raw` from the H2
evaluator and prompts from `router_prompts_v3`. Its first and repair validation
at lines 143 and 155 therefore call the legacy validator. Nothing in that
module reads `manifest.validation_policy` or calls `validator_for_policy`.
`router_validation_policy.py` is referenced only by the H3 unit test and freeze
inventory; no lifecycle or replay evaluator uses it.

An offline probe using a temporary copy of the exact H3 r2 run returned:

`{"status":"valid","accepted_topic":"current@2:2","sent_frozen_h3_prompt":false}`

Thus a locator reaches the accepted semantic draft, no repair is requested,
and the dispatched parser system message differs from the prompt whose hash is
recorded in the H3 manifest. This directly violates SPEC sections 2.2, 2.3,
4.3, 4.8, and the pre-sampling abort conditions.

Minimal correction: add a versioned H3 lifecycle path that loads the frozen H3
prompt bytes and selects `topic_literal_guard.v1` from the manifest for both
attempts. The corresponding H3 replay must reconstruct those same envelopes
and use the same selected validator. Regression tests must exercise that real
lifecycle, proving locator attempt 1 causes exactly one repair, repeated locator
fails, literal topic stays byte-identical, and the frozen prompt is dispatched.

### P1 — the H3 freeze does not contain or validate an H3 replay/lifecycle

`freeze_router_v3_h3_run.py:69-70` freezes only the prompt module, guard,
selector, and freeze script as H3 executable code. It contains neither an H3
lifecycle nor an H3 technical/semantic replay evaluator. Consequently the
mandatory empty H3 replay, envelope reconstruction, tamper rejection, observed
classifier role selection, repair binding, and H3 denominators/gates cannot be
run. `test_router_h3.py` tests the guard directly, not the actual sampling or
replay path.

The same validator also does not anchor the manifest's `run_id`, parent run ID,
parent manifest hash, superseded r1 ID, prompt bytes, or byte equality of all H2
inputs back to their expected sources. It returns `receipts_present=False`
unconditionally. The currently reviewed artifact has the correct values and
hash, but the validation code is not fail-closed against provenance drift as
required before sampling.

Minimal correction: freeze and hash the versioned H3 lifecycle and replay
modules plus their complete dependencies. Make `validate_frozen` verify the
fixed H3 r2 identity, exact H2 parent identity/hash, exact byte parity for input,
source manifests, schema and unchanged classifier prompt, exact H3 prompt
bytes, and actual receipt presence. Add the inherited r2 provenance/tamper and
empty-replay tests against the H3 entry points.

## Confirmed correct in the reviewed area

- `router_draft_h3.py` uses the normative locator regex and raises stable
  `TOPIC_MUST_BE_LITERAL_TEXT task N` before delegating to the old validator.
  It performs no dereference, fallback, rewrite, or semantic repair.
- Representative literal topics with numbers, units, negation, punctuation,
  colon, and ordinary email syntax pass unchanged. No hidden literal-topic
  false rejection was found relative to the normative regex.
- Locator forms covering valid, unknown, out-of-range, embedded, quoted, and
  multiple references are rejected by the isolated H3 guard.
- Parser and repair prompt changes match the type-contract hypothesis; the
  classifier prompt and schema hashes remain equal to H2.
- H3 r2 manifest and frozen inputs have the stated hashes. H2 manifest hash is
  `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862` and
  H2 receipts hash is
  `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`.
  Read-only H2 replay still reports 45 receipts, first technical 15/20 and final
  technical 20/20.
- Focused H3 plus legacy draft tests: 57 passed. A broader H2 v3 test invocation
  includes tests that still assume the now-sampled H2 directory is empty; those
  failures are test-fixture assumptions and do not close the missing H3
  lifecycle/replay blocker.

## R3 follow-up critique — 2026-10-08

Reviewed run: `router-v3-h3-tuning-20261008-r3`; manifest SHA-256
`509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5`.

Verdict remains **changes_required**. R3 correctly fixes the intended technical
entry point, but the two original P1 areas are not fail-closed end to end.

### Closed portion

`FileSubscriptionRunnerH3` validates the r3 freeze before dispatch, reads the
three frozen prompt files, selects `topic_literal_guard.v1`, applies it to both
initial and repair output, and permits only one repair. `replay_h3` reconstructs
classifier/parser/repair envelopes from frozen input and prompt bytes, binds the
repair payload to the actual policy validation error, validates receipt/binding
hashes, and reports an empty run as incomplete with fixed denominator 20 and a
failed overall gate. Coherently rehashed request and repair-error tampering are
rejected. The r3 freeze anchors run/parent identity, superseded r1/r2 IDs,
prompt bytes, exact H2 input/source/schema bytes, code hashes, and actual receipt
presence. Focused H3 tests pass 28/28.

### P1 — inherited public lifecycle remains a write bypass for H3 r3

The public `FileSubscriptionRunnerV3` still accepts the H3 r3 directory because
it does not reject `validation_policy=topic_literal_guard.v1`. It sends H2
prompts and calls H2 `_validate_raw`, while writing receipts carrying the r3 run
ID. An offline temporary-copy probe using the exact r3 manifest returned:

`{"base_runner_status":"valid","accepted_topic":"current@2:2","used_frozen_h3_parser":false}`

Thus an available entry point can contaminate the frozen H3 run before the H3
replay later rejects it. The requirement is fail-closed before dispatch, so
post-hoc replay rejection is insufficient.

Minimal correction: make every non-H3 lifecycle entry point refuse a manifest
with the H3 validation policy/run identity before `_call` can persist or
dispatch anything, while preserving historical H2 replay through its frozen
runtime snapshot. Add a regression test that points the inherited runner at a
temporary r3 copy and asserts rejection before an envelope is written.

### P1 — no frozen H3 assembly/semantic replay path exists

The tested r3 chain stops at `semantic_draft`. `FileSubscriptionRunnerH3`
returns the validated draft, and `evaluate_router_v3_h3_replay.py` computes only
technical validity. It never calls `assemble_skill_query_plan`; its
`semantic_gate` remains hard-coded `not_evaluated_without_receipts` even for
synthetic runs with receipts. The only semantic replay in the repository is the
H2 evaluator, which imports H2 `_validate_raw` and H2 technical replay. No H3
semantic/assembly evaluator is included in r3 `code_files_sha256`.

Therefore the requested real chain `lifecycle -> H3 guard -> assembly -> replay`
is neither executable nor frozen, and an assembler regression or locator
leakage through a future H2-based evaluator would not be caught before sampling.

Minimal correction: add and freeze an H3 semantic replay that consumes only
`replay_h3`-validated final drafts, assembles with the unchanged assembler, uses
the unchanged scorer/audit maps, and proves a locator-invalid draft never
reaches assembly while a literal draft produces the byte-identical legacy plan.
Its code/dependencies and deterministic tests must be in the next immutable run.

H2 artifacts remain unchanged: manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862` and
receipts SHA-256
`184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`.
No live or external sampling was performed.

## R4 follow-up critique — 2026-10-08

Reviewed run: `router-v3-h3-tuning-20261008-r4`; manifest SHA-256
`5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`.

Verdict: **changes_required**. R4 closes the two r3 runtime gaps, but historical
replay and the newly frozen H3 gates still contain P1 defects.

### Confirmed closed

- The inherited `FileSubscriptionRunnerV3` now rejects an H3 run identity,
  directory, or validation policy before envelope persistence or dispatch. The
  repeated temporary-copy bypass probe produced zero dispatches and no envelope.
- `FileSubscriptionRunnerH3` sends exact frozen prompts, validates initial and
  repair output with `topic_literal_guard.v1`, allows one repair, and assembles
  only the validated final draft. The invalid locator draft does not reach the
  assembler.
- Technical replay reconstructs receipts/envelopes/repair errors, validates
  tamper bindings, revalidates the selected final draft, and assembles the same
  plan. The H3 semantic replay uses the frozen reconciled scorer and baseline,
  produces per-case plans/scores and two byte-identical substantive replays.
- R4 identity, r3/H2 lineage, prompts, inputs/schema, evaluator data and changed
  code are included in the frozen inventory. Focused H3 tests pass 30/30.

### P1 — shared lifecycle evolution broke executable historical H2 replay

Changing `router_subscription_lifecycle_v3.py` is acceptable in principle only
if H2 replay is switched to its immutable runtime snapshot without weakening
the original hash/tamper validation. That switch is not implemented.

The ordinary H2 `validate_frozen()` now fails with `frozen code hash mismatch:
router_subscription_lifecycle_v3.py`; the relevant H2 immutability tests fail.
R4 merely special-cases that one live-file mismatch in its own validator and
copies the historical source. Running the snapshotted
`evaluate_router_v3_file_replay.py` against the real H2 run fails because its
snapshotted `freeze_router_v3_run.py` resolves the v2 source run beneath the
snapshot directory, where it does not exist. Therefore neither the live path
nor the snapshot path currently reproduces the historical H2 15/20 -> 20/20
gate with its original tamper checks.

This is P1, not harmless evolution: artifacts/receipts are byte-identical, but
the specification requires executable historical replay before sampling.

Minimal correction: provide and test a snapshot-isolated H2 replay launcher
whose module/resource paths explicitly bind the immutable snapshot code to the
original frozen H2/v2 artifacts. It must reproduce the historical report and
the original coherent-tamper negatives. Do not relax H2 manifest code hashes or
silently substitute the changed working-tree lifecycle.

### P1 — H3 useful/full gates do not implement all frozen criteria

`evaluate_router_v3_h3_semantic_replay.py:90` defines `h3_useful` as only
`plan_match_count >= 15 and no_new_critical_regressions`. SPEC section 6 also
requires first technical >=19/20, final technical 20/20, complete coverage, and
zero locator-in-query in accepted plans. Those conditions are omitted from the
reported top-level `useful` decision.

The strict `full_tuning_gate_pass` includes technical thresholds but only checks
*new* critical regressions relative to B. It does not enforce the stated zero
ready plans with lost intent, negation, number/unit, wrong document, unsupported
intent, or dependent chain. Up to two inherited critical semantic failures can
coexist with the 18/20 plan threshold and still pass this Boolean gate.

Minimal correction: compute named booleans for coverage, locator-free accepted
plans, all H3 useful technical thresholds, and the strict zero-critical-ready
categories. Include them in the frozen metrics and require them explicitly in
`useful`/`full_tuning_gate_pass`; add negative tests where semantic 15/20 or
18/20 alone would otherwise produce a false pass.

H2 manifest and receipt hashes remain byte-identical to their published values.
No live/model/holdout/external sampling was performed.

## R7 follow-up critique — 2026-10-09

Reviewed run: `router-v3-h3-tuning-20261009-r7`; manifest SHA-256
`53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`.

Verdict: **changes_required**. The gate false-pass defect is closed, but the H2
snapshot replay launcher is not actually bound to r7's frozen snapshot and
resources.

### Confirmed closed: named useful/full gates and task attribution

`compute_h3_gates` now requires complete 20-case coverage, first technical
>=19/20, final technical 20/20, locator-free accepted plans, semantic >=15/20,
and no new critical regressions for `useful`. The strict gate additionally
requires classifier >=18/20, all 11 expected complex routes, semantic >=18/20,
status >=18/20, all three ambiguities, V03/V06, and zero prohibited ready-plan
losses. Each required Boolean is named in the report.

The ready-loss audit accepts integer and serialized assignment keys, treats an
unmatched expected task as unassigned, and catches the specified intent,
negation, number/unit, document and unsupported/dependent categories. Focused
r7 tests pass 12/12, including coherent system/user tamper negatives and the
actual lifecycle -> assembly -> receipt replay path.

### P1 — snapshot launcher is stale and consumes mutable evaluator resources

`replay_router_h2_snapshot.py:17-18,57-65` defaults to the r5 directory and
validates the r5 manifest, not the reviewed r7 manifest or r7 historical
snapshot. The observed successful replay reports its snapshot directory as
`router_subscription_run_tuning_v3_h3_r5_20261009/historical_h2_runtime`.
Deleting or changing that superseded run breaks r7 historical replay even
though r7 contains its own snapshot.

The child process loads executable modules from that snapshot, which correctly
avoids changed checkout code. However, lines 36 and 41-42 pass the working-tree
artifact directory to the snapshotted semantic evaluator for gold and baseline.
The launcher never verifies those resource bytes against r7
`evaluation_data_sha256`; it only checks aggregate B=11/H2=5 afterward. A
coherent resource change that preserves those aggregates is not rejected. The
v2 manifest is likewise checked only by run ID rather than a pinned expected
hash.

Therefore current 15/20 -> 20/20, B=11 and H2=5 reproduction plus tamper tests
are useful evidence, but do not prove the exact r7 snapshot/resources were
loaded or that checkout resource drift fails closed.

Minimal correction: version the launcher for r7, bind it to r7 manifest SHA and
`r7/historical_h2_runtime`, and use frozen copies of gold/baseline/v2 resources
inside the r7 artifact (or verify their exact r7-manifest hashes before the
child starts). Pin the v2 manifest hash. Add negatives for superseded-r5
independence and coherently changed evaluator-resource bytes, while retaining
the existing exact-envelope tamper tests.

### P2 — r7 manifest transport label still says r6

The r7 manifest's `transport` value is `r6 implementation freeze only`. This is
non-executable metadata, but it misidentifies the reviewed revision. Correct it
in the next immutable revision.

H2 manifest and receipt hashes remain byte-identical. No live/model/holdout or
external-system activity was performed.

## R8 final provenance critique — 2026-10-09

Reviewed run: `router-v3-h3-tuning-20261009-r8`; manifest SHA-256
`ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`.

Verdict: **pass**. No P0, P1, or P2 findings remain in the changed scope.

The default historical launcher is now bound to the r8 run directory and its
own `historical_h2_runtime`. Successful evidence reports the exact r8 run ID,
manifest hash, snapshot path, 16-file snapshot inventory identity, H2 manifest
and receipts hashes, v2 manifest/source hash, frozen gold hash, and frozen
baseline hash. Snapshot-directory and v2-directory substitution are rejected.

R8 freezes gold, reconciled baseline, H2/H2-parent/v2 manifests, and validates
their exact hashes and identities before subprocess launch. It validates every
referenced H2 and v2 file against the pinned manifests, plus H2 receipts. The
child imports replay code from the r8 snapshot and reads evaluator resources
only from r8's frozen `evaluation/` directory; mutable working-tree gold or
baseline cannot influence a successful replay. Drift probes for gold, baseline,
v2/H2 manifests and snapshot code all fail closed.

The snapshot replay reproduces H2 first technical 15/20, final 20/20, B=11 and
semantic plan 5/20. A repeated coherent H2 user-envelope/hash/binding tamper is
rejected by the snapshotted exact-envelope validation. The transport label now
correctly identifies r8.

R8 semantic scorer and named gate logic are byte-identical to r7 apart from
versioned imports. The focused r8 provenance plus r7 gate suite passes 20/20.
H2 manifest and receipts remain at their published hashes. No live/model/
holdout/Polza/Qdrant/production/bot activity was performed.

## R9 acceptance-fix critique — 2026-10-09

Verdict: **changes_required**. The frozen-resource correction is sound, but the
new regression predicate has one P1 false-pass boundary.

Confirmed closed: manifest SHA-256 is
`6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`;
the semantic evaluator validates R9 before scoring, consumes gold and reconciled
baseline only from R9's `evaluation/` directory, verifies their manifest hashes,
rejects an alternate `gold_path`, records consumed hashes, and obtains H2 through
the R9-bound historical snapshot. Mutable `GOLD_PATH`/`BASELINE_PATH` globals do
not influence the result. R8-to-R9 replay/lifecycle changes are version bindings
only; prior technical gates, scorer, runtime policy, and provenance checks remain
unchanged. The unnecessary-clarification count exactly means an actual
`needs_clarification` on a case whose expected status is different, and the
strict gate rejects counts above one. Targeted offline tests passed: 15/15.

### P1 — same-code critical omissions are collapsed and can pass the B/H2 gates

`evaluate_router_v3_h3_semantic_replay_r9.py:118-121` reduces each case's
`critical_omissions` to a set of `code` values. Critical omission identity also
contains `task_index` (and, for unmatched tasks, failed fields). A new omission
with an already present code is therefore invisible. An isolated fixture with
old `unmatched_expected_task(task_index=0)` and new omissions for task 0 plus
task 1 returned `added_critical_codes=[]` and
`no_new_critical_regressions=true`. If the old plan was already non-matching,
the plan-regression check does not catch it either. This can falsely satisfy
both the B and H2 no-new-critical predicates, contrary to the specification's
zero-new-omissions and separate per-case-diff requirement.

Minimal correction: compare a stable omission identity rather than only its
code (at least `code` plus `task_index`, and retain the relevant failed-field
detail), report the added omission records per case, and add a negative test for
an additional same-code omission on another expected task while every other
gate input passes. Freeze the correction as a new immutable revision and repeat
the narrow offline review. No sampling is authorized by this verdict.

## R10 critical-omission identity critique — 2026-10-09

Verdict: **pass**. No P0, P1, or P2 finding remains in the changed scope.

Reviewed run `router-v3-h3-tuning-20261009-r10`; manifest SHA-256 independently
matches `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
The R10 comparator canonicalizes stable omission records, excludes only the
transient `closest_actual_task_index`, normalizes `failed_fields`, and uses a
`Counter`, so multiplicity is preserved. Added records are reported individually
with their case ID and normalized identity.

Independent fixtures confirmed that an extra duplicate record, the same code on
a different `task_index`, a different dependency target, and changed
`failed_fields` each fail the regression predicate and report the added record.
Reordering omission records, reordering `failed_fields`, and changing only
`closest_actual_task_index` remain non-regressions. This closes the R9 false pass
without introducing order-dependent false positives.

R9 and R10 have identical frozen model/evaluation file hashes and identical
historical H2 snapshot inventories. Every common non-versioned code hash is
unchanged, including `router_subscription_eval_v3.py`; replay/lifecycle diffs are
version bindings. Frozen gold/baseline binding, separate B/H2 predicates, the
`<=1` unnecessary-clarification strict gate, scorer semantics, and prior
provenance checks are unchanged. `validate_frozen()` passed with the exact R10
identity. Targeted R10/R9/R8 tests passed 18/18. No live model, sampling,
holdout, Polza, Qdrant, production, or bot activity was performed.
