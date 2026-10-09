# Technical checkpoint: question parser stage 1, v2

**State:** implementation checkpoint saved; independent tests, critique, and
acceptance are still pending. No tuning workers were started.

## Implemented

- Added the semantic draft v2 wire schema and fail-closed validator. SourceRef
  uses `source_tokens.v1` token ordinals and deterministic exact source slicing.
- Added deterministic assembly of `skill_query_plan.v2`: task IDs, skill and
  coverage mapping, exact evidence, constraints/concepts, dependencies,
  clarification issues, unsupported/deferred status, and provenance trace are
  code-generated.
- Added `plan_skill_query_v2` as an explicit entry point. The established
  `plan_skill_query`, `Route`, and `route_query` remain compatible with v1.
- Added a two-stage v2 classifier/parser prompt path, strict raw content plus
  adapter metadata contract, and one same-role parser repair for contract or
  reference validation errors. Transport/classifier failures do not fallback.
- Preserved the v1 pending/dialog behavior and added v2 checks that reject
  malformed pending state before LLM calls, preserve unmatched pending
  clarification, and verify selected/open resolutions against the saved task.
- Added tuning-only migration from the already archived 20-record tuning report.
  V03/V06 remain tuning and are tagged as one metamorphic pair. No holdout
  records were read into the migration.
- Added immutable file-freeze helpers, raw receipt validation and append,
  hash-checked file-only `ReplayAdapter`, v2 semantic and paired evaluators, and
  an offline frozen-input gate. The model input JSONL contains no expected/gold
  fields or split labels.
- Follow-up critic fixes: implicit document ambiguity is limited to `text`;
  unsupported operations remain deferred and `unknown` requests clarification.
  V2 rejects stale pending/context document IDs against the validation catalog
  before adapter calls. Repair calls retain classifier output and the complete
  original payload with raw failed response and validation errors.
- The parser prompt now embeds the complete compact normative JSON schema,
  including optional nested shapes. Abbreviation meaning groups carry stable
  readable canonical IDs/labels. The evaluator checks operation enum,
  constraints separately, source evidence coverage, clarification field/subject
  and allowed options, plus exact dependency edges after semantic one-to-one
  task matching. Receipt validation checks requested model/effort and marks
  incomplete role sets as partial. Early failures aggregate known physical
  requests and return null when an attempted call has unknown count.
- Final evaluator correction: required semantic elements are checked in their
  normative output field (`topic` in query, `object`/`concept` in concepts,
  condition/quantity/exclusion in constraints, intent by operation, point by
  point reference). The V03 regression passes with a short topic query, a `ВЛ`
  concept, and three separate constraints. Clarification choices now require
  exact one-to-one option matching: canonical IDs and their deterministic labels
  when IDs are specified; otherwise unique exact normalized labels.

## Frozen tuning inputs

- `router_plan_cases_tuning_v2.json` SHA-256:
  `386e7d682cf5153dd9e5bba266e348eb621fc6cfd6378b2af51c62e4609c14fe`
- `router_source_manifests_tuning_v2.json` SHA-256:
  `0668beebfae1879abb93988cdffef181ce1e1b9741f8fa3a08edf1b3bb5fc5be`
- Subscription file-replay manifest:
  `router_subscription_run_tuning_v2_criticfix3_20261008/manifest.json`
  SHA-256 `29388141657d47bed126e01fda28ce8a6158d06e1caefd56f44175fe48c511ad`.
- Updated evaluator code hash in the manifest:
  `66c11fb5b505c66501c8413bfa8c863db285017c52ddad60f71c806b7d897535`.
- Manifest records 20 tuning case IDs/order, prompt/schema/input and source
  hashes, code file hashes, requested Luna/Sol medium models, and
  `supports_json_schema=false` for the file-replay transport.
- Offline gate result: `offline_frozen_inputs_valid`, 20 cases, zero receipts,
  `live_workers_started=false`.

## Verification and remaining limits

Addressed v2 regression command:

```text
python -m pytest sandbox/skill_router_lab/test_router_draft_v2.py -q
37 passed
```

`run_router_v2_offline_gate.py` passed with `offline_frozen_inputs_valid`, 20
tuning cases, and `awaiting_subscription_receipts`. `py_compile` passed for all
changed Python modules. No live LLM, Polza, network, Qdrant, bot, production
route, or holdout execution occurred.

Remaining before any subscription sampling: independent test/critic review and
acceptance; verify the future worker host's actual model/effort metadata and
receipt collection. Current code-side semantic metrics do not establish model
quality or complete source coverage; the tuning gate and holdout decision have
not been run. This checkpoint does not declare stage acceptance.

## Limit checkpoint after repeated critique — 2026-10-07

Paused at **92% used** in the five-hour Codex window, leaving 8% and obeying the
rule not to start a new batch at or above 90%. No subscription eval worker has
been started.

The first independent test pass found and Luna fixed `physical_call_count`.
The next review cycle closed six of eight critic groups. Sol 5.5 then reported
130 relevant tests passing, `py_compile` passing, and the offline frozen gate
valid with 20 tuning inputs, no gold, and no receipts.

The short Sol 5.6 re-critique still reports exactly two P1 evaluator blockers in
`router_subscription_eval.py`:

1. Semantic elements already represented by `concepts` or `constraints`
   (`object`, `condition`, `quantity`, `exclusion`) are still also required in
   `task.query`. Route expectations by representation: topic to query, concept
   to concepts, and condition/quantity/exclusion to constraints. The real V03
   probe must then pass without duplicating every element in query.
2. Clarification option evaluation accepts a non-empty subset. Require complete
   one-to-one coverage of expected canonical IDs (or deterministic label
   matching), rejecting omissions, duplicates, and extras.

Next steps after the five-hour reset, without scope expansion:

1. GPT-6 Luna medium makes only the two evaluator fixes and adds targeted tests.
2. GPT-5.5 Sol medium repeats targeted plus relevant full tests/offline gate.
3. GPT-5.6 Sol medium performs a short review of only those two fixes.
4. GPT-6 Astra medium performs final offline acceptance. If accepted, start the
   frozen subscription tuning run with fresh GPT-6 Luna medium and GPT-6 Sol
   medium eval workers. Do not use Polza, and do not open holdout.

## Offline acceptance — 2026-10-08

GPT-6 Astra medium returned **accept_offline** after an independent run of 154
tests, the frozen offline gate, recursive no-gold inspection, and review of the
two final evaluator fixes. The accepted frozen run is
`router_subscription_run_tuning_v2_criticfix3_20261008`, manifest SHA-256
`29388141657d47bed126e01fda28ce8a6158d06e1caefd56f44175fe48c511ad`.

Offline acceptance permits the subscription tuning run with fresh GPT-6 Luna
medium (classifier/simple parser) and GPT-6 Sol medium (complex parser). It does
not accept model quality or authorize holdout. Polza remains prohibited for this
run.

## Subscription tuning replay — 2026-10-08

- Added `evaluate_router_v2_tuning.py` and
  `test_router_v2_replay_eval.py`. The runner uses only the frozen 20-case
  tuning inputs, their separate tuning gold, `ReplayAdapter`, the authoritative
  v2 validator/assembler, and deterministic semantic comparators. It performs
  two complete replays and requires matching substantive JSON before writing
  `router_subscription_eval_tuning_v2_20261008.json`.
- Report SHA-256:
  `1e6652ba32703506c631c1dda0c844a96297de4464f9cad69585a1bda33d2dfe`.
  Substantive replay SHA-256, excluding `generated_at`:
  `62f34708578ce50cad5fc602d93cdf1bfba697bf4f16abe62d2d85b15c213971`.
- Results: wire/schema validity 20/20; first-draft technical validity 16/20;
  classification 17/20 (8/11 expected-complex cases classified complex);
  four cases required repair, three recovered; final technical validity 19/20;
  assembler errors
  0; semantic plan 7/20, expected status 18/20, source/evidence coverage 8/20
  cases, and exact dependency edges 10/20. The tuning gate is
  `tuning_failed`. Critical omissions are itemized per case in the report.
- Clarification: 3 ambiguous cases, fields matched 2/3, subjects 1/3, options
  2/3; no unnecessary clarification on expected-ready cases. The V03/V06
  paired check passed. Receipt coverage is 44/44: classifier 20, simple parser
  15, complex parser 9, covering all 20 cases.
- Metadata limits are explicit: all 44 receipts are
  `requested_by_host_only`; server `actual_model`, event IDs, per-message
  timestamps, usage, and physical request counts were unavailable. Worker
  transcript hashes were also unavailable. Timestamp fields are host-
  unavailable markers. The report makes no server-side model verification or
  cost/request-count claim. Retry receipts are assessed by whether the second
  captured parser response validates; the host did not provide the repair
  message wrapper for independent prompt verification.
- Replay test plus v2 suite: 39 passed. `py_compile` passed for the runner and
  tests. Offline gate reports `offline_frozen_inputs_valid`, 20 cases,
  `receipts_validated`, and no workers started during replay. Inputs, prompts,
  schema, receipts, and gold were not modified; holdout was not opened.
