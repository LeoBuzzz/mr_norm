# H5 r3 independent critique — 2026-10-10

Verdict: **changes_required**.

The bounded H5 prompt policy is general rather than keyed to tuning case IDs, the parser has exactly one concluding `Before returning JSON, self-check` block, and the checked-in r3 directory currently has 20 ordered inputs and no receipt/envelope/binding JSONL files. The prompt preserves genuine competing meanings and document IDs, and the focused injected-output tests do not claim model quality. Those positive properties do not clear the offline gate because the exact-manifest trust anchor is bypassable and the required prior regression suite is red. No sampling or live/model/network path was used in this critique.

## P1 — exact-manifest pin is controlled by an unpinned module

`freeze_router_v3_h5.py:19` defines `EXPECTED_MANIFEST_SHA256`, and all three entry points ultimately trust that value. But `freeze_router_v3_h5.py:21` omits `freeze_router_v3_h5.py` from `CODE_FILES`; the manifest's `code_files_sha256` and the inventory's implementation map therefore do not authenticate the file that supplies the root hash and lineage validator. The test inventory also contains only the three test files.

This makes the coherent-substitution negative test incomplete. It proves rejection while the unpinned trust-anchor file is assumed honest. In a disposable copy I changed only manifest transport text, updated `hash_inventory.json.manifest_sha256`, and patched the three imported `EXPECTED_MANIFEST_SHA256` values to the substituted hash, which is exactly what changing the unpinned freeze module before process start achieves. `validate_frozen()`, `H5SubscriptionCoordinator`, and `FileSubscriptionRunnerH5` all accepted the substituted manifest. Output:

```text
validator True
coordinator router-v3-h5-tuning-20261009-r3
lifecycle router-v3-h5-tuning-20261009-r3
```

The r3 report's second negative phase actually demonstrates this trust-anchor assumption by temporarily patching all three expected-hash constants. Lineage mismatches still fail behind the pin, but a non-lineage manifest substitution succeeds once the unpinned pin is changed. Therefore the exact-manifest/tamper gate is not non-circular and effective under the stated pinned-byte model. A successor revision needs an independently authenticated trust anchor or acceptance artifact; editing r3 would violate immutability.

## P1 — H5.4 does not permit acceptance with the 12 H4 failures

I reran the three H4 suites read-only:

```text
python -m pytest -q -p no:cacheprovider --basetemp .pytest_h5_critique_h4 \
  test_router_h4_offline.py test_router_h4_lifecycle_boundaries.py \
  test_router_subscription_h4_coordinator.py
12 failed, 1 passed in 2.18s
```

The failure diagnosis in the r3 test report is accurate. One test asserts an empty H4 run although the frozen run now has receipts; the other failures copy that populated run and then correctly hit duplicate-attempt or nonempty-start guards. I found no evidence in these traces of an H5 behavioral regression. They are stale empty-run fixture debt.

That diagnosis does not waive the gate. H5.4 line 497 requires the focused and relevant previously passing suite to **PASS**, and line 522 separately requires independent tests to **PASS**. It contains no exception for fixture debt. The r3 report itself calls the result a literal exception, then gives a PASS verdict. Those statements cannot both satisfy the normative spec. The stale tests must be made runnable against an empty fixture in an allowed successor/review arrangement, or the spec must explicitly authorize and define a replacement regression proof before acceptance. Until then `accept_offline` and sampling are blocked.

## P1 — the required D03/scope negative contrast is not exercised

H5.4 lines 507–511 require a scope-only continuation that preserves condition and topic, plus positive and negative task-local source/condition drafts that the existing scorer distinguishes. `test_router_h5_lifecycle_boundaries.py:166-172` only asserts that four prompt substrings exist. It does not run a D03-like scope continuation through lifecycle/assembler/scorer and does not show that omission of the scope condition fails semantically while the preserved form passes. This is a test-presence tautology, not the specified boundary check.

The D04 source test at lines 149–164 is better: it directly proves the existing source scorer distinguishes current-only evidence from current plus historical document evidence. The V01/V03/V06, U03, and A02/A03 injected drafts cover useful plumbing and output boundaries, including exact ambiguity options. They do not fill the explicit D03 requirement. Since D03 is named as a non-waivable critical regression guard in H5.1/H5.5, this missing offline check blocks acceptance.

## P2 — predecessor preservation and historical replay are only partially auditable

The checked-in r1/r2 manifests and resources appear preserved: their manifest hashes match the reported values, neither directory has receipt/envelope/binding JSONL files, and r3 resources are byte-identical to r2. However, `validate_frozen()` authenticates only the r2 manifest bytes (`freeze_router_v3_h5.py:34`) and compares six r3 resources with r2 (`:41-42`). It does not revalidate r2's full code/test/historical inventory and does not validate r1 at all. The independent report performed those checks, but the release entry points do not preserve that guarantee.

The H2 and r10 replay results are plausible and repeat byte-identically, but the H4 replay is described only as an in-process adaptation of r10 functions. The exact script/command and canonical replay digest are not preserved in the report or pinned inventory. A future reviewer cannot reproduce the claimed H4 `20/14/19/19` replay from the report alone or verify that the monkeypatch bound every intended H4 dependency. This does not show the historical numbers are wrong; it makes the H4 historical replay evidence weaker than the exact-byte standard claimed by H5.4.

## Other audited properties

- The classifier/parser policy is phrased in terms of independent operations, supplied alias evidence, competing meanings/documents, inherited evidence, conditions, polarity, and correction signals. No tuning case ID appears in either frozen prompt. The fictional `ZQ` contrast is generic rather than a disguised tuning literal.
- The parser contains one occurrence of the concluding self-check marker. The later alias/ambiguity paragraph is a contrast block, not a duplicate self-check.
- True A02/A03 ambiguity is protected at the output boundary: exact candidate options are retained, unknown IDs fail closed, and distinct document IDs are explicitly kept distinct. The tests correctly do not claim that the known retained-task representation defect is fixed.
- V01/V03/V06 alias outputs, U03 deferred unsupported behavior, D04 task-local inherited evidence, one repair, terminal failure, wrong-role binding, resource substitution, and empty 0/20 fail-closed behavior have focused offline coverage. These are injected-output/runtime checks and provide no evidence that a model will follow the prompt.
- R3 currently contains 20 inputs in the manifest order and zero `receipts.jsonl`, `request_envelopes.jsonl`, or `receipt_envelope_bindings.jsonl`. `empty_replay_summary()` itself is hard-coded to zero and does not inspect receipt-like files, so the filesystem audit is the meaningful evidence for the zero-receipt claim.
- R1 and r2 remain present with their original manifest hashes; this critique did not modify either predecessor, any implementation, prompt, test, frozen run, manifest, spec, decision, or checkpoint.

The two release-blocking actions are therefore concrete: create a new immutable revision with a genuinely external/authenticated exact-manifest trust anchor, and restore a green prior regression proof while adding the missing executable D03 scope/scorer contrast. Historical H4 replay should also be captured as a reproducible pinned command or artifact before Astra acceptance.
