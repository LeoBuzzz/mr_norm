# H3 independent test report — 2026-10-08

Verdict: PASS for H3 implementation/freeze. No product code was changed. No live calls, sampling, holdout, Polza, Qdrant, production bot, or eval workers were used.

Scope checked against `SPEC_QUESTION_PARSER_STAGE1.md` v3.1 and `DECISION_QUESTION_PARSER_STAGE1_H3_20261008.md`:

- correct run: `router-v3-h3-tuning-20261008-r2`;
- H3 manifest SHA-256: `29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3`;
- r1 recorded as superseded and unreceipted;
- H3 r2 has no `receipts.jsonl`, `request_envelopes.jsonl`, or `receipt_envelope_bindings.jsonl`;
- parser prompt field types are explicit for `topic`, `source`, `point`, `terms[].source`, `conditions[].source`, `uncertain[].source`, `document`, `terms[].meaning`, `after`, and `replace`;
- repair prompt contains targeted `TOPIC_MUST_BE_LITERAL_TEXT` guidance;
- `TOPIC_MUST_BE_LITERAL_TEXT` fails closed before delegated v2 validation/assembly and does not auto-dereference locator text;
- `router_validation_policy.validator_for_policy()` selects legacy v2 and H3 policies and rejects unknown policies;
- H3 freeze preserves H2 input/schema/order parity, classifier prompt hash, requested models/effort, and no gold/receipt inclusion;
- H2 immutable hashes remain: manifest `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`, receipts `184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160`, receipt count 45.

Commands run:

```powershell
python -m pytest -q -p no:cacheprovider sandbox/skill_router_lab/test_router_h3.py
# 20 passed in 0.10s
```

```powershell
python -m pytest -q -p no:cacheprovider sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 152 passed in 0.34s
```

```powershell
python -m py_compile sandbox/skill_router_lab/router_draft_h3.py sandbox/skill_router_lab/router_prompts_v3_h3.py sandbox/skill_router_lab/router_validation_policy.py sandbox/skill_router_lab/freeze_router_v3_h3_run.py sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/router_draft.py sandbox/skill_router_lab/router_prompts_v3.py sandbox/skill_router_lab/evaluate_router_v3_file_replay.py sandbox/skill_router_lab/router_subscription_lifecycle_v3.py
# passed
```

```powershell
python sandbox/skill_router_lab/run_router_v2_offline_gate.py
# {"case_count":20,"live_workers_started":false,"receipt_state":"receipts_validated","run_id":"router-v2-tuning-20261007","status":"offline_frozen_inputs_valid"}
```

A consolidated read-only integrity script also passed and printed:

```json
{
  "h3_manifest_sha256": "29a878aa47d8aab61105f1bd91c30744d0a33d773dc58b3bd1a1b9abcb0444e3",
  "h2_manifest_sha256": "e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862",
  "h2_receipts_sha256": "184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160",
  "h2_receipt_count": 45,
  "h3_receipts_present": false,
  "validation_policy": "topic_literal_guard.v1",
  "h3_validate_status": "h3_frozen_inputs_valid"
}
```

One broad legacy command was intentionally reported, not hidden:

```powershell
python -m pytest -q -p no:cacheprovider sandbox/skill_router_lab/test_router_h3.py sandbox/skill_router_lab/test_router_v3_eval.py sandbox/skill_router_lab/test_router_draft_v2.py sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py
# 174 passed, 8 failed
```

All eight failures are stale `test_router_v3_eval.py` assumptions that copy the H2 r2 directory and expect it to have no receipts. Current H3 spec says H2 r2 is immutable with 45 receipts, and the failures are duplicate/resume guard or receipt-count mismatches against those existing receipts. I did not edit those tests or mutate H2. This is not an H3 implementation/freeze defect, but it remains a test-fixture isolation issue if that old full command is kept as a required gate.

Defects found in H3 implementation/freeze: none.
