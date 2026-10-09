# Question parser stage 1, v3 H2 checkpoint — 2026-10-08

Status: the six final v3 P1 corrections are implemented for renewed independent
review. No acceptance is claimed. No subscription workers were launched, no new
model sampling occurred, and no holdout, Polza, Qdrant, network, or production
system was used.

## Reconciled baseline

- Immutable v2 legacy score: **7/20**.
- Reconciled baseline B: **11/20**. The four evaluator-only recoveries are
  S01 (union evidence spans), D01 (punctuation is not a semantic atom), D02
  (ordinary Russian inflection), and A01 (grounded unresolved task with a
  document clarification). Full per-case reasons and the source-token audit
  map are in `baseline_reconciled_v3_20261008.json` and
  `baseline_reconciled_v3_reasoned_diff_20261008.md`.
- Source evidence coverage is 12/20 by union of semantic atoms, while the
  legacy strict single-span diagnostic is 8/20. Dependencies and critical
  omissions use the same one-to-one task assignment; the V03/V06 paired guard
  passes.
- First-draft wire validity is 20/20; first-draft technical validity is
  16/20, so the correctly wired >=19 technical gate fails. Three captured
  attempt-2 outputs recover S01, S02, and C01. C02 has no attempt-2 receipt and
  is reported as `missing_attempt`, not as a model repair failure.
- Legacy `operation=unknown` audit derived from the original question maps C01
  and U03 to define, U01 to compare, U02 to conclude. Gold was not edited.
- V3 H2 progress threshold from B is 15/20 (B+4). The tuning gate still needs
  its classifier, technical validity, clarification, critical safeguard,
  semantic-plan, and receipt requirements; no such quality result is available
  until a later authorized subscription run is replayed.

## Frozen v3 inputs and H2 changes

The new directory is `router_subscription_run_tuning_v3_h2_20261008`.
It contains the same 20 ordered model-input rows and schema bytes as frozen v2,
with new classifier/parser/repair prompts. Gold and receipts are absent. The
run manifest reports `input_sha256_same`, `schema_sha256_same`, and
`case_order_same`; offline `validate_frozen()` returns
`v3_frozen_inputs_valid`.

Prompt SHA-256 values (after the prompt-hygiene and final P1 fixes):

- classifier `8ce5a284ade616c8a713ef7f96c1f8234253d0ef638575a936fa639b6910405a`
- parser `317c3c5039bfe79acc898dcad42d0e78817f002bcfbbf547e8a2c8127325c3d6`
- repair `fdc2e90018fb0e09ce75205ca4bb1f7e8ea271e4c0b72a94ee012c463d9ee27c`

H2 instructions define operations and semantic field ownership. The parser
prompt has six synthetic contrast groups; SourceRefs were checked against the
exact quoted example strings and tokenizer, including the staircase example.
Flat `candidates.terms` strings are explicitly bridged as separate legacy
meaning IDs, with ambiguity retained when candidates compete. A hygiene regression
checks all tuning question strings, case IDs, and electrical literals are absent
from classifier/parser/repair prompts. Request envelope
capture now runs through an injected file-replay lifecycle: it fsyncs each
envelope before dispatch, binds receipts to its hash, selects the parser from the
classifier result, attempts exactly one repair after technical invalidity, and
records missing attempts, transport failures, and model failures. Fake-dispatch
tests exercise this flow; no default or network transport exists. The scorer
keeps semantic one-to-one assignment independent of DAG correctness and accepts
zero unresolved tasks when a clarification itself is valid. Schema, assembler,
runtime models/effort, candidates, limits, and 20 inputs are unchanged.

Manifest SHA-256:
`c85aef34f6fd558d939e8924a8b56748c96af01a7d197f4ea03b1799de59fc4d`.

Frozen implementation code hashes:

- `router_prompts_v3.py` `5d7845b570f62059f78ebd13d935899f7125ac78bd01b788e27bb6acd7e2c017`
- `router_subscription_eval_v3.py` `147704ef6ce71bbb381891a3d01fd05921142fbc9591865af73b36ce04667f7a`
- `router_request_envelope_v3.py` `8b32696fc3547d86e4f719e47cab86b39bbab204fb08185dc2f943950b5ff24a`
- `router_subscription_lifecycle_v3.py` `4885e7c05e1669950143697df61f0b3b438b79776b396ac0b904e4eaf4fdda8c`
- `freeze_router_v3_run.py` `6bd7beeee359e64f0f9824cdd612f34f7d01a11619022438ac5d6c6c72d420a1`

The manifest now freezes hashes for all inherited v2 dependencies and the v3
prompt, scorer, envelope, lifecycle, and freeze/validation code. The inherited
v2 dependency hashes are recorded separately and checked against the v2 source
manifest.

Baseline report SHA-256:
`48cff410dfc9fc61f1cf4b4e4c39285f66a796188239711c44833a8487253929`.
Reasoned diff SHA-256:
`a0dd76cfc9ba15deda0c06b5275d00501339493e3905387e8e5502e123b3f411`.
Input manifest remains SHA-256
`b0a7ab3d55b8d185ab2439718ea180580f99ca51442c56f6b61d1ec0405fc4a4`; schema
remains `daf2033dd8c47f3764c824d6832a4d5e32d6e029a15acbb2ff83ccb469c1b035`.

## Verification and limits

- Deterministic replay of the old receipts ran twice; actual plans were
  identical. Old gold, receipts, and v2 report were not changed.
- Targeted v3 tests and relevant router/evaluator suites after the final P1
  fixes: **153 passed**.
- `py_compile` passed for the new evaluator, prompts, envelope capture,
  freeze/validation utility, and tests.
- Existing v2 offline gate remains `offline_frozen_inputs_valid`, 20 cases,
  `receipts_validated`, no workers started.
- V3 frozen-input validation passes with no receipts and no gold.
- Reconciled baseline replayed twice after scorer changes: both runs remain B=
  **11/20**, with identical substantive outputs and the same 11 passing cases.
- One pytest-cache permission warning occurred outside the sandbox; it did not
  affect test results.

Open risks: the reconciled scorer's fixed grammatical-connector list and
operation audit should receive independent review before use on new receipts.
The baseline's A02 corpus-scope issue and semantic omissions remain failures.
The H2 hypothesis is untested until a new tuning run has complete immutable
request envelopes and replay receipts. Server actual-model, event, timestamp,
usage, and physical request metadata remain unavailable in the historical run.

## Final receipt lifecycle P1 follow-up — r1

The immutable request lifecycle now uses the inherited `RECEIPT_FIELDS`,
`append_receipt`, and `ReplayAdapter`. Receipt role remains the classifier-
predicted parser role; the sole repair is the same role at attempt 2. Exact
request envelope hashes are joined through `receipt_envelope_bindings.jsonl`,
an additive sidecar, leaving receipt rows valid under the inherited schema.
Requested model and effort are taken from and checked against the frozen
manifest. A durable pre-dispatch envelope or receipt blocks resampling that
case/role/attempt; an envelope without a receipt is reported as
`missing_attempt`.

`evaluate_router_v3_file_replay.py` validates receipt/envelope/input/prompt
hashes through `ReplayAdapter`, applies wire and technical validation to the
first draft and optional repair, and reports `missing_attempt`,
`transport_failure`, and `model_failure` separately. Its no-receipt snapshot
reports 0/20 wire-valid, 0/20 first/final technical-valid, and all 20 cases as
`missing_attempt`; this is only a gate snapshot, not a model result.

The runtime-helper changes are frozen as new run lineage
`router-v3-h2-tuning-20261008-r1`, parented to `router-v3-h2-tuning-20261008`
with parent manifest SHA-256
`c85aef34f6fd558d939e8924a8b56748c96af01a7d197f4ea03b1799de59fc4d`. The r1
manifest SHA-256 is
`1d4a4840ccabec4779558c37cea2a5fd35a1ef3d6f2fdd83d1a9d9b3f0af2a4f`.
Freezing and validation now fail closed if any inherited v2 runtime dependency
differs from its original source-manifest hash. The r1 input, schema, case
order, and all three prompts have the same hashes as the parent v3 run.

The prior lifecycle checkpoint passed 159 router/evaluator tests and 432 full
sandbox tests. The final r2 follow-up passed 26 focused v3 tests and **433 full
sandbox tests** using a workspace-local pytest base directory. `py_compile`,
the v2 offline gate, and r2 frozen validation passed. No live sampling was
performed.

## H3 r9 Astra findings repair — 2026-10-09

Created immutable unreceipted R9, preserving R8. Run
`router-v3-h3-tuning-20261009-r9`; manifest SHA-256
`6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`.
R9 is explicitly parented to R8 manifest
`ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`.

The H3 semantic evaluator now validates the R9 freeze before reading resources,
consumes only `evaluation/router_plan_cases_tuning_v2.json` and the frozen
reconciled baseline, reports both actual consumed hashes, and rejects an
arbitrary `gold_path`. Its output is unchanged when working-tree gold/baseline
path globals point to mutated substitutes; mutated frozen resources fail hash
validation. H2 comparison data is obtained through the R9-bound snapshot replay
with the exact original H2 manifest/runtime provenance.

Gates now expose distinct no-new-critical-regression booleans versus baseline B
and H2; both are required for useful/full pass. The evaluator counts
unnecessary clarifications as actual clarification status on a case whose
expected status is different. Full tuning pass requires <=1. Negative tests
prove the gate rejects two extra clarifications and an H2-only regression.
Current empty-receipt replay remains fail-closed: useful=false and strict=false;
it reports zero extra clarifications but technical/semantic coverage fails.

R9 acceptance tests plus R8 provenance/R7 integration suite: 27 passed.
`py_compile`, R9 `validate_frozen`, and R9-bound historical H2 replay passed;
historical metrics remain technical 15/20 ->20/20, B=11/20, H2=5/20. No live
calls, sampling, holdout, Polza, Qdrant, production or bot activity occurred.
This is implementation evidence only; independent review and Astra acceptance
for R9 remain outstanding.

## Final provenance and gate P1 follow-up — r2

The gate reconstructs every envelope against the frozen prompt bytes and exact
input row. Classifier attempt 1 receives canonical JSON for the frozen row;
predicted parser attempt 1 receives that row plus the classifier decision;
parser attempt 2 must use the frozen repair prompt and the deterministically
reconstructed repair payload from attempt 1 raw output and its actual validator
error. Each envelope and receipt binding must carry the current run ID. Prompt
and user-message mutations are rejected even when envelope, sidecar, and
receipt hashes are coherently recomputed.

The report declares coverage and completion explicitly. Its first-technical
denominator is always 20 with a >=19 pass threshold; final-technical denominator
is 20 with a 20/20 threshold. The empty r2 preparation run is correctly
`complete=false`, with classifier and predicted-parser coverage 0/20 and all
cases `missing_attempt`; it is not represented as evaluation success.

Because parser request payload construction changed, the new run is
`router-v3-h2-tuning-20261008-r2`, parented to r1 manifest SHA-256
`1d4a4840ccabec4779558c37cea2a5fd35a1ef3d6f2fdd83d1a9d9b3f0af2a4f`. R2
manifest SHA-256 is
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`. Frozen
prompts, 20 inputs, schema bytes, case order, and baseline B remain unchanged.

## Offline acceptance and limit checkpoint — 2026-10-08

GPT-6 Astra medium returned **accept_offline** after independent review and 172
passing tests. It authorized subscription sampling only for frozen run
`router-v3-h2-tuning-20261008-r2`, using fresh GPT-6 Luna medium for classifier
and simple-parser roles and fresh GPT-6 Sol medium for complex-parser roles.

The reconciled baseline is B=11/20. H2 is useful only at >=15/20 semantic plans
with no new critical regressions and final technical validity >=19/20. The full
tuning gate remains >=18/20 semantic/status, 20/20 final technical validity,
classifier >=18/20 with all 11 expected-complex cases routed complex, all three
ambiguities correct, and the existing safety/metamorphic requirements.

Sampling was not started because the five-hour Codex window reached **94% used**.
Resume after reset from the exact r2 manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`.
Do not use Polza and do not open holdout.

## Post-sampling deterministic semantic evaluation — r2

The authorized r2 run now has **45 immutable receipts**. No new calls were made
for this evaluation. The offline technical replay was complete and deterministic:
first drafts were wire-valid 20/20, first technical validity was 15/20 (fails
the >=19 threshold), and final technical validity was 20/20 (passes).

Added `evaluate_router_v3_semantic_replay.py`, which reuses the v2 receipt-to-
draft validation/conversion/assembly and the reconciled v3 `compare_case`
scorer. It loads only the frozen 20-case tuning gold, reconstructs actual plans
from r2 receipts, and compares two complete replays byte-for-byte. Both produced
identical actual plans and results. Report:
`router_subscription_run_tuning_v3_h2_r2_20261008/semantic_evaluation_report.json`
SHA-256 `9441a9d0b2ccdcf44e18afb3e4c19494f8b010f31ce8b13aca12fd6f49e4a5cc`.
Frozen receipt SHA-256 was verified as
`184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`; frozen
input, schema, and prompt hashes also match the r2 manifest.

The reconciled baseline is B=11/20; r2 semantic plans matched **5/20**, below
the >=15 useful threshold, so H2 was **not useful**. There were six regressions
among baseline-passing cases: S01, S03, C03, D02, U01, and U02. Additional
critical omission codes appeared for S03, C03, D02, U01, and U02.

The full tuning gate **failed**: classifier 15/20 (threshold 18), and only
10/11 expected-complex cases were classified complex; semantic plans 5/20
(threshold 18); status 18/20 (passes); ambiguity cases 1/3 correct; V03/V06
metamorphic concepts compatibility failed; first technical validity 15/20
(threshold 19); final technical validity 20/20 (passes). Thus the semantic
result is byte-reproducible, but it misses usefulness and the overall gate.
No holdout, Qdrant, production, bot, or network was used, and frozen inputs,
prompts, receipts, and schema were unchanged.

## H3 architecture decision — 2026-10-08

Astra reviewed the completed H2 raw receipts, repair envelopes, semantic and
technical reports and current prompt/validator/assembler. Verdict:
**changes_required**; H2 remains failed. No code, H2 frozen artifacts or gold
were changed in this architecture step; no model sampling was performed.

The chosen single H3 hypothesis is field-representation contract alignment:
H2 examples explicitly taught SourceRef values for topic, while the validator
accepted them as ordinary strings and the assembler copied them into query.
This affects 12/20 cases and five substantive baseline regressions. All five
first technical failures are the reverse representation error: literal point
instead of SourceRef, successfully repaired later.

Next: GPT-6 Luna medium implements SPEC v3.1, preserving historical runtime
replay first, then correcting only parser/repair field-format instructions and
adding a fail-closed TOPIC_MUST_BE_LITERAL_TEXT guard. No automatic dereference,
classifier tuning, candidate grouping, gold changes or scorer recalibration.
Independent classifier errors, flat term ambiguity, D03 constraint omission and
C02 task-local evidence scoring limitation are documented separately.

## H3 implementation checkpoint — 2026-10-08

Implemented the narrow H3 field-type correction in versioned modules. H2's
`router_prompts_v3.py`, `router_draft.py`, H2 run directory, 45 receipts, and
historical semantic report were not edited. The H3 parser prompt now defines
`topic` as literal text and `point` as a one-point `SourceRef`; synthetic topic
examples use literal text and their token spans were checked with the frozen
tokenizer. Repair has a single targeted instruction for the stable
`TOPIC_MUST_BE_LITERAL_TEXT` marker. `router_draft_h3.py` rejects locator-shaped
topic values before delegating to the unchanged v2 validator/assembler, without
derefencing or fallback. `router_validation_policy.py` selects either the
legacy v2 validator or the explicit H3 policy and rejects unknown versions.

The first, unreceipted H3 freeze was created before the synthetic span audit
found one off-by-one reference. Following the spec's immutable-run rule, that
reserved r1 directory was not rewritten; the corrected freeze is revision r2,
which marks r1 as superseded. No receipts or sampling exist in either H3 run.
The r2 run is `router_subscription_run_tuning_v3_h3_r2_20261008`, run ID
`router-v3-h3-tuning-20261008-r2`, parented to H2 r2 manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`. Its
manifest SHA-256 is
`29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3`.
It records `validation_policy=topic_literal_guard.v1`, preserves the 20 H2
tuning inputs/order and schema, and contains no gold or receipts. The frozen
parser prompt SHA-256 is
`0c6cc788cea3034a45eab010b417a7025aabdcba42453916a56dd78b949c374f`; repair
prompt SHA-256 is
`dcad9e073d6acb27ea9db4d314be10ac321dc46e547e4c29380bfa8b815aed63`.
H2 executable dependencies are copied and hash-verified under the H3 run's
`historical_h2_runtime/` for historical replay provenance.

The untouched H2 historical report remains SHA-256
`9441a9d0b2ccdcf44e18afb3e4c19494f8b010f31ce8b13aca12fd6f49e4a5cc` and
continues to record reconciled B=11/20, H2 semantic=5/20, first technical
15/20 and final technical=20/20. H3 policy is not applied retroactively.
Focused offline tests passed: 57 tests across `test_router_h3.py` and
`test_router_draft_v2.py`; the four H3 modules also passed `py_compile`.
Freeze validation passed with 20/20 input parity, unchanged schema, no gold,
and no receipts. The broader `test_router_v3_eval.py` lifecycle tests that
create receipts in the fixed H2 directory are incompatible with its already
frozen real receipts and correctly hit the duplicate/resume guard, so those
eight mutation-oriented tests were deselected; the other 22 v3 evaluator tests
passed. This is a fixture-isolation issue, not a reason to mutate the historical
run. H3 remains unaccepted and unsampled; independent tests, critique, and
Astra offline acceptance must precede any subscription dispatch.

Documents:
- SPEC_QUESTION_PARSER_STAGE1.md (v3.1 / H3 normative instructions and gates)
- SPEC_QUESTION_PARSER_STAGE1_V3_H2_ARCHIVE.md (unchanged former v3.0)
- DECISION_QUESTION_PARSER_STAGE1_H3_20261008.md (root causes and choice)

Implementation must pass GPT-5.5 medium tests, GPT-5.6 Sol medium critique and
Astra offline acceptance before sampling. Reserved next run is
router-v3-h3-tuning-20261008-r1, same 20 frozen tuning inputs/schema/order,
fresh subscription Luna medium classifier/simple and Sol medium complex.
H3 useful threshold remains >=15/20 (B+4), first technical>=19/20, final20/20,
no new critical regressions against B or H2; the full strict tuning gate is
unchanged. This decision does not authorize immediate sampling or holdout.

## Independent H3 implementation critique — 2026-10-08

GPT-5.6 Sol medium critique verdict is **changes_required**. The reviewed
artifact is the corrected r2 run
`router-v3-h3-tuning-20261008-r2`; r1 is superseded and must not be used.
Its manifest SHA-256 is
`29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3`.

The isolated H3 prompt and `TOPIC_MUST_BE_LITERAL_TEXT` guard match the narrow
type-contract hypothesis, and 57 focused H3/legacy-draft tests pass. However,
the only executable subscription lifecycle still imports the H2 prompts and
H2 `_validate_raw`; neither lifecycle nor replay reads `validation_policy` or
uses `validator_for_policy`. A read-only/offline probe against a temporary copy
of exact H3 r2 accepted `topic="current@2:2"` as valid and showed that the sent
parser prompt did not equal the frozen H3 parser prompt. Therefore locator
rejection and its one-repair policy are not active in the actual run path.

The H3 manifest also freezes no H3 lifecycle or replay evaluator, so mandatory
empty replay, exact envelope reconstruction, repair binding, coherent tamper
negatives, coverage denominators and gates are not yet executable for H3. The
freeze validator needs fail-closed anchoring of r2/parent identity and exact
byte parity rather than only manifest-self-described hashes.

H2 remains unchanged: manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`,
receipts SHA-256
`184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`;
read-only replay still reports 45 receipts, first technical 15/20 and final
technical 20/20. No live/model/holdout/external sampling was performed.

Detailed report:
`CRITIQUE_QUESTION_PARSER_STAGE1_H3_20261008.md`. Sampling remains forbidden
until the lifecycle/replay/freeze blockers are fixed, independently retested,
re-criticized, and accepted offline by Astra.

## H3 independent test result — 2026-10-08

GPT-5.5 Sol medium independent check of H3 implementation/freeze completed with
verdict PASS for the H3 scope. Product code was not changed; no live calls,
sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.
Detailed report: `REPORT_QUESTION_PARSER_H3_INDEPENDENT_TEST_20261008.md`.

Confirmed facts: correct run is `router-v3-h3-tuning-20261008-r2`; manifest
SHA-256 is `29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3`;
`router-v3-h3-tuning-20261008-r1` is recorded as superseded; H3 r2 has no
receipts/envelopes/bindings; `validation_policy=topic_literal_guard.v1`; H2
manifest SHA-256 remains `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`;
H2 receipts SHA-256 remains `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`
with 45 receipt rows. H3 prompt field types, fail-closed topic guard before
delegated validation/assembly, no auto-dereference, version selection, freeze
integrity and H2 immutability were verified.

Commands/results: `test_router_h3.py` 20 passed; H3+v2/router offline suite
152 passed; py_compile passed for H3 and related router modules; v2 offline gate
returned `offline_frozen_inputs_valid` with `live_workers_started=false`. A
broader legacy command including `test_router_v3_eval.py` produced 174 passed and
8 failed because those old mutation-oriented tests still assume H2 r2 has no
receipts; current SPEC v3.1 records H2 r2 as immutable with 45 receipts. No H3
implementation/freeze defects were found.

## H3 critique resolution of the test/implementation discrepancy

The later independent critic review supersedes the preceding test-only
conclusion for acceptance status: **changes_required**. The 152 passing offline
tests validate the isolated guard and frozen artifacts but do not execute an
H3 subscription lifecycle or H3 replay. The only current lifecycle imports H2
prompts and H2 validation, and an offline temporary-run probe accepted a
locator-valued topic while sending a parser prompt different from the frozen
H3 prompt. This is a runtime integration blocker, not a contradiction in the
isolated guard tests. The authoritative detailed finding and minimal fix are in
`CRITIQUE_QUESTION_PARSER_STAGE1_H3_20261008.md`. H3 remains unsampled and must
not proceed to Astra acceptance or dispatch until this path is fixed and tested.

## H3 r3 independent critic follow-up — 2026-10-08

Reviewed r3 manifest SHA-256:
`509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5`.
Verdict remains **changes_required**.

R3 closes the intended technical path: `FileSubscriptionRunnerH3` sends exact
frozen H3 prompts, applies `topic_literal_guard.v1` to initial and repair output,
permits one repair, and `replay_h3` verifies exact envelopes, policy-derived
repair errors, bindings, hashes, identity and empty-run gates. Focused tests are
28/28 passing. The freeze now anchors r3/parent identity, exact input/source/
schema and prompt bytes, runtime hashes and receipt presence.

Two P1 blockers remain in the changed scope. First, inherited
`FileSubscriptionRunnerV3` can still be pointed at r3 and dispatch H2 prompts
with H2 validation; a temporary-copy probe accepted locator topic
`current@2:2` as valid while writing r3 receipts. It must reject H3 manifests
before any envelope write or dispatch. Second, no H3 assembly/semantic replay
exists or is frozen: the tested path ends at `semantic_draft`, technical replay
never calls the assembler, and its semantic gate is a constant placeholder.
The actual `lifecycle -> guard -> assembly -> replay` chain is therefore not
yet executable or immutable.

H2 manifest and receipt hashes remain unchanged. No live/model/holdout/Polza/
Qdrant/production/bot activity occurred. Full details and minimal corrections
are recorded in `CRITIQUE_QUESTION_PARSER_STAGE1_H3_20261008.md`.

## H3 r4 independent critic follow-up — 2026-10-08

Reviewed r4 manifest SHA-256:
`5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`.
Verdict is **changes_required**.

R4 closes the prior bypass and missing-assembly defects. The legacy runner now
rejects H3 before write/dispatch; the H3 runner uses exact frozen prompts,
policy validation/one repair, then assembly. Technical receipt replay repeats
validation and assembly, while the new semantic replay uses the reconciled
scorer/baseline and deterministic per-case plans. Focused H3 tests pass 30/30.

Two P1 blockers remain. First, changing the shared legacy lifecycle invalidated
the live H2 code hash. This would be acceptable only with a working historical
snapshot replay, but the live H2 validator now fails and the snapshotted replay
cannot resolve its frozen v2 source run from inside the snapshot directory.
Historical H2 replay with original tamper checks is therefore not executable;
unchanged artifacts alone do not satisfy the reproducibility requirement.

Second, the frozen H3 semantic gates are incomplete. `h3_useful` omits required
first/final technical thresholds, complete coverage and explicit locator-free
accepted plans. The strict gate checks only *new* critical regressions relative
to baseline, rather than the required zero ready plans with the listed critical
semantic losses. Both decisions can report false pass under admissible score
combinations.

H2 manifest/receipt bytes and published hashes remain unchanged. No live/model/
holdout/Polza/Qdrant/production/bot work was performed. Detailed evidence and
minimal fixes are in `CRITIQUE_QUESTION_PARSER_STAGE1_H3_20261008.md`.

## H3 r7 independent critic follow-up — 2026-10-09

Reviewed r7 manifest SHA-256:
`53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`.
Verdict is **changes_required**.

The semantic gate P1 is closed. Named `useful` criteria now require complete
coverage, first technical >=19/20, final 20/20, locator-free accepted plans,
semantic >=15/20 and no new critical regressions. The full gate adds semantic
and status >=18/20, classifier >=18/20, all 11 complex routes, all ambiguities,
V03/V06 and zero prohibited ready-plan losses. Integer assignment keys and
unmatched expected tasks are handled. Focused r7 tests pass 12/12.

One provenance P1 remains. `replay_router_h2_snapshot.py` still defaults to the
superseded r5 snapshot/manifest rather than r7's frozen snapshot. Its child
imports code from that snapshot, but reads gold/baseline from mutable working-
tree paths without validating them against r7 evaluation-data hashes; the v2
manifest is checked only by run ID. The current replay reproduces H2 first15,
final20, B=11 and semantic5 and rejects the tested envelope tampering, but it
does not prove exact r7 snapshot/resource isolation or fail closed on resource
drift that preserves aggregate metrics.

The next revision must bind the launcher to its own manifest and snapshot,
freeze or hash-verify every evaluator resource before subprocess launch, pin
the v2 manifest hash, and retain the exact-envelope tamper negatives. The r7
manifest also has a P2 stale `transport` label referring to r6. H2 artifact
hashes remain unchanged. No live/model/holdout/external work was performed.

## H3 r8 final provenance critic follow-up — 2026-10-09

Reviewed r8 manifest SHA-256:
`ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`.
Independent critic verdict is **pass** with no P0/P1/P2 findings in the changed
scope.

The launcher defaults to r8's own snapshot and frozen evaluation directory. It
reports and validates r8 manifest/snapshot identities, H2 manifest/receipt
hashes, exact v2 manifest/source hash, and frozen gold/baseline hashes. H2/v2
manifest contents and every referenced resource are checked. Snapshot, frozen
resource and directory-substitution probes fail closed; coherent H2 envelope
tampering remains rejected by the original snapshotted checks. Historical
metrics reproduce first15/final20, B=11 and H2=5.

R8 scorer/gates differ from r7 only in versioned imports. The combined focused
r8 provenance and r7 gate suite passes 20/20. Transport metadata now names r8.
H2 artifacts remain byte-identical. No live/model/holdout/Polza/Qdrant/
production/bot activity was performed. R8 is ready for the next offline
acceptance step; this critique does not authorize sampling.

## H3 lifecycle/replay P1 resolution — 2026-10-08

Addressed only the two P1 findings in `CRITIQUE_QUESTION_PARSER_STAGE1_H3_20261008.md`.
The H3 lifecycle now loads parser/repair prompt bytes from the frozen run,
requires `validation_policy=topic_literal_guard.v1`, and calls
`validator_for_policy` after wire validation on both first draft and repair.
It reuses durable receipt/envelope mechanics without changing the H2 lifecycle
or H2 replay path. A topic such as `current@2:2` fails technical validation,
records the stable marker in the repair payload, and cannot be accepted. A
second invalid locator fails after exactly one repair.

Added `evaluate_router_v3_h3_replay.py`. It reconstructs classifier and
predicted-parser requests from frozen inputs, uses observed classifier role,
reconstructs repair payloads from the exact first response and H3 validation
error, verifies envelope hashes/bindings/receipt metadata, and validates with
the manifest-selected policy. The empty replay fixes denominators at 20,
reports zero receipts/coverage, lists all 20 missing attempts, and fails closed
with `complete=false` and `overall_gate_pass=false`. Freeze validation is
anchored to H3 run identity, pinned H2 r2 manifest identity/hash, byte-identical
H2 inputs/source manifests/schema/classifier prompt, exact H3 parser/repair
prompt bytes, and the inherited H2 dependency snapshot. Receipt presence/count
is read from disk.

The corrected immutable H3 revision is r3:
`router-v3-h3-tuning-20261008-r3` at
`router_subscription_run_tuning_v3_h3_r3_20261008`. r1 and r2 are explicitly
superseded, unreceipted freezes; do not dispatch from them. r3 parent is H2 r2
manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`. H3 r3
manifest SHA-256: `509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5`.

Focused tests passed: 65 across `test_router_h3_lifecycle.py`,
`test_router_h3.py`, and `test_router_draft_v2.py`; `py_compile` passed for the
H3 lifecycle, replay, policy, prompt, validator, and freeze modules. The empty
replay report is saved as
`router_subscription_run_tuning_v3_h3_r3_20261008/offline_replay_report.json`,
SHA-256 `1f7a08540e864e58cc9716eacf7dfb06075ce39949fbc16d68de13218203ca96`.
Fake-dispatch regressions verify frozen prompt delivery, one repair,
repeated-locator failure, literal-topic preservation, envelope and repair-error
binding tamper rejection, and prompt/identity drift rejection. No receipts or
models were used. H2 manifest, receipts, and historical report remain at their
previous hashes. A read-only H2 frozen validation and replay reconfirmed 45
receipts, first technical 15/20, final technical 20/20; the saved H2 semantic
report remains B=11/20 and H2=5/20. Twenty-two unaffected tests in
`test_router_v3_eval.py` also passed; eight receipt-writing tests were excluded
because they target the already sampled immutable H2 directory. H3 remains
unsampled and still requires independent critique and Astra offline acceptance.

## H3 r3 independent test result — 2026-10-08

GPT-5.5 Sol medium independent check of the changed H3 r3 area completed with
verdict PASS. Product code was not changed; no live calls, sampling, holdout,
Polza, Qdrant, production bot, or eval workers were used. Detailed report:
`REPORT_QUESTION_PARSER_H3_R3_INDEPENDENT_TEST_20261008.md`.

Confirmed correct run `router-v3-h3-tuning-20261008-r3`, manifest SHA-256
`509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5`. The real
H3 path `router_subscription_lifecycle_h3.py -> frozen H3 prompt ->
validation_policy guard initial/repair -> assembly -> evaluate_router_v3_h3_replay.py`
was exercised on copied frozen r3 runs. Covered scenarios: locator initial +
successful repair, locator repeated after repair fails after exactly one repair,
ordinary literal topic, exact point SourceRef, two-point composite request,
document ambiguity with exact candidate option IDs, empty replay, envelope and
repair binding reconstruction, coherent tamper negatives, and H2 immutability.

Results: `test_router_h3_lifecycle.py` + `test_router_h3.py` 28 passed; focused
H3/v2/router offline subset 160 passed; H3 py_compile passed; v2 offline gate
returned `offline_frozen_inputs_valid` with `live_workers_started=false`.
Independent scenario script confirmed empty replay missing=20 and H2 receipt
count=45. H2 manifest SHA remains
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`; H2 receipts
SHA remains `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`.
No H3 r3 changed-area defects were found.

## H3 r4 lifecycle bypass and semantic replay P1 resolution — 2026-10-08

Addressed the final two P1 findings with a single end-to-end fake-dispatch path.
The legacy `FileSubscriptionRunnerV3` now rejects H3 run IDs/directories and
non-legacy validation policies during construction, before envelope creation;
it rechecks the on-disk manifest immediately before dispatch. The bypass test
passes an actual r4 run directory to the legacy runner and confirms rejection,
no dispatcher call, and no envelope/receipt writes. The H2 runner still executes
against its H2 manifest in the regression fixture.

The H3 lifecycle now returns and persists the output of the existing
`assemble_skill_query_plan`; deterministic H3 replay reconstructs the plan from
receipts and invokes the unchanged reconciled `compare_case` and V03/V06 pair
scorers. The semantic report has computed outcomes and gates, not a placeholder.
The code inventory pins lifecycle, assembler, technical replay, semantic replay,
and reconciled scorer hashes. End-to-end fake-dispatch cases cover literal topic
search, exact point reference, a two-task composite, ambiguity, and invalid
locator followed by the single allowed repair. Empty receipt replay, envelope
reconstruction/tamper, and coherent identity tamper are also covered. Two
semantic replays compare equal after excluding generated time.

The immutable revision is `router-v3-h3-tuning-20261008-r4` at
`router_subscription_run_tuning_v3_h3_r4_20261008`, directly parented to r3
manifest SHA-256
`509730784cc00ddb00c1e86e6151d9bfeecaa9d6cee0a47ba18444ac6eba5ec5` and ultimate
H2 r2 manifest SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`. Its
manifest SHA-256 is
`5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`.
The r4 empty replay correctly fails its live-sampling quality gates: it has no
receipts (classifier, parser, technical, and semantic denominators remain 20),
so r4 is a frozen implementation revision, not an acceptance result.

Validation: focused H3/v2 suite 67 passed; `py_compile` passed for lifecycle,
technical replay, semantic replay, and freeze modules; `validate_frozen()` passed
for r4; semantic replay ran twice and wrote
`router_subscription_run_tuning_v3_h3_r4_20261008/semantic_evaluation_report.json`.
No model sampling, live calls, holdout, Polza, Qdrant, production, or bot activity
occurred.

Known hash constraint: adding the mandatory pre-dispatch guard changes the
shared legacy lifecycle module hash, so direct H2 `validate_frozen()` against
the current checkout reports a code-hash mismatch for that module. H2 manifest,
receipts, and historical reports were not rewritten; the exact old lifecycle
source remains in r3/r4 historical snapshots, and H2 behavior is covered by the
legacy-manifest regression. Preserve this caveat for independent review rather
than claiming byte-identical H2 runtime validation.

## H3 r4 independent test result — 2026-10-08

GPT-5.5 Sol medium independent check of H3 r4 changed scope completed with
verdict PASS for H3 r4, with an explicit H2-verifier caveat. Product code was
not changed; no live calls, sampling, holdout, Polza, Qdrant, production bot, or
eval workers were used. Detailed report:
`REPORT_QUESTION_PARSER_H3_R4_INDEPENDENT_TEST_20261008.md`.

Confirmed correct run `router-v3-h3-tuning-20261008-r4`, manifest SHA-256
`5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`. Addressed
changed scope was exercised independently: legacy V3 runner rejects H3 before
envelope/dispatch/write; H3 lifecycle dispatches frozen H3 prompts, applies
`topic_literal_guard.v1`, and returns assembled `SkillQueryPlan`; H3 replay
reconstructs envelopes/bindings and exposes `actual_plan`; semantic replay uses
reconciled scorer/gates with denominator 20 and no placeholders. Five real
end-to-end scenarios covered literal search, exact point, two-point composite,
locator initial+repair, and ambiguity. Empty replay, repair binding, tamper
negatives, and H2 immutability checks also passed.

Results: H3 targeted tests 30 passed; focused H3/v2/router subset 162 passed;
H3 py_compile passed; v2 offline gate returned `offline_frozen_inputs_valid`.
Independent scenario script confirmed r4 manifest hash, empty missing=20,
legacy bypass rejection before dispatch, 11 receipts across five cases, and
semantic scores present for actual plans.

H2 verifier caveat: direct current-checkout H2 `validate_frozen`, technical
replay, and semantic replay all fail with the same root cause, `frozen code hash
mismatch: router_subscription_lifecycle_v3.py`, because r4 changed that shared
file to add the mandatory H3 pre-dispatch guard. H2 artifacts were not rewritten,
but current-checkout H2 verification no longer passes. Treat this as acceptable
only if the gate explicitly verifies immutable H2 artifact hashes and the frozen
historical-runtime snapshot; otherwise it is a historical reproducibility
violation. Minimal criterion is recorded in the r4 independent report.

## R5/R6 follow-up critique resolution — 2026-10-09

R4 critique P1 findings are addressed. R5 added a snapshot-isolated historical
H2 launcher and named H3 gates; a gate audit then produced immutable successor
R6 with fail-closed attribution when a ready plan omits a task and with empty
locator coverage reported false. R4 and R5 run directories/manifests were not
overwritten; both are unreceipted predecessors.

The H2 launcher `replay_router_h2_snapshot.py` checks original H2 r2 manifest
SHA-256 `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862` and
receipts SHA-256 `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`,
verifies its frozen runtime inventory, and imports only the copied snapshot in a
clean child process. The launcher binds the original H2 r2 run, H2 r1 parent,
and frozen v2 source artifacts. It reproduced complete coverage 20/20, first
technical 15/20, final technical 20/20, baseline B=11/20, and H2 semantic 5/20
twice with byte-identical results. Coherently rehashed system-prompt and
user-request tampering are rejected by the snapshot replay.

Current immutable revision: `router-v3-h3-tuning-20261009-r6` at
`router_subscription_run_tuning_v3_h3_r6_20261009`. Direct parent R5 manifest
SHA-256 `c5ef56602de9ed73773a1bcd9c88b3eeb88e553e7e60e896f0fcb64f9c42bf29`;
ultimate H2 parent SHA-256 is the H2 value above. R6 manifest SHA-256:
`b18c1c25e5c69915f2e7ea9d92d0d8eb554720ee454a368abadc0ce6b706200a`. Inventory
pins H3 lifecycle/replay/gates, assembler and scorer, the legacy-runner guard,
snapshot launcher, and inherited runtime dependencies. The tuning input bytes,
source manifests, schema and prompts remain frozen; no gold or receipts are
included.

H3 replay now reports named booleans for complete coverage, first technical
>=19/20, final technical 20/20, locator-free accepted plans, semantic useful
>=15/20, no new critical regressions, and zero ready losses in intent, negation,
number/unit, wrong document, or unsupported/dependent chains. `useful` requires
all useful criteria; strict tuning additionally requires classifier >=18/20
and all 11 complex cases, semantic/status >=18/20, all ambiguity cases, V03/V06
compatibility, and zero critical ready losses. Negative gate tests cover 15/20
with a missing technical criterion and 18/20 with missing complex/critical
criteria.

Verification: `test_router_h3_r6.py` 11 passed; `test_router_h3.py` plus
`test_router_draft_v2.py` 57 passed; relevant `py_compile` and R6
`validate_frozen()` passed. R6 empty replay correctly reports zero receipts,
coverage 0/20, locator-free accepted plans false, and both quality gates false.
R6 semantic report SHA-256 `005d26a5e96de205eeee9908f4cbcb67a148a53a728883d497291d35b76b7713`;
H2 replay evidence SHA-256
`ab2197d563e88c1f9ce43f3a30d2e834d18a51a0a65f96c835766c987c360f98`. No model
sampling, live calls, holdout, Polza, Qdrant, production, or bot activity
occurred.

## R7 task-assignment audit correction — 2026-10-09

The final gate audit found that `compare_case` returns integer task-assignment
keys before JSON serialization. R7 accepts the scorer's actual key type and
adds a positive mapped-task regression alongside the unmatched-task negative;
R5/R6 are retained unchanged as unreceipted predecessors. This prevents valid
document/point/condition matches from being counted as ready-plan losses.

Current frozen revision: `router-v3-h3-tuning-20261009-r7`, directory
`router_subscription_run_tuning_v3_h3_r7_20261009`. Direct parent is R6
manifest SHA-256
`b18c1c25e5c69915f2e7ea9d92d0d8eb554720ee454a368abadc0ce6b706200a`; ultimate
H2 parent remains SHA-256
`e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`. R7
manifest SHA-256 `53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`.
R7 empty semantic replay is deterministic and fails useful/full gates with
coverage 0/20, first/final technical 0/20, and locator-free accepted plans
false. H2 snapshot replay still reproduces 15/20 -> 20/20, B=11, H2=5 twice.

Validation: `test_router_h3_r7.py` 12 passed; `test_router_h3.py` plus
`test_router_draft_v2.py` 57 passed; py_compile and `validate_frozen()` passed.
R7 semantic report SHA-256
`5ba22f5ee5b80b0921991910496594271813f11ccf81cca71d4ef2a11912f1af4`;
R7 historical H2 evidence SHA-256
`bd3ab7e3adadf03890356b77b8c3bf24214a34c888aef70bf6838d2f7c80a637`.
No sampling, live calls, holdout, Polza, Qdrant, production, or bot activity
occurred.

## H3 r7 independent test result — 2026-10-09

GPT-5.5 Sol medium independent check of final H3 r7 completed with verdict PASS.
Product code was not changed; no live calls, sampling, holdout, Polza, Qdrant,
production bot, or eval workers were used. Detailed report:
`REPORT_QUESTION_PARSER_H3_R7_INDEPENDENT_TEST_20261009.md`.

Confirmed correct run `router-v3-h3-tuning-20261009-r7`, manifest SHA-256
`53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`. Snapshot
H2 launcher reproduced exact historical metrics: first technical 15/20, final
technical 20/20, B=11/20, H2 semantic=5/20; repeated snapshot replay was
deterministic and coherent prompt/user tamper was rejected. r7 empty replay and
semantic replay fail closed with denominator 20 and no receipts. Named H3 useful
and full gates reject false-pass cases; integer task-assignment attribution and
unmatched-ready critical-loss accounting were verified. One real end-to-end path
(S02) exercised lifecycle -> frozen prompt -> H3 policy -> assembly -> technical
replay -> semantic replay, and lifecycle plan matched replay `actual_plan`.
Legacy V3 runner rejects r7 before dispatch/write. r4/r5/r6 manifest hashes were
checked: r4 `5a7febf3b297978aa60729ddc0142994fbb11ca120e11ac40c21876fc2add3d0`,
r5 `c5ef56602de9ed73773a1bcd9c88b3eeb88e553e7e60e896f0fcb64f9c42bf29`, r6
`b18c1c25e5c69915f2e7ea9d92d0d8eb554720ee454a368abadc0ce6b706200a`.

Results: r7 targeted tests 12 passed; relevant r7/r6/r5/H3/v2/router suite 184
passed; r7 py_compile passed; snapshot H2 CLI returned the expected metrics;
snapshot coherent tamper tests 2 passed. A broad legacy command including stale
r4 `test_router_h3_lifecycle.py` produced 188 passed and 6 failed because those
old tests validate r4 against the current checkout after shared semantic replay
hash drift; this is not an r7 failure and should not be used as the final r7 gate
without updating to r7-specific entrypoints or immutable snapshot criteria.

## H3 r8 provenance repair — 2026-10-09

Created immutable unreceipted R8, leaving R7 intact. Run
`router-v3-h3-tuning-20261009-r8`; manifest SHA-256
`ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`.
The H2 replay launcher now defaults only to R8 and reports that run identity,
manifest SHA, snapshot identity `historical_h2_runtime`, snapshot inventory
SHA-256 `437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`,
and frozen resource hashes. R8 pins direct parent R7 manifest SHA
`53dde8f93648791f9986a5cac1537a8d5c2e1c488e87903ff8b8f97c9f8be417`, H2 r2
manifest SHA `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`,
H2 receipt SHA `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`,
and full v2 manifest SHA `29388141657d47bed126e01fda28ce8a6158d06e1caefd56f44175fe48c511ad`.
Gold, reconciled baseline, and H2/H2-parent/v2 manifests are frozen under the
R8 evaluation directory. Before loading, the validator checks exact R8 copies,
all resources listed by H2 and v2 source manifests, historical snapshot
inventory, prompts/schema/source manifests, and parent identities.

R8 snapshot replay reproduced first technical 15/20, final technical 20/20,
baseline B=11/20, and H2 semantic 5/20. Provenance regression tests reject
tampering with gold, baseline, v2 manifest, H2 manifest/source bindings, and
snapshot code/directory substitution. R8 provenance plus R7 end-to-end suite:
20 tests passed. R8 modules and tests passed `py_compile`. A module-style import
validation attempt from repository root did not work because these tools are
script-oriented; the supported freezer and replay CLIs validate successfully.
No sampling/live calls, holdout, Polza, Qdrant, production, or bot activity
occurred; gates and scorer semantics remain unchanged.

## H3 r8 independent provenance test result — 2026-10-09

GPT-5.5 Sol medium independent check of H3 r8 changed provenance scope completed
with verdict PASS. Product code was not changed; no live calls, sampling,
holdout, Polza, Qdrant, production bot, or eval workers were used. Detailed
report: `REPORT_QUESTION_PARSER_H3_R8_INDEPENDENT_TEST_20261009.md`.

Confirmed correct run `router-v3-h3-tuning-20261009-r8`, manifest SHA-256
`ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50`. The r8
launcher is bound to the r8 manifest and r8 historical snapshot. Frozen gold,
reconciled baseline, H2 r2 manifest, H2 r1 parent manifest, v2 manifest, and
H2/v2 resource inventories are checked before load. Historical replay reproduced
first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20. Success
evidence includes r8 identity, snapshot identity/hash, H2 manifest/receipts SHA,
v2 manifest SHA, gold SHA, and baseline SHA. Transport label is r8-specific.
Drift negatives rejected tampering of gold, baseline, v2 manifest, H2 manifest,
H2 parent manifest, snapshot runtime code, and snapshot semantic replay resource;
snapshot directory substitution was rejected. Gates/scorer boundary behavior and
one real end-to-end path (S02) still passed.

Results: r8 provenance targeted tests 8 passed; relevant r8/r7/r6/r5/H3/v2/router
suite 192 passed; r8 py_compile passed; r8 snapshot CLI returned expected
historical metrics. Independent scenario script confirmed 7 drift negatives and
E2E S02. No H3 r8 provenance-scope defects were found.

## Astra final offline acceptance H3 r8 — 2026-10-09

Verdict: **changes_required**; r8 sampling is not authorized. Full report:
ACCEPTANCE_QUESTION_PARSER_H3_R8_ASTRA_20261009.md.

Independent relevant suite passed 192 tests with workspace-local basetemp;
r8 frozen validation and snapshot-isolated H2 replay passed (15/20 ->20/20,
B11, H2 semantic5). Snapshot provenance/tamper and real lifecycle path accepted.

Two remaining P1 requirements affect the new H3 semantic evaluator:
1. It still reads HERE gold/baseline (and arbitrary gold_path), while validation
   checks r8/evaluation copies. Bind actual consumed scorer resources to the
   run's frozen hashes and report them.
2. Named gates omit <=1 unnecessary clarification and separate no-new-critical
   regression against H2 (only B is compared). Add measured predicates and
   fail-closed negative boundary tests without changing semantic matching.

Preserve r8 immutable. Next: Luna narrow evaluator/provenance revision, changed-
area Sol5.5 tests and Sol5.6 critique, then Astra offline acceptance. No live,
sampling, holdout, Polza, Qdrant, production or bot calls were made. Only the
acceptance report and this checkpoint were modified by Astra.

## H3 r9 acceptance-fix critic follow-up — 2026-10-09

Independent verdict: **changes_required**. Correct run identity and manifest
were verified (`router-v3-h3-tuning-20261009-r9`, SHA-256
`6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`).
Frozen-only gold/baseline consumption, working-tree independence, resource
tamper rejection, R9-bound H2 replay, and the strict `<=1` unnecessary
clarification predicate are confirmed. Targeted R9+R8 provenance tests passed
15/15; no external or live execution occurred.

One P1 remains before Astra acceptance or sampling: `_regressions_against`
compares sets of omission codes, so adding another critical omission with the
same code for a different `task_index` is not reported. A direct false-pass
fixture returned `no_new_critical_regressions=true`. Compare stable omission
identities and add a same-code/different-task negative gate test in a new frozen
revision; then repeat the narrow critic and offline acceptance.

## H3 r10 final identity critic follow-up — 2026-10-09

Independent verdict: **pass**. Run identity and manifest SHA-256 were verified:
`router-v3-h3-tuning-20261009-r10`,
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.

The critical-omission comparison now preserves duplicate multiplicity and
distinguishes stable task, dependency, and failed-field identity. It reports
each added normalized record with its case ID. Direct probes rejected an added
duplicate, different task, different dependency, and changed failed fields;
record/field reorder and transient closest-match changes did not create false
regressions. Frozen inputs/evaluation resources and H2 snapshot hashes match R9,
while clarification/regression gates and the underlying scorer remain unchanged.
R10 frozen validation passed and the targeted R10/R9/R8 suite passed 18/18.
No external or live execution occurred. R10 is ready for repeat Astra offline
acceptance; this critic verdict does not itself authorize sampling.

## H3 r9 independent acceptance test — 2026-10-09

Independent verdict: **changes_required**. Correct run identity and manifest were verified: `router-v3-h3-tuning-20261009-r9`, SHA-256 `6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`. Product code was not changed; no live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used. Detailed report: `REPORT_QUESTION_PARSER_H3_R9_INDEPENDENT_TEST_20261009.md`.

Confirmed: r9 targeted acceptance tests passed 7/7; relevant offline suite passed 199/199; r9 modules passed `py_compile`; H2 snapshot launcher reproduced first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20. Frozen-only gold/baseline consumption, working-tree independence, scorer-resource/hash provenance, explicit path substitution rejection, frozen mutation rejection, separate B/H2 regression gates, `<=1` unnecessary-clarification budget, and a controlled real H3 lifecycle path were verified.

Remaining P1 blocker: `_regressions_against` still compares critical omissions by code-only sets. A direct same-code/different-task fixture (`unmatched_expected_task` on task 0 in the reference, task 1 in the new result) returns `no_new_critical_regressions=true`. This is a false pass and must be fixed before Astra acceptance or sampling. Normalize critical omission identity using stable task/evidence fields, and add a regression test for same-code/different-task omissions.

## H3 r10 critical-regression identity repair — 2026-10-09

Created immutable unreceipted R10 without modifying R9. Run
`router-v3-h3-tuning-20261009-r10`; manifest SHA-256
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
R10 pins R9 manifest SHA
`6e1974825cd52512fd66c4eb297d43e7c65f52312e3ce7ecdea4c8f8c9ed1826`.

Critical regression comparison now canonicalizes omission records and compares
multisets using stable code, task/dependency identity, and normalized failed
fields. It excludes the transient closest-actual-task hint and reports full
added records with case IDs. Regression tests cover a new task-indexed omission
when the code already existed, changed failed fields, and reordered records and
failed fields with transient hints changed.

Three R10 identity tests passed; combined R10 identity and R9 acceptance tests
passed (10 tests). `py_compile`, `validate_frozen`, and the R10 historical H2
snapshot replay passed. Historical metrics remain first15/final20, B11, H2=5;
empty R10 replay remains fail-closed. No scorer semantics, gold, gates, sampling,
live calls, holdout, Polza, Qdrant, production, or bot were changed or used.

## H3 r10 independent targeted test — 2026-10-09

Independent verdict: **PASS**. Correct run identity and manifest were verified: `router-v3-h3-tuning-20261009-r10`, SHA-256 `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`. Product code was not changed; no live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used. Detailed report: `REPORT_QUESTION_PARSER_H3_R10_INDEPENDENT_TEST_20261009.md`.

Confirmed r10 fixes the prior P1: canonical critical omission identity now uses stable record fields with multiset counts. Same-code/new-task, changed normalized `failed_fields`, changed dependency edge target, and duplicate-count negatives are detected; reordered omissions/fields and transient `closest_actual_task_index` do not create false regressions.

Results: targeted r10+r9 acceptance tests passed 10/10; relevant offline suite passed 202/202; r10 modules passed `py_compile`; r10 H2 snapshot launcher reproduced first technical 15/20, final technical 20/20, B=11/20, H2 semantic=5/20. Frozen-only scorer resource consumption, working-tree independence, substitution/tamper rejection, named gate false-pass negatives, and one controlled real H3 lifecycle path were verified. No blockers found in the targeted r10 scope.
## Limit checkpoint after H3 r10 independent PASS — 2026-10-09

H3 r10 is the current immutable candidate: run
`router-v3-h3-tuning-20261009-r10`, manifest SHA-256
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
GPT-5.5 Sol medium independent tests passed (10 targeted, 202 relevant offline),
and GPT-5.6 Sol medium critique passed with no P0/P1/P2 in the changed scope.
Historical snapshot replay remains first technical 15/20, final technical 20/20,
B=11/20, H2=5/20. No live sampling or holdout use occurred.

The five-hour Codex window reached 91% used after these checks, so the next
batch was not started. The single next step after reset is GPT-6 Astra medium
offline acceptance of r10. Only an `accept_offline` verdict may authorize the
frozen 20-case subscription tuning run; otherwise follow Astra's narrow
findings. Continue to keep Polza, holdout, Qdrant, production, and the bot out
of scope.

## Astra offline acceptance H3 r10 — 2026-10-09

Verdict: **accept_offline**. Report:
ACCEPTANCE_QUESTION_PARSER_H3_R10_ASTRA_20261009.md.
Accepted run router-v3-h3-tuning-20261009-r10, manifest SHA-256
 ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc.

Astra independently reran 202 tests, exact historical H2 replay (15/20 ->20/20,
B11/H2=5), twice-identical empty r10 semantic replay and an actual r10 copied-
fixture lifecycle/guard/repair/assembly/technical+semantic replay (S02 passed).
All r8/r9 mandatory findings are closed: frozen-only consumed scorer resources,
clarification <=1, separate B/H2 regression gates and canonical multiset identity.
No product code, gold, frozen real run or real receipts were modified.

The only next authorized sampling is this frozen r10 on the same20 tuning cases:
fresh subscription GPT-6 Luna medium classifier/simple and GPT-6 Sol medium
complex, EACH case/role/attempt in isolated context with exact pre-dispatch
envelope. Do not reuse persistent worker history. Follow receipt provenance,
one technical repair and limit checkpoints. No holdout/Polza/Qdrant/production/
bot/definition work. Useful/full tuning are not yet accepted. Thresholds remain
SPEC's >=15 useful and unchanged strict full gate.

Use normal Python invocation for replay (or consistently inherited PYTHONUTF8);
a parent-only -X utf8 diagnostic causes Windows subprocess encoding mismatch.

## Frozen H3 r10 subscription tuning result — 2026-10-09

Completed the authorized 20-case frozen subscription run for
`router-v3-h3-tuning-20261009-r10`, manifest SHA-256
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
Every classifier/parser case used a fresh isolated subscription worker with its
captured exact pre-dispatch envelope. Models were GPT-6 Luna medium for
classifier/simple and GPT-6 Sol medium for complex. All 40 receipts and bindings
were imported; no repair was required. No Polza, holdout, Qdrant, production or
bot access occurred.

Deterministic replay was complete and byte-identical across two runs. Technical
quality passed: first 20/20, final 20/20, locator-free accepted plans true, zero
critical ready losses, and no new critical regressions versus B or H2. Semantic
usefulness failed: plan match 13/20 (<15), classifier 17/20, status 16/20,
ambiguities 1/3, and unnecessary clarifications 4 (>1). Therefore `useful=false`
and `full_tuning_gate_pass=false`; holdout remains unopened.

The run report is
`router_subscription_run_tuning_v3_h3_r10_20261009/semantic_evaluation_report.json`.
At the post-run checkpoint the weekly Codex window had 6% remaining (94% used),
so no diagnosis/fix agent batch was started. Next work after renewal is a bounded
failure analysis of the seven plan mismatches and four unnecessary
clarifications, followed by a new hypothesis/spec revision through the existing
Astra/Luna/Sol roles. R10 and its receipts remain immutable.

## H4 hypothesis/spec checkpoint — 2026-10-09

The user lowered the weekly reserve threshold from 5% to 2%. GPT-6 Astra medium
completed the bounded r10 failure analysis with verdict `spec_ready` and added
the normative H4 section to `SPEC_QUESTION_PARSER_STAGE1.md`; rationale is in
`DECISION_QUESTION_PARSER_STAGE1_H4_20261009.md`.

Chosen smallest coherent hypothesis: the H3 prompts explicitly prohibit treating
flat candidate strings as aliases, which turns one surface/expansion meaning
into false ambiguity. H4 changes only the shared alias-versus-ambiguity policy in
classifier/parser prompts and their version wiring/tests. Expected recoveries are
V01, V03, V06 and U03, taking the observed 13/20 to a projected 17/20. D03 and
A02/A03 remain explicitly out of this revision. Genuine competing meanings and
documents, all 13 prior passes, technical 20/20, no-regression gates and the
clarification budget remain protected. Holdout and sampling remain closed.

After the Astra batch the weekly limit reported 99% used (1% remaining), below
the requested 2% reserve. No Luna implementation batch was started. Exact next
step after weekly renewal: GPT-6 Luna medium implements only the H4 specification
as a new immutable revision, then GPT-5.5 Sol medium tests, GPT-5.6 Sol medium
critiques, and GPT-6 Astra medium performs offline acceptance before any new
subscription sampling. Continue without Polza, holdout, Qdrant, production or bot.

## H4 implementation checkpoint — 2026-10-09

GPT-6 Luna medium implementation completed as a separate immutable H4 revision;
H3 r10 and its receipts were not edited. New run:
`router-v3-h4-tuning-20261009-r1`, directory
`router_subscription_run_tuning_v3_h4_20261009`, manifest SHA-256
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`.
It pins direct parent H3 r10 manifest SHA
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
Frozen validation reports 20 cases, no receipts, unchanged input/source/schema
hashes, and H2 historical snapshot inventory SHA
`437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`.

Implementation scope: separate `router_prompts_v3_h4.py` updates only the shared
classifier/parser alias-versus-ambiguity policy and adds the single bounded
fictional contrast block. H4 lifecycle and freeze wiring are in
`router_subscription_lifecycle_h4.py` and `freeze_router_v3_h4.py`; the H4 frozen
manifest inventories the prompt/runtime revision and reuses r10's exact tuning
inputs, schema, evaluation resources, and historical runtime snapshot. Focused
offline assertions are in `test_router_h4_offline.py`. No semantic assembler,
scorer, gold, gate, holdout, production, Qdrant, bot, or subscription behavior was
changed.

Offline results: `python -m pytest -q --basetemp=.pytest_h4_focus_tmp
test_router_h4_offline.py test_router_h3_r10_critical_identity.py` — 5 passed.
H4 frozen validation passed. Pinned H3 r10 replay reproduced first/final technical
20/20 and semantic plan 13/20. No H4 model-quality claim is made. A broader legacy
`test_router_h3_lifecycle.py` invocation had six setup failures because its H3
validator detects a pre-existing workspace executable hash mismatch for
`evaluate_router_v3_h3_semantic_replay.py`; it did not fail on the H4 revision.
The direct H4 lifecycle smoke test passed. Next offline review remains the
independent Sol critique and Astra acceptance specified in H4.4; no sampling has
been performed or authorized.

## H4 r1 independent test — 2026-10-09

Independent verdict: **PASS**. Detailed report:
`REPORT_QUESTION_PARSER_H4_R1_INDEPENDENT_TEST_20261009.md`.

Verified run `router-v3-h4-tuning-20261009-r1`, manifest SHA-256
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
direct parent H3 r10 manifest SHA-256
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`,
20 frozen cases, no receipts, and historical snapshot inventory SHA
`437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`.

Targeted offline tests and py_compile passed. Parent r10 replay reproduced the
expected frozen baseline. A disposable-copy H4 lifecycle probe verified the real
end-to-end path with frozen H4 prompts: V01/V03/V06 can validate as ready/simple
without clarification, U03 validates as deferred/complex without clarification,
and A02/A03 preserve genuine document/term clarification options. A02/A03 still
fail strict plan comparison through the known retained `scope=corpus` issue,
which remains outside H4.

The reported six legacy H3 setup failures were reproduced and traced to the
generic `evaluate_router_v3_h3_semantic_replay.py` hash mismatch expected by the
old H3 r4 validator. H4 r1 does not pin or depend on that generic file; it pins
the r10-suffixed replay file. This legacy drift does not block H4 r1 offline
acceptance. No live LLM, Polza, holdout, Qdrant, production, bot, sampling, or
frozen-code modification occurred.

## H4 r1 independent critique — 2026-10-09

Verdict: **changes_required**. Detailed critique:
`CRITIQUE_QUESTION_PARSER_H4_R1_20261009.md`.

The alias-family policy is coherent and non-case-specific. It permits
V01/V03/V06 to remain simple without artificial clarification, keeps U03
unsupported and deferred, and preserves A02 document ambiguity and A03
competing term meanings. The H4 manifest SHA-256
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
direct R10 parent pin, frozen hashes, and version wiring were independently
confirmed. The legacy generic H3 semantic-replay hash drift is outside H4's
pinned executable chain and is non-blocking.

One P1 remains: checked-in `test_router_h4_offline.py` only asserts prompt text
and one unrelated prompt-loading lifecycle smoke path. The report's manual
V01/V03/V06/U03/A02/A03 probes are not reproducible tests, and the committed
suite does not cover the spec-required alias, competing-term, competing-document,
unknown-ID, unsupported-define, or one-repair boundaries. Add focused lifecycle
tests/fixtures and record their passing command before Astra offline acceptance.
No sampling is authorized by this critique.

## H4 r1 P1 lifecycle-test correction — 2026-10-09

Added reproducible checked-in lifecycle boundary tests in
`test_router_h4_lifecycle_boundaries.py`. They run `FileSubscriptionRunnerH4`
through disposable copies of the frozen run and injected offline responses; no
live model path is involved. Coverage now locks V01/V03/V06 as ready with no
clarification while retaining object, quantity, and exclusion conditions; U03
`define` as deferred without clarification; A02/A03 as document/term
clarifications with exact supplied options; unknown candidate IDs as rejected
even after the one allowed repair; and the one-repair cap with terminal failure.

The H4 r1 manifest does not pin tests: `test_router_h4_offline.py` and test
inventories are absent from both `files_sha256` and `code_files_sha256`. The
test addition therefore does not alter the immutable H4 r1 run, and repository
freeze convention does not require a prompt/runtime successor for test-only
coverage. Kept r1 unchanged; frozen manifest SHA-256 remains
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`, with
direct parent `router-v3-h3-tuning-20261009-r10` at
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.

Verification:

```text
python -m pytest -q --basetemp=sandbox\skill_router_lab\.pytest_h4_boundaries_tmp sandbox\skill_router_lab\test_router_h4_offline.py sandbox\skill_router_lab\test_router_h4_lifecycle_boundaries.py sandbox\skill_router_lab\test_router_h3_r10_critical_identity.py
14 passed
python -m py_compile sandbox\skill_router_lab\test_router_h4_lifecycle_boundaries.py
passed
python -c "import sys; sys.path.insert(0, 'sandbox/skill_router_lab'); from freeze_router_v3_h4 import RUN_DIR, validate_frozen; print(validate_frozen(RUN_DIR))"
status=h4_r1_frozen_inputs_valid, 20 cases, no receipts
python sandbox\skill_router_lab\replay_router_h2_snapshot_r10.py
status=h3_r10_frozen_inputs_valid, first technical 15/20, final technical 20/20
python sandbox\skill_router_lab\evaluate_router_v3_h3_semantic_replay_r10.py
deterministic replay; semantic plan 13/20; full tuning gate false (expected parent baseline)
```

## H4 r1 P1 lifecycle-test independent verification — 2026-10-09

Independent verification of the checked-in P1 test correction passed. Reviewed
`test_router_h4_lifecycle_boundaries.py` against
`CRITIQUE_QUESTION_PARSER_H4_R1_20261009.md` and H4.4. The tests run
`FileSubscriptionRunnerH4` plus the real assembler on disposable copies of the
frozen H4 run, with injected offline outputs; they are not prompt-substring-only
checks and do not mutate frozen artifacts.

Confirmed coverage: V01/V03/V06 ready with no false clarification and retained
object/quantity/exclusion constraints; U03 deferred with no alias
clarification; exact A02/A03 document/term clarification options; unknown
candidate IDs fail closed after the single repair; invalid parser output gets
only one repair and remains terminal; frozen validation/provenance still report
manifest SHA-256
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
direct parent r10
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`, 20 cases,
and no receipts.

Verification:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_tmp_h4_boundaries_verify test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_h3_r10_critical_identity.py
14 passed in 0.92s
python -m py_compile freeze_router_v3_h4.py router_subscription_lifecycle_h4.py router_prompts_v3_h4.py test_router_h4_lifecycle_boundaries.py test_router_h4_offline.py
passed
python -c "from freeze_router_v3_h4 import RUN_DIR, validate_frozen; print(validate_frozen(RUN_DIR))"
status=h4_r1_frozen_inputs_valid, 20 cases, no receipts
```

An earlier run without `--basetemp` failed before test execution because pytest
could not scan the host temp directory. The recorded passing commands use a
writable workspace basetemp. No live LLM, sampling, Polza, holdout, Qdrant,
production, bot path, implementation edit, or frozen artifact modification
occurred.


## H4 r1 Astra final offline acceptance — 2026-10-09

Verdict: **accept_offline**. See
`ACCEPTANCE_QUESTION_PARSER_H4_R1_ASTRA_20261009.md` for evidence and exact
authorization. Independently reran focused/relevant suites: **120 passed in
4.27s**. Validated immutable H4 manifest
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`, exact
r10 parent, unchanged common runtime/input/schema/evaluation resources, 20
cases and no receipts. P1 lifecycle test correction is closed. Real H4
boundary tests plus a precise-span V06 probe passed; unknown IDs and single
repair remain fail-closed. Empty/partial parent replay remains fail-closed.
Read-only replay reproduces H2 15→20, B11/H2 semantic5; r10 technical20,
semantic13, classifier17, status16 and ambiguity1/3, twice identically.

Authorized next action only: fresh 20-case frozen H4 subscription tuning under
this exact manifest, Luna medium classifier/simple and Sol medium complex,
fresh isolated case/role/attempt workers and exact envelopes. No H4 quality
claim yet. Preserve H4.5 gates, 13 r10 passes and true A02/A03 clarification;
D03/A02/A03 known defects remain explicit. Check usage before batches, start
below five-hour90%, stop at five-hour95% or weekly98%, preserving 2% weekly
reserve. No live call or sampling was performed during acceptance; no holdout,
Polza, Qdrant, production, bot, definition task, implementation change or
frozen artifact modification occurred.

## H4 r1 subscription sampling limit checkpoint — 2026-10-09

Started the authorized frozen H4 subscription run with exact pre-dispatch
envelopes and a fresh isolated worker for every case/role/attempt. Coordinator:
`prepare_router_subscription_h4.py`. All 20 GPT-6 Luna medium classifier
receipts are imported and bound. Classifier outputs route 8 cases to the simple
parser and 12 to the complex parser.

Nine first parser attempts are also imported and technically valid: S01, S02,
S03, S04 (GPT-6 Luna medium), C01, C02, C03, V01 (GPT-6 Sol medium), and V03
(GPT-6 Luna medium). No repair envelope has been needed. Current immutable run
state: 40 prepared envelopes, 29 receipts, 29 bindings, 0 repairs.

Remaining parser cases, in frozen order: V06, D01, D02, D03, D04, A01, A02,
A03, U01, U02, U03. Their exact attempt-1 envelopes already exist under
`h4_sampling_envelopes`; raw finals must continue to be imported through the
H4 coordinator. Do not resample completed roles.

The five-hour window reached 90% used after importing V03, so no new batch was
started. Resume after reset, still stopping before the 95% reserve boundary.
After all parser receipts (and at most one required repair per invalid case),
run deterministic technical and semantic H4 replay against H4.5 gates. Holdout
remains closed. No Polza, Qdrant, production or bot use.
