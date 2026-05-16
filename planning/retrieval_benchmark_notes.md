# Retrieval Benchmark Notes

Live batch report: `output/reports/retrieval_compare_batch_20260516_111639.md`

## Quality fix applied

- **Vector `doc_name` filter variants**: `run_vector_tool` now uses `expand_doc_name_filter_variants`, same as payload/point.
- Before fix: vector returned zero hits for `doc_name: "Правила устройства электроустановок"` because indexed payload uses uppercase canonical name.
- After fix: PUE scoped vector queries return results; hybrid can fuse vector + payload ranks.

## Stage 4 completion

- Point tool no longer performs document-only Qdrant scroll; it requires `point_number`, `heading_path_text`, `chunk_id`, or `point_identity_key`.
- Vector tool applies `doc_name` uppercase variants like payload/point.
- Batch benchmark harness, golden fixture, and deterministic eval metrics are in place.

## Remaining gaps (Stage 5+ / corpus)

1. **Vector empty query**: filter-only point lookup cannot use vector (by design).
2. **RRF vs semantic rank**: vector top for grounding query may differ from payload intro chunk.
3. **Control characters**: chunk text still contains `\u0001` in some PUE points (corpus cleanup, not retrieval).
4. **Graph tool**: not implemented yet (planned after retrieval baseline is stable).

## Fixture

Golden questions: `tests/fixtures/retrieval_questions.json` (5 questions, manual_judgement filled from first live run).
