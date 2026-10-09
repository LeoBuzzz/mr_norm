# Astra final offline acceptance — H3 r10 — 2026-10-09

Verdict: **accept_offline**.

Accepted run: router-v3-h3-tuning-20261009-r10.
Accepted manifest SHA-256:
ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc.
This accepts the offline implementation and frozen experiment preparation only.
It does not accept model quality, useful/full tuning gates, or production use.

## Evidence and closed findings

- Independently reran the relevant offline suite: 202 passed in 3.80s, with
  workspace-local pytest basetemp. Read latest independent PASS and critic PASS.
- Frozen validation returns the exact r10 identity/hash, 20 cases, r9 lineage,
  snapshot inventory, no real sampling receipts. Classifier, input rows/order,
  schema and semantic matching scorer remain unchanged under frozen checks.
- r8 resource finding closed: the H3 semantic evaluator validates and consumes
  r10/evaluation gold and baseline only, rejects unchecked alternate gold paths,
  and reports hashes and exact consumed paths. Checkout path globals are unused.
- r8 gate finding closed: unnecessary clarification count is measured, and <=1
  is required by full gate. B and H2 regressions have separate named predicates;
  H2 reference comes from pinned isolated historical replay.
- r9 false-pass finding closed: omission comparison uses canonical stable record
  identities and Counter multiplicity. New task, edge, failed fields or duplicate
  records are detected; record/failed-field ordering and closest-match hints do
  not spuriously change identity. No semantic scorer relaxation was introduced.
- Historical snapshot replay independently reproduced first technical15/final20,
  B11/H2 semantic5. Hash/resource/snapshot/envelope tamper protections remain.
- Two read-only semantic replays of the real empty r10 were identical: receipt0,
  complete=false, useful=false, full=false. Actual frozen resource provenance
  names r10 and the prescribed manifest SHA.
- Additional real r10 lifecycle probe on a disposable COPY of the frozen run:
  S02 classifier -> frozen parser -> invalid locator topic -> H3 guard -> one
  repair -> unchanged assembly -> receipt technical replay -> semantic replay.
  Three mock receipts, first technical=false, final=true, repair=succeeded,
  lifecycle/replay plans identical and S02 semantic plan matched. Real r10
  directory and receipts were untouched; this is offline fixture evidence,
  not subscription sampling. Fixture: astra-r10-e2e-zb9t2vi1/run.

No mandatory acceptance findings remain. Existing C02 measurement limitation
and model/ambiguity/classification risks remain tuning observations, not changes
silently added to H3. The useful threshold stays >=15/20 (B+4), first technical
>=19/20, final20/20, no new critical regressions versus B and H2. Full strict
criteria in SPEC remain unchanged, including classifier18/all11 complex,
semantic/status18, all3 ambiguities, <=1 unnecessary clarification, paired
V03/V06 and zero prohibited ready-plan losses.

## Precisely authorized next action

Authorize ONLY frozen router-v3-h3-tuning-20261009-r10, manifest above, on the
same 20 tuning cases through Codex subscription. Use fresh GPT-6 Luna medium
for classifier/simple and fresh GPT-6 Sol medium for complex. EACH case/role/
attempt must have an isolated context (no fork and no reuse of prior responses
or task history), with the exact canonical pre-dispatch envelope. This explicit
isolation requirement takes precedence over older wording about two persistent
workers. No gold, reports, source browsing, tools or failure hints in workers.

Persist envelope before dispatch, raw observed response before validation,
request/receipt binding and honest metadata. At most one technical repair;
route using observed classifier, never gold. No prompt/schema/input/model/code
changes after dispatch. Unknown server metadata remains null. Freeze/hash drift
aborts the run; no silent resampling. No batch at >=90% five-hour usage; stop
and checkpoint at >=95%. Sampling is not performed by this acceptance task.

No holdout, Polza, Qdrant, production, bot or definition-task work is authorized.

## Operational note

A diagnostic launched with parent-only `python -X utf8` hit a Windows child
stdout encoding mismatch; the supported normal Python invocation reproduced
successfully. Run replay with normal interpreter defaults, or a consistent
inherited PYTHONUTF8=1 across parent and child. This is an invocation caveat,
not a reason to change the accepted immutable code before this experiment.

Only this acceptance report and checkpoint were edited. Offline tests created
workspace-local temporary fixtures. Product code, gold and frozen artifacts
were not changed; no live model calls occurred.
