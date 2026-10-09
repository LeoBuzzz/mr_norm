# Astra offline acceptance — H3 r8 — 2026-10-09

Verdict: **changes_required**. Subscription sampling is not authorized yet.
Reviewed run: router-v3-h3-tuning-20261009-r8.
Manifest SHA-256: ce52767e4b1e5c6e21f3f7e7f5a3bed56d77943a3ae86eb31ac6a73e22453d50.

## Confirmed closed

Independent rerun: 192 passed in 2.78s using workspace-local pytest basetemp.
The initial run without that option had 184 passed / 8 setup errors caused
only by Windows access denial to pytest's default temp directory; the same
suite passed after redirecting temporary test data inside the workspace.

Frozen r8 validation passes. The snapshot launcher uses r8's 16-file historical
runtime and frozen evaluator resources, rejects resource/snapshot substitution,
and reproduces H2 first15/final20, B11 and semantic5. H2 snapshot provenance
fixes are accepted. Classifier/schema/input parity checks pass. Topic guard,
repair, assembly and receipt replay share the real lifecycle path; tests cover
exact-envelope/hash tampering and gate boundaries. Empty r8 has zero receipts,
useful=false and full_tuning_gate_pass=false. No sampling or external tools ran.

The latest critic PASS and independent report are consistent with their tested
scope. Two remaining acceptance requirements concern the NEW H3 semantic
scorer, not the historical H2 launcher that r8 fixed.

## Mandatory corrections before sampling

### P1: new H3 semantic replay still reads mutable working-tree gold/baseline

In evaluate_router_v3_h3_semantic_replay_r8.py, GOLD_PATH and BASELINE_PATH
point to HERE/router_plan_cases_tuning_v2.json and
HERE/baseline_reconciled_v3_20261008.json. semantic_replay_h3 reads those paths
(default gold_path can also be supplied arbitrarily). validate_frozen verifies
r8/evaluation copies, but these are not the bytes subsequently consumed by
this NEW scorer. The historical snapshot launcher correctly uses the frozen
copies; that fix does not propagate to the H3 scoring path.

Minimal fix in a new immutable revision: consume gold and baseline only from
that run's evaluation directory after validating their pinned hashes, or verify
any explicitly allowed alternative against the exact pinned hash before load.
Reject arbitrary unchecked gold_path. Record actual consumed hashes in output.
Add regression probes against changed working-tree gold/baseline and an alternate
gold_path: irrelevant checkout drift must not affect scoring, and substituted
unapproved evaluator bytes must fail closed. Keep semantic matching rules intact.

### P1: useful/full gate omits two explicit specification criteria

semantic_replay_h3 computes regressions only against baseline case_scores;
there is no H2 regression comparison. It also never computes/counts unnecessary
clarifications, so full_tuning_gate_pass does not enforce the <=1 requirement.
Two false clarifications can coexist with semantic/status18 and all three
required ambiguities correct. Thus the other checks do not imply this criterion.

Read-only gate probe with every current predicate satisfied returns useful=true
and full_tuning_gate_pass=true; adding unnecessary_clarifications=2 or an H2
regression signal has no effect because these predicates are absent.

Minimal fix: compute unnecessary clarification count from expected status versus
actual needs_clarification, expose a named <=1 predicate in the full gate. Compute
per-case critical regression diffs independently against frozen B and frozen H2
results, expose both predicates and require both for useful/full. Obtain H2
reference data through pinned historical replay or freeze a derived report with
explicit provenance, never unverified working-tree results. Include separate
negative tests for two unnecessary clarifications and H2-only regression while
all other gate predicates pass. Do not weaken existing criteria.

## Scope and next step

No product code, gold, frozen r8/H2 artifacts or receipts were modified by this
acceptance. Only this report and checkpoint were written. Preserve r8 as an
immutable unreceipted predecessor; make the narrow evaluator/provenance fixes
in a new explicit revision, rerun changed-area tests and critique, then repeat
Astra offline acceptance. No classifier/prompt/runtime semantic changes needed.

A later accept_offline may authorize only the named frozen revision on the same
20 tuning cases, fresh subscription GPT-6 Luna medium classifier/simple and
GPT-6 Sol medium complex, each case/attempt in isolated context with the exact
pre-dispatch envelope. This report does not authorize r8 sampling, live calls,
holdout, Polza, Qdrant, production or bot activity.
