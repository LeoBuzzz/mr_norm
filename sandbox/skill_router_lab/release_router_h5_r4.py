"""Trusted-host release preflight for H5 r4 (stdlib only)."""
import argparse, hashlib, json, os, runpy, sys
from pathlib import Path

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--anchor-launcher-sha256",required=True);p.add_argument("--anchor-lock-sha256",required=True);p.add_argument("--anchor-manifest-sha256",required=True);p.add_argument("--action",choices=("freeze","replay","prepare"),required=True);a,forward=p.parse_known_args()
 here=Path(__file__).resolve().parent; run=here/"router_subscription_run_tuning_v3_h5_r4_20261010"; lockp=run/"release_lock.json"
 if sha(__file__)!=a.anchor_launcher_sha256: raise SystemExit("external launcher anchor mismatch")
 if sha(lockp)!=a.anchor_lock_sha256: raise SystemExit("external release-lock anchor mismatch")
 lock=json.loads(lockp.read_text(encoding="utf-8"))
 if lock.get("manifest_sha256")!=a.anchor_manifest_sha256 or sha(run/"manifest.json")!=a.anchor_manifest_sha256: raise SystemExit("external manifest anchor mismatch")
 if sha(run/"hash_inventory.json")!=lock["hash_inventory_sha256"]: raise SystemExit("hash inventory anchor mismatch")
 manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
 for rel,want in manifest["files_sha256"].items():
  if sha(run/rel)!=want: raise SystemExit("frozen resource changed: "+rel)
 for rel,want in manifest["historical_h2_runtime_sha256"].items():
  if sha(run/"historical_h2_runtime"/rel)!=want: raise SystemExit("H2 snapshot changed: "+rel)
 for name in ("receipts.jsonl","request_envelopes.jsonl","receipt_envelope_bindings.jsonl"):
  if (run/name).exists(): raise SystemExit("offline r4 evidence stream appeared: "+name)
 expected_tree=lock["r4_content_tree_sha256"]
 actual_tree={p.relative_to(run).as_posix():sha(p) for p in run.rglob("*") if p.is_file() and p.name!="release_lock.json"}
 if actual_tree!=expected_tree: raise SystemExit("r4 content tree membership/hash mismatch")
 evidence_tree={p.relative_to(run/"evidence").as_posix():sha(p) for p in (run/"evidence").rglob("*") if p.is_file()}
 if evidence_tree!=lock["r4_evidence_sha256"]: raise SystemExit("r4 evidence tree membership/hash mismatch")
 expected=lock["executable_closure_sha256"]
 actual={rel:sha(here/rel) for rel in expected if (here/rel).is_file()}
 if actual!=expected: raise SystemExit("release closure inventory mismatch")
 if lock.get("predecessor_trees") != {rel: lock["predecessor_trees"][rel] for rel in lock.get("predecessor_trees",{})}: raise SystemExit("bad predecessor tree inventory")
 # Complete tree membership/hash checks precede every candidate import.
 for rel, entries in lock["predecessor_trees"].items():
  root=here/rel; found={p.relative_to(root).as_posix():sha(p) for p in root.rglob("*") if p.is_file()}
  if found!=entries: raise SystemExit("predecessor tree changed: "+rel)
 os.environ["ROUTER_H5_R4_PREFLIGHT_MANIFEST_SHA256"]=a.anchor_manifest_sha256
 sys.path.insert(0,str(here))
 if a.action=="freeze": runpy.run_module("freeze_router_v3_h5_r4",run_name="__main__")
 elif a.action=="replay": sys.argv=[str(here/"replay_router_h4_for_h5_r4.py")]+forward;runpy.run_module("replay_router_h4_for_h5_r4",run_name="__main__")
 else: sys.argv=[str(here/"prepare_router_subscription_h5_r4.py")]+forward;runpy.run_module("prepare_router_subscription_h5_r4",run_name="__main__")
if __name__=="__main__": main()
