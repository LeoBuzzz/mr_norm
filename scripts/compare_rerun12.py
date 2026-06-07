import json
from pathlib import Path

IDS = {
    "case_002", "case_013", "case_018", "case_019", "case_020", "case_022",
    "case_024", "case_028", "case_038", "case_040", "case_041", "case_042",
}

old = json.loads(Path("reports/pipeline_research/eval_results_natural42.json").read_text(encoding="utf-8"))
new = json.loads(Path("reports/pipeline_research/eval_results_natural42_rerun12.json").read_text(encoding="utf-8"))
old_map = {e["id"]: e for e in old["entries"]}
new_map = {e["id"]: e for e in new["entries"]}

print("case_id | old | new | delta | chunk | retry | routing")
for cid in sorted(IDS):
    o, n = old_map[cid], new_map[cid]
    os_ = o.get("judge_score") or 0
    ns_ = n.get("judge_score") or 0
    chunk = "Y" if n.get("chunk_in_evidence") else "N"
    retry = n.get("retry_reason") or ("yes" if n.get("retry_triggered") else "no")
    diag = n.get("pipeline_diagnostics") or {}
    routing = diag.get("tool_routing_mode") or n.get("tool_routing_mode") or "-"
    print(f"{cid} | {os_} | {ns_} | {ns_ - os_:+d} | {chunk} | {retry} | {routing}")

wins = sum(1 for cid in IDS if (new_map[cid].get("judge_score") or 0) > (old_map[cid].get("judge_score") or 0))
still1 = sum(1 for cid in IDS if (new_map[cid].get("judge_score") or 0) == 1)
mean_old = sum(old_map[c].get("judge_score") or 0 for c in IDS) / len(IDS)
mean_new = sum(new_map[c].get("judge_score") or 0 for c in IDS) / len(IDS)
print(f"\nwins={wins} still1={still1} mean_old={mean_old:.2f} mean_new={mean_new:.2f}")
