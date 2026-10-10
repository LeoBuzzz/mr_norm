# H5 r6 independent critique — 2026-10-10

Verdict: **changes_required**.

The reviewer-held r6 values match the current bytes: manifest
`a0c7bb381ca1b0ce4e812f67b1ba7b4636ae56b52028d269fe9e383a4046f48c`,
release lock `dae5d5d20d3081a15ddf0b6ede3d64d7044eeca44e11721b8cd1bbec9a11724d`,
launcher `add50fa1a3fd0f967ea4acf9296681885b8f25b2ce2475efcc915d3f455d2fcd`,
and run ID `router-v3-h5-tuning-20261010-r6`. Holding those values outside
the checkout removes the r3 self-hash cycle. The current launcher nevertheless
does not establish the H5.6.1 trusted-host/import-path boundary: unpinned local
Python can execute before its self-hash check and after the otherwise successful
preflight. Therefore the external triple is non-circular as a record, but the
tested launch procedure is not a non-bypassable use of that record under the
stated threat model. No sampling, live LLM, network, Polza, holdout, Qdrant,
production, or bot path was used.

## P1 — local module shadowing executes before the launcher verifies itself

`release_router_h5_r6.py:2` imports `hashlib` and the other claimed trusted
stdlib modules before `main()` compares the launcher bytes with the external
hash. The reported invocation is ordinary `python -B`, and the report does not
identify a trusted wrapper, isolated import configuration, or verified
`sys.path`. With normal script execution, the script directory participates in
module resolution before the first anchor check.

In a disposable directory I copied the exact launcher, added a local
`hashlib.py` that only wrote a marker, and invoked the launcher with deliberately
wrong anchors. The marker was written before the launcher reached its hash
comparison; the process then failed because the shadow module did not expose
`sha256`:

```text
returncode 1
marker_exists True
AttributeError: module 'hashlib' has no attribute 'sha256'
```

An attacker-controlled shadow can instead expose a compatible `sha256` and
forge the checks or perform arbitrary side effects. This is precisely the local
module-shadowing condition that H5.6.1 says the trusted host must exclude. The
reviewer-held anchor cannot authenticate code that has already run before the
authentication operation.

## P1 — an unlisted package directory crosses the post-preflight closure

The second import boundary is independently bypassable. The derived closure at
`release_router_h5_r6.py:23-40` recognizes a local dependency only as
`<module>.py`; it does not recognize `<module>/__init__.py`, namespace packages,
or reparse/symlinked package directories. The launcher checks only lock-listed
root files at lines 64-69, does not reject extra root entries, then explicitly
prepends the checkout at line 83 before the candidate imports at lines 84-86.

I copied the complete lock-listed checkout, all predecessor trees, and the r4,
r5, and r6 trees. I kept the original external manifest/lock/launcher triple
unchanged and added only `jsonschema/__init__.py` outside the run directory.
`--action prepare status` passed every pre-import hash/tree/closure check and
then executed the package's marker while importing the candidate dependency:

```text
returncode 1
marker_exists True
ImportError: cannot import name 'Draft202012Validator' from 'jsonschema'
```

The failure after the marker is not fail-closed preflight: unanchored checkout
code already executed. A compatible proxy package could continue into
validation or dispatch. A symlink/junction package directory has the same gap,
and `Path.rglob(... if p.is_file())` does not provide a general reparse-point
policy. The present checkout contains no symlinks, and all lock paths are
relative, traversal-free, and casefold-unique, but those current-state facts do
not close an allowed pre-start addition or local import shadow.

These two findings invalidate the report's claim that coherent substitution,
path/import tricks, and all local dependencies are rejected before side effects.
A successor needs a trusted host launch procedure that authenticates the
launcher without importing from the candidate directory, fixes and records the
interpreter/import paths, and makes local package/file membership exact before
any candidate or third-party import. Patching only the already anchored Python
launcher is insufficient for the first boundary.

## P2 — the H4 fixture refactor omits two mandated self-checks

The three archived original H4 tests have the stated hashes, the original test
function set remains present, and assertion counts increased rather than
decreased. The current populated H4 evidence is still 40/40/40 with hashes
`6e40bf122b8c1a0145967adf3045c2c35adb9e89bbc9ee1ba3f0e9ad2ccf4b88`,
`e052b331a00e75e766cb988492404ae6c3a986cee4b2cbf1beedeb244d214200`,
and `eb999ddc529a9f0cf09767fff2bf3c701c00c128c47b4011cab66dde89468949`.
The fixture helpers remove only the three evidence streams from a `tmp_path`
copy, and the populated original is separately hash-checked. No skip, xfail,
test deletion, or behavioral-assertion suppression was found.

However, H5.6.2 expressly requires the synthetic fixture to assert that the
resolved destination is outside the original run and to compare manifest and
resource bytes before and after stream removal. The helpers at
`test_router_h4_lifecycle_boundaries.py:13-18`,
`test_router_subscription_h4_coordinator.py:15-20`, and
`test_router_h4_offline.py:24-29` assert only that the three copied streams are
absent. The code is presently straightforward and my read-only comparison found
no evidence loss, but the exact regression proof required by H5.6.2 is not
encoded in the frozen suite.

## P2 — r6 replay metadata and successor scope are not fully recorded

The three pinned r6 replay files are each 266,340 bytes with SHA-256
`f57815b4742d3532f72c535da847e5520f7c8f507df8499731d716593a9c0e07`.
The pinned adapter performs two fresh child processes, canonicalizes without
lossy normalization, and retains the real H2, r10, and H4 failures. The digest
and substantive procedure are credible and byte-reproducible. The r6 report,
unlike the r4/r5 reports, does not give the exact launcher CLI, interpreter path
and version, dependency versions, or an explicit input/closure-hash record that
H5.6.4 requires in the report. The adapter returns an interpreter string at
runtime, but that metadata is not stored beside the canonical outputs.

Also, the checked-in normative allowlist in H5.6.5 names the r4 successor files
exactly. It states that later pinned-byte fixes require a successor revision and
new reviews, but no checked-in r5/r6 decision expands the exact allowlist to the
r5/r6 modules, `r6_trust_pins_20261010.json`, or
`reviewer_anchor_inputs_h5_r6_20261010.json`. These additions are narrowly
related to the two prior FAIL reports and do not alter prompts, scorer, gold, or
schema, but scope authorization cannot be established from the governing files
available to this critique. An external coordinator authorization, if one
exists, should be preserved or mirrored as provenance without pretending that a
mutable local mirror is the anchor.

## Checks that passed

- Independent recomputation matched all 52 r4 tree files, all 52 r5 tree files,
  all 51 non-lock r6 content files, and all 94 executable-closure files. No
  unsafe `..`, absolute, backslash-alias, colon, or casefold-collision path was
  present, and the current trees contain no symlinks.
- I reran the exact 15-module r3 selection plus the four r4 suites, four r5
  suites, and r6 trust suite: **167 passed**, with no failures. No skip, xfail,
  skipif, collection deselection, or PASS-exception marker appears in those 24
  modules. This is the mandatory H5.6 regression selection; the known stale
  r10 coordinator module was not silently represented as passing.
- The D03 positive, condition-omission, history-omission, and adjacent-task
  cases are meaningful. The adjacent case asserts both global evidence failure
  and the first task's own evidence failure, so the result is not explained
  only by the extra task. D04 complete/current-only similarly asserts the
  intended evidence field. Both use `router_subscription_eval_v3.compare_case`;
  its SHA-256 is unchanged across the r4, r5, and r6 closures:
  `147704ef6ce71bbb381891a3d01fd05921142fbc9591865af73b36ce04667f7a`.
  The r6 lifecycle differs from r5 only in run identity/preflight bindings, so
  the scorer proof is not a substitute scorer.
- The report accurately limits unavailable historical provenance: the missing
  original r1 live-code and r1/r2 test generations are not claimed as validated
  from present resources, and erroneous legacy lineage fields remain historical
  data rather than executable trust roots.
- The local `reviewer_anchor_inputs_h5_r6_20261010.json` remains explicitly
  `issued: false`; treating the external reviewer message, rather than this
  candidate-input note, as authority follows the non-circular part of H5.6.1.

The release remains offline-only. The two P1 import-boundary failures require a
new immutable successor, new external hashes, independent tests, critique, and
separate Astra `accept_offline`; the current r6 PASS report cannot authorize
sampling or dispatch.
