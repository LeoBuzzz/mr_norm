# H5 r6 independent test — 2026-10-10

Verdict: **PASS for independent offline H5.6 testing**. I issued the reviewer-held external hash triple to the coordinator channel for **synthetic offline tests only**. This is not `accept_offline`, sampling, or dispatch authorization. I did not modify implementation, prompts, tests, frozen runs, manifests, locks, spec, decision, or checkpoint.

## Independently computed identity and closure

Run ID: `router-v3-h5-tuning-20261010-r6`. SHA-256 values computed directly from disk:

- Manifest: `a0c7bb381ca1b0ce4e812f67b1ba7b4636ae56b52028d269fe9e383a4046f48c`.
- Hash inventory: `502813caab1df902a78a76826f8a447cb623a165bb9730ecc23d090f4803c8a4`.
- Release lock: `dae5d5d20d3081a15ddf0b6ede3d64d7044eeca44e11721b8cd1bbec9a11724d`.
- Stdlib launcher: `add50fa1a3fd0f967ea4acf9296681885b8f25b2ce2475efcc915d3f455d2fcd`.
- `reviewer_anchor_inputs_h5_r6_20261010.json`: `79e1fc2819a750679250f01e2d2f58b3d972e3d4639a5bc3c6a278a7f441d47c`; its `issued` field remains `false`. It is a candidate-input note, not the external anchor.

The lock's exact membership and bytes match all 51 r6 content files, 22 r6 evidence files, 94 executable-closure files, all four older predecessor trees (r1 29, r2 29, r3 29, H4 32), and the **complete r4 and r5 direct-parent trees, 52 files each including their release locks**. Manifest resource, code, test, and 16-file historical H2 runtime maps match disk bytes and inventory maps. All 94 Python closure files compiled to a disposable directory. The r6 direct-parent SHA is the actual r5 manifest `2e7bac2a309c5e8723907763e76171c979b294eb39df456c4c21be0106f00756`; r5→r4, r4→r3, r3→r2, and r2→r1 parent fields match disk. R4's H4/r10/H2 ancestry fields match their actual manifests. R6 has the same ordered 20 inputs, 11 resources, and historical H2 snapshot bytes as r5, which in turn matches r4.

## External pre-import boundary

With the independent hash triple as command-line anchor, the original launcher completed `--action prepare status` with zero prepared envelopes, receipts, bindings, and repairs. In separate disposable checkout copies, keeping that triple fixed, each substitution was rejected: r4 lock/manifest/parser resource; r5 lock/manifest/parser resource; r6 manifest/lock/inventory/parser resource; launcher; validator; local `router_draft.py`; extra or omitted r6 content; coherent manifest transport plus inventory change; missing anchor; wrong anchor; and altered r4/r5 lineage. No candidate evidence streams were created. This includes direct-parent lock tampering that r5 had accepted.

For a stronger import-order probe, I appended a file-writing import marker to the copied r6 validator, coordinator, and lifecycle, updated the copied closure and used its explicit **synthetic** lock hash, then changed a r5 parent file. All three supported launcher actions (`prepare`, `replay`, `freeze`) rejected `direct parent tree changed` before any marker appeared. Separate r4-lock and r5-manifest/resource marker probes also rejected before import. A copied lock with `prepare_router_subscription_h4.py` omitted from its closure, using that modified lock's explicit synthetic hash, failed the launcher's derived-closure check. These synthetic anchors test internal preflight ordering; the reviewer-held original triple was not replaced.

## Required behavior and replay

The exact r3 regression selection plus four r4 suites, four r5 suites, and the r6 trust suite ran without deselection: **167 passed, 0 failed, 0 skipped, 0 xfailed**. Command: `python -B -m pytest -q -p no:cacheprovider --tb=short --basetemp C:\PY\PythonProject\mr_norm\h5_r6_sol6_required` followed by those 24 modules, from `sandbox/skill_router_lab`. The passing lifecycle/scorer tests execute D03 complete continuation, condition-only omission, task-local historical-source omission with an adjacent task, D04 complete versus current-only source, explicit correction, and independent new request through the real runner, validator/assembler, and unchanged `compare_case`. D03 complete and D04 complete score full-plan PASS; each specified omission remains technically valid but fails the relevant semantic/evidence check.

The H4 fixture tests remain in the full suite. Independently read actual H4 evidence gives 40 receipts, 40 envelopes, and 40 bindings with SHA-256 respectively `6e40bf122b8c1a0145967adf3045c2c35adb9e89bbc9ee1ba3f0e9ad2ccf4b88`, `e052b331a00e75e766cb988492404ae6c3a986cee4b2cbf1beedeb244d214200`, and `eb999ddc529a9f0cf09767fff2bf3c701c00c128c47b4011cab66dde89468949`. These equal the pinned H4 tree bytes. R6 has no receipt/envelope/binding streams. Its empty replay summary is coverage 0/20, incomplete, overall gate false, with all 20 case IDs missing; adding any one of the three stream files on a disposable copy makes it fail closed.

Two launcher-mediated replay invocations, each spawning two fresh child processes, yielded four identical canonical outputs and matched all three pinned replay evidence files. Each output is **266,340 bytes**, SHA-256 `f57815b4742d3532f72c535da847e5520f7c8f507df8499731d716593a9c0e07`. H2 retains first/final technical 15/20→20/20 and semantic 5/20; r10 retains final technical 20/20, semantic 13/20, classification 17/20, status 16/20; H4 retains final technical 20/20, semantic 14/20, classification 19/20, status 19/20, including real D03/D04 failures. No historical tree was written.

Historical provenance remains bounded as in the r4/r5 reports: several old r1 live-code and r1/r2 test generations are unavailable at their stored hashes. Exact resource/tree preservation and present executable closure do not establish full validation of that unavailable original code. The legacy erroneous r2 `h4_manifest_sha256` and null H2 lineage fields remain historical data; the verified actual ancestry is recorded separately above. The independent PASS addresses the r6 offline release closure, not those old claims or H4 semantic quality gates.

Tests and probes used disposable workspace directories and fresh processes. No sampling, live LLM, network, Polza, holdout, Qdrant, production, or bot path was invoked.
