# H4 r1 independent critique — 2026-10-09

Verdict: **changes_required**. No P0 finding. The H4 prompt policy, version
wiring, immutable provenance, and parent R10 pin are coherent, but one P1 test
gap remains before offline acceptance.

Reviewed immutable run `router-v3-h4-tuning-20261009-r1`. The manifest SHA-256
independently matches
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`;
`validate_frozen()` reports 20 cases, no receipts, and direct parent
`router-v3-h3-tuning-20261009-r10` at manifest SHA-256
`ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`.
The frozen input/source/schema/evaluation hashes match R10, and the H4 manifest
binds its prompts and executable runtime files. The targeted suite currently
passes 5/5.

The classifier and parser state one consistent, non-case-specific rule:
candidate-string count, ordering, and matching initials do not establish
equivalence; a contextually established surface/expansion family can resolve to
one exact supplied ID; competing meanings and distinct document IDs remain
uncertain. The fictional ZQ/QX contrast contains no tuning literals. Applied to
the frozen inputs, that policy permits V01/V03/V06 to remain one-result simple
requests, keeps U03 complex because `define` is unsupported and therefore
deferred, preserves A03's two competing expansions plus surface ID, and
preserves A02's two document IDs. It does not disguise the known A02/A03
`scope=corpus` representation defect.

## P1 — the central H4 lifecycle boundary is not preserved by checked-in tests

`test_router_h4_offline.py:12-17` checks prompt substrings, and
`test_router_h4_offline.py:20-42` runs one unrelated frozen case only to prove
prompt loading and simple-parser role selection. It has no executable assertions
for the required alias-family draft, term ambiguity, document ambiguity,
unsupported define/deferred behavior, unknown-ID rejection, or one-repair cap.
Those are the exact mandatory focused checks in
`SPEC_QUESTION_PARSER_STAGE1.md:326-329`.

The independent test report describes useful disposable-copy probes for
V01/V03/V06/U03 and A02/A03, but it supplies neither a checked-in test nor a
reproducible probe command/fixture. Consequently a later prompt/lifecycle change
could invert the H4 boundary while the committed H4 suite still passes. Injected
drafts cannot prove future model adherence, but they can and should lock the
validator/assembler/role/status boundary that the offline gate claims to cover.

Required correction: add focused lifecycle tests with fictional inputs (or
checked-in reusable probe fixtures) that assert: alias family -> simple/no
clarification while preserving object, quantity, and exclusion; define with a
single alias family -> complex/deferred; competing term meanings -> complete
term clarification; two document IDs -> complete document clarification;
unknown IDs fail validation; and no more than one repair attempt is dispatched.
The V01/V03/V06/U03 and A02/A03 frozen rows may additionally be used as wiring
regressions, without asserting model quality. Re-run the focused suite and
record the exact command/result before Astra offline acceptance.

## P2 — none

The legacy generic H3 drift is non-blocking for H4 r1. The reported mismatch is
for `evaluate_router_v3_h3_semantic_replay.py`, while H4's executable chain uses
and pins `evaluate_router_v3_h3_semantic_replay_r10.py`; H4 validation also
revalidates the exact R10 parent. The failing legacy H3 fixture therefore does
not participate in H4 dispatch, validation, assembly, or R10 replay. It should
remain documented as legacy-suite debt and must not be represented as an H4
suite pass.

No live LLM, sampling, Polza, holdout, Qdrant, production, or bot path was used.
This verdict does not authorize sampling.

## P1 correction follow-up — 2026-10-09

Verdict: **pass**. No P0, P1, or P2 finding remains for the H4 r1 offline
diff. `test_router_h4_lifecycle_boundaries.py` closes the prior P1 with
checked-in, deterministic real-path tests through `FileSubscriptionRunnerH4`,
the frozen validator, and the production assembler on disposable copies of the
H4 run. The assertions are non-tautological: they verify dispatch role and
attempt sequence, assembled status/clarification/concept/constraint behavior,
exact document and term option preservation, unknown-ID rejection, and
terminal failure after the single allowed repair. The frozen V01/V03/V06/U03
and A02/A03 rows serve as checked-in reusable fixtures; no required H4.4
alias/ambiguity lifecycle boundary remains only a manual probe.

Independent command:
`python -m pytest -q -p no:cacheprovider --basetemp .pytest_critique_followup_tmp test_router_h4_lifecycle_boundaries.py test_router_h4_offline.py test_router_h3_r10_critical_identity.py`
returned **14 passed in 0.94s**. The manifest remains
`4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`;
`validate_frozen()` still reports 20 cases, the exact H3 r10 parent, and no
receipts. Test files are absent from `code_files_sha256`, so adding the focused
test is compatible with r1 immutability. Every pinned code hash and frozen-file
hash matches; no prompt, runtime code, schema/input/evaluation resource, or
frozen artifact drift was found. No live call, sampling, Polza, holdout,
Qdrant, production, or bot path was used.
