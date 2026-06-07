import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "reports/pipeline_research/eval_results_natural42.json").read_text(encoding="utf-8"))
entries = data["entries"]
m = data["metrics"]


def classify(e):
    score = e.get("judge_score") or 0
    if score >= 7:
        return "ok"
    ans = (e.get("actual_answer") or "").lower()
    chunk, doc = e.get("chunk_in_evidence"), e.get("doc_in_evidence")
    if chunk and "отсутств" in ans and "фрагмент" in ans:
        return "chunk_but_refusal"
    if chunk:
        return "chunk_wrong_answer"
    if doc:
        return "doc_only_miss"
    if "отсутств" in ans and "фрагмент" in ans:
        return "refusal"
    return "wrong_no_gold"


by_class = Counter(classify(e) for e in entries)
print("=== NATURAL42 FINAL ===")
print(f"cases={len(entries)} mean={m['mean_judge_score']} chunk={m['chunk_in_evidence_rate']} doc={m['doc_in_evidence_rate']}")
print(f"score>=7: {m['score_ge_7_rate']} score=1: {m['score_histogram'].get('1', 0)}")
print(f"by_type: {m['by_question_type']}")
print(f"failure_class: {dict(by_class)}")
print(f"no_resolved_doc: {sum(1 for e in entries if not (e.get('resolved_doc_name') or '').strip())}/{len(entries)}")
print(f"retry: {sum(1 for e in entries if e.get('retry_triggered'))}/{len(entries)}")
print()
print("=== WORST 12 ===")
for e in sorted(entries, key=lambda x: x.get("judge_score", 0))[:12]:
    print(
        f"{e['id']} score={e['judge_score']} class={classify(e)} "
        f"chunk={e.get('chunk_in_evidence')} doc={e.get('doc_in_evidence')} type={e.get('question_type')}"
    )
    print(f"  Q: {e['question'][:90]}")
    print(f"  resolved: {e.get('resolved_doc_name') or '-'} routing: {e.get('intent_routing_mode')}")
    if (e.get("judge_score") or 0) <= 3:
        print(f"  judge: {(e.get('judge_reason') or '')[:110]}")
    print()

batches = [entries[i : i + 10] for i in range(0, len(entries), 10)]
print("=== BY BATCH ===")
for i, b in enumerate(batches, 1):
    scores = [e.get("judge_score", 0) for e in b]
    print(
        f"batch{i} n={len(b)} mean={sum(scores)/len(scores):.2f} "
        f"chunk={sum(1 for e in b if e.get('chunk_in_evidence'))/len(b):.0%} "
        f"score1={sum(1 for s in scores if s == 1)}"
    )

print("=== KEY CASES (pre-fix failures) ===")
for cid in ("case_001", "case_002", "case_006"):
    e = next((x for x in entries if x["id"] == cid), None)
    if e:
        rd = (e.get("resolved_doc_name") or "")[:60]
        print(f"{cid}: score={e['judge_score']} chunk={e['chunk_in_evidence']} resolved={rd or '-'}")
