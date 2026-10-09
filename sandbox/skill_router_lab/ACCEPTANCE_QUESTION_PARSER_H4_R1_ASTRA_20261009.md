# Astra final offline acceptance — H4 r1 — 2026-10-09

Verdict: **accept_offline**.

Accepted run: `router-v3-h4-tuning-20261009-r1`.
Manifest SHA-256: `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`.
Direct parent: `router-v3-h3-tuning-20261009-r10`, manifest
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.

This accepts the offline implementation and frozen experiment preparation.
It does not establish H4 model quality, useful/full tuning success, stage1
completion, or production readiness. No sampling occurred in this acceptance.

## Independent review and frozen scope

Read the normative H4.1–H4.5 specification, H4 decision, parent r10 acceptance
and evidence, independent H4 test report including correction verification,
and H4 critique including its P1 correction follow-up. Inspected the H4 prompt,
freeze, lifecycle and checked-in tests directly; did not rely only on verdicts.

`validate_frozen(RUN_DIR)` independently passed with the exact manifest above,
20 cases, exact r10 lineage, no receipts, and historical snapshot inventory
`437c4f641e0a140b9a517519d0dfc5e472257243ed667e314dde1f1e473f5fcf`.
Every pinned code and frozen-file hash passed. Comparing H4/r10 inventories
found no common runtime-code hash drift; the only differing common resource
hashes are `prompts/classifier.txt` and `prompts/parser.txt`. Repair prompt,
inputs/order/candidates/context, source manifests, schema, gold and evaluation
resources remain unchanged. H3 parent validation also passes.

Direct lifecycle diff against r10 contains only H4 version/import/class/error
labels. Validation, assembly, status precedence, candidate preparation,
receipt/envelope mechanics, observed-classifier role selection, and one-repair
policy are unchanged. Prompt diff is confined to alias-versus-ambiguity policy
and one fictional ZQ/QX contrast. No tuning case IDs, real tuning terms or
per-case branch logic were added to that policy. Existing synthetic SourceRefs
are unchanged; the new contrast has no SourceRefs needing token ordinals.
There is no new dictionary, normalization, meaning-group protocol, scorer,
parser framework, D03 fix, or hidden A02/A03 representation fix.

## Executable evidence

From `sandbox/skill_router_lab`, independently executed:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h4_astra_accept_tmp test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py test_router_h3.py test_router_h3_r5.py test_router_h3_r6.py test_router_h3_r7.py test_router_h3_r8_provenance.py test_router_h3_r9_acceptance.py test_router_h3_r10_critical_identity.py test_router_draft_v2.py test_router_v2_replay_eval.py
120 passed in 4.27s
```

The real H4 lifecycle tests operate on disposable frozen-run copies with
injected deterministic outputs. V01/V03/V06 route through simple_parser and
assemble ready without clarification; U03 routes through complex_parser and
assembles deferred with unsupported_intent and no alias clarification. A02/A03
retain exact document/term clarification options. Unknown candidate IDs fail
closed after one repair; a second invalid response is terminal. Tests assert
role/attempt sequence and assembled data, closing the prior P1 test gap.
These injected drafts prove boundary compatibility, not model adherence.

The broad-span drafts in the checked-in boundary tests deliberately test
plumbing rather than fine-grained evidence semantics. An additional independent
V06 real-path probe used operation all, term/object `current@6:6`, quantity
`current@7:8`, exclusion `current@12:13`, exact supplied expansion ID and literal
topic. It assembled ready/simple, no clarification, concept surface ВЛ with
variant воздушная линия, quantity 110 кВ, and excluded кабельных линий.
The persistent disposable fixture is `astra-h4-accept-d2mvrm9m/h4`; its saved
exact envelopes and raw receipts reproduce the probe without live calls.

Inherited tests passed for topic literal/point SourceRef guards, exact
one-to-one clarification options, canonical critical omission identity and
multiplicity, dependency/constraint protection, frozen resource substitution,
snapshot tamper, and runtime/replay assembly parity. On a separate disposable
r10 copy (`astra-h4-accept-d2mvrm9m/empty-r10`), I independently replayed an empty
receipt set and then a classifier-only set: both kept complete_coverage,
useful and full_tuning_gate_pass false. The final copy retains the partial set.
No frozen originals were altered.

Read-only `replay_h2_snapshot()` reproduced first technical 15/20, final 20/20,
B=11/20 and H2 semantic=5/20. Two read-only `semantic_replay_h3()` calls from
the r10-suffixed module returned byte-identical substantive results: r10
first/final technical 20/20, semantic 13/20, classifier 17/20, status 16/20,
ambiguity 1/3, four unnecessary clarifications V01/V03/V06/U03, no new critical
regressions versus B/H2 and no critical ready losses. Replay functions were
called directly to avoid overwriting the parent's saved reports.

The previously reported generic H3/legacy-v3 hash/receipt-fixture failures
remain documented legacy-suite debt, not an H4 pass claim. H4 uses the pinned
r10-suffixed replay chain, and the relevant suite above passed independently.

## Precisely authorized next action

Authorize ONLY a fresh 20-case Codex subscription tuning dispatch into the
accepted, currently unreceipted H4 r1 run under the exact manifest above.
Preserve cases/order, gold/scorer, all frozen prompts/runtime/resources and
model roles: GPT-6 Luna medium classifier/simple, GPT-6 Sol medium complex.
Each case/role/attempt must use a fresh isolated worker with no fork, prior
answers or chat history and the exact canonical envelope only. Do not expose
gold, reports, source browsing, tools or failure hints to sampling workers.

Persist each immutable envelope before dispatch and raw observed receipt with
binding before validation. Preserve honest requested-versus-actual metadata;
unknown actual model stays null. Route from observed classifier; permit at
most one technical repair and no semantic retry or silent resampling. No
prompt/code/input changes after first dispatch; any frozen drift aborts.

Check limits before each batch: start only below 90% five-hour usage, retain
the prior five-hour stop at 95%, and stop/checkpoint at weekly used >=98%.
Do not launch a batch estimated to consume the user's 2% weekly reserve.

Evaluate H4 useful and strict full criteria exactly as H4.5 states, including
retaining all 13 r10 plan passes, genuine A02/A03 clarification, separate
B/H2/r10 critical comparisons and two identical deterministic replays. The
anticipated 17/20 is a hypothesis, not a result. D03 and A02/A03 corpus-scoped
unresolved-task defects remain explicit; useful success would not waive full
acceptance or open holdout. If H4 fails, preserve results and stop this
experiment; a further hypothesis needs a new decision.

No holdout, Polza, Qdrant, production, bot or definition-task work is authorized.
Only this report and the checkpoint were edited; tests/probes created local
disposable fixtures. Implementation and frozen artifacts were not modified.
