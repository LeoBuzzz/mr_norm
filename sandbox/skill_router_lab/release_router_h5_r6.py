"""Trusted-host release preflight for H5 r6 (stdlib only)."""
import argparse, ast, hashlib, json, os, runpy, sys
from pathlib import Path

REQUIRED_EXECUTED_ROOTS = [
    "test_router_h5_r5_offline.py", "test_router_h5_r5_lifecycle_boundaries.py",
    "test_router_subscription_h5_r5_coordinator.py", "test_router_h5_r5_trust.py", "test_router_h5_r6_trust.py",
    "test_router_h5_offline.py", "test_router_h5_lifecycle_boundaries.py",
    "test_router_subscription_h5_coordinator.py", "test_router_h4_offline.py",
    "test_router_h4_lifecycle_boundaries.py", "test_router_subscription_h4_coordinator.py",
    "test_router_h3.py", "test_router_h3_r5.py", "test_router_h3_r6.py",
    "test_router_h3_r7.py", "test_router_h3_r8_provenance.py",
    "test_router_h3_r9_acceptance.py", "test_router_h3_r10_critical_identity.py",
    "test_router_draft_v2.py", "test_router_v2_replay_eval.py",
    "test_router_subscription_r10_coordinator.py", "freeze_router_v3_h5_r6.py",
    "router_subscription_lifecycle_h5_r6.py", "prepare_router_subscription_h5_r6.py",
    "replay_router_h4_for_h5_r6.py", "prepare_router_subscription_h4.py",
    "evaluate_router_v3_h3_replay_r10.py", "evaluate_router_v3_h3_semantic_replay_r10.py",
    "freeze_router_v3_h4.py", "router_subscription_lifecycle_h4.py",
    "replay_router_h2_snapshot_r10.py",
]

def _derived_import_closure(here):
    seen=set();pending=list(REQUIRED_EXECUTED_ROOTS)
    while pending:
        rel=pending.pop()
        if rel in seen: continue
        path=here/rel
        if not path.is_file(): raise SystemExit("executed dependency missing from checkout: "+rel)
        seen.add(rel)
        try: tree=ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc: raise SystemExit("invalid executed dependency: "+rel) from exc
        names=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): names.extend(item.name for item in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module: names.append(node.module)
        for name in names:
            candidate=name.replace(".","/")+".py"
            if (here/candidate).is_file() and candidate not in seen: pending.append(candidate)
    return seen

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--anchor-launcher-sha256",required=True);p.add_argument("--anchor-lock-sha256",required=True);p.add_argument("--anchor-manifest-sha256",required=True);p.add_argument("--action",choices=("freeze","replay","prepare"),required=True);a,forward=p.parse_known_args()
 here=Path(__file__).resolve().parent; run=here/"router_subscription_run_tuning_v3_h5_r6_20261010"; lockp=run/"release_lock.json"
 if sha(__file__)!=a.anchor_launcher_sha256: raise SystemExit("external launcher anchor mismatch")
 if sha(lockp)!=a.anchor_lock_sha256: raise SystemExit("external release-lock anchor mismatch")
 lock=json.loads(lockp.read_text(encoding="utf-8"))
 if lock.get("run_id")!="router-v3-h5-tuning-20261010-r6": raise SystemExit("r6 release-lock identity mismatch")
 if lock.get("manifest_sha256")!=a.anchor_manifest_sha256 or sha(run/"manifest.json")!=a.anchor_manifest_sha256: raise SystemExit("external manifest anchor mismatch")
 if sha(run/"hash_inventory.json")!=lock["hash_inventory_sha256"]: raise SystemExit("hash inventory anchor mismatch")
 manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
 for rel,want in manifest["files_sha256"].items():
  if sha(run/rel)!=want: raise SystemExit("frozen resource changed: "+rel)
 for rel,want in manifest["historical_h2_runtime_sha256"].items():
  if sha(run/"historical_h2_runtime"/rel)!=want: raise SystemExit("H2 snapshot changed: "+rel)
 for name in ("receipts.jsonl","request_envelopes.jsonl","receipt_envelope_bindings.jsonl"):
  if (run/name).exists(): raise SystemExit("offline r4 evidence stream appeared: "+name)
 expected_tree=lock["r6_content_tree_sha256"]
 actual_tree={p.relative_to(run).as_posix():sha(p) for p in run.rglob("*") if p.is_file() and p.name!="release_lock.json"}
 if actual_tree!=expected_tree: raise SystemExit("r6 content tree membership/hash mismatch")
 evidence_tree={p.relative_to(run/"evidence").as_posix():sha(p) for p in (run/"evidence").rglob("*") if p.is_file()}
 if evidence_tree!=lock["r6_evidence_sha256"]: raise SystemExit("r6 evidence tree membership/hash mismatch")
 expected=lock["executable_closure_sha256"]
 required=_derived_import_closure(here)
 missing=required-set(expected)
 if missing: raise SystemExit("executed dependency absent from release closure: "+", ".join(sorted(missing)))
 actual={rel:sha(here/rel) for rel in expected if (here/rel).is_file()}
 if actual!=expected: raise SystemExit("release closure inventory mismatch")
 if lock.get("predecessor_trees") != {rel: lock["predecessor_trees"][rel] for rel in lock.get("predecessor_trees",{})}: raise SystemExit("bad predecessor tree inventory")
 # Complete r4 and r5 parent trees, including release locks, are checked before imports.
 pins=lock
 for rel, expected_tree in (("router_subscription_run_tuning_v3_h5_r4_20261010",pins["r4_complete_tree_sha256"]),("router_subscription_run_tuning_v3_h5_r5_20261010",pins["r5_complete_tree_sha256"])):
  root=here/rel; found={p.relative_to(root).as_posix():sha(p) for p in root.rglob("*") if p.is_file()}
  if found!=expected_tree: raise SystemExit("direct parent tree changed: "+rel)
 if sha(here/"router_subscription_run_tuning_v3_h5_r4_20261010"/"manifest.json")!=lock["r4_manifest_sha256"]: raise SystemExit("r4 direct parent manifest mismatch")
 if sha(here/"router_subscription_run_tuning_v3_h5_r5_20261010"/"manifest.json")!=lock["r5_manifest_sha256"]: raise SystemExit("r5 direct parent manifest mismatch")
 # Complete tree membership/hash checks precede every candidate import.
 for rel, entries in lock["predecessor_trees"].items():
  root=here/rel; found={p.relative_to(root).as_posix():sha(p) for p in root.rglob("*") if p.is_file()}
  if found!=entries: raise SystemExit("predecessor tree changed: "+rel)
 os.environ["ROUTER_H5_R6_PREFLIGHT_MANIFEST_SHA256"]=a.anchor_manifest_sha256
 sys.path.insert(0,str(here))
 if a.action=="freeze": runpy.run_module("freeze_router_v3_h5_r6",run_name="__main__")
 elif a.action=="replay": sys.argv=[str(here/"replay_router_h4_for_h5_r6.py")]+forward;runpy.run_module("replay_router_h4_for_h5_r6",run_name="__main__")
 else: sys.argv=[str(here/"prepare_router_subscription_h5_r6.py")]+forward;runpy.run_module("prepare_router_subscription_h5_r6",run_name="__main__")
if __name__=="__main__": main()
