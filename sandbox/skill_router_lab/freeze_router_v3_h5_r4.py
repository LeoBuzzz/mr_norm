"""Create and validate immutable H5 r4 resources; release authorization is external."""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path
from typing import Any
from router_subscription_eval import freeze_files
HERE=Path(__file__).resolve().parent
PARENT_DIR=HERE/"router_subscription_run_tuning_v3_h5_r3_20261009"
RUN_DIR=HERE/"router_subscription_run_tuning_v3_h5_r4_20261010"
RUN_ID="router-v3-h5-tuning-20261010-r4"
PARENT_RUN_ID="router-v3-h5-tuning-20261009-r3"
PARENT_MANIFEST_SHA256="2617500135233dd4d0903e8e6c3bda7e668acc55d993f3d6fe64ff17f428bdf7"
ANCESTRY={"h5_r2_manifest_sha256":"0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194","h5_r1_manifest_sha256":"c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd","h4_manifest_sha256":"4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa","h3_r10_manifest_sha256":"ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc","h2_manifest_sha256":"e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"}
CODE_FILES=["router_prompts_v3_h5.py","router_subscription_lifecycle_h5.py","prepare_router_subscription_h5.py","router_draft.py","router_draft_h3.py","router_validation_policy.py","router_subscription_lifecycle_v3.py","router_subscription_eval.py","router_subscription_eval_v3.py","router_request_envelope_v3.py","evaluate_router_v2_tuning.py","evaluate_router_v3_semantic_replay.py","freeze_router_v3_h5_r4.py","router_subscription_lifecycle_h5_r4.py","prepare_router_subscription_h5_r4.py","release_router_h5_r4.py","replay_router_h4_for_h5_r4.py"]
TEST_FILES=["test_router_h5_r4_offline.py","test_router_h5_r4_lifecycle_boundaries.py","test_router_subscription_h5_r4_coordinator.py","test_router_h5_r4_trust.py"]
EVIDENCE=("receipts.jsonl","request_envelopes.jsonl","receipt_envelope_bindings.jsonl")
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def tree_hashes(root:Path)->dict[str,str]:
 return {p.relative_to(root).as_posix():sha(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}
def validate_frozen(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); mb=(d/"manifest.json").read_bytes(); m=json.loads(mb)
 if m.get("run_id")!=RUN_ID: raise ValueError("H5 r4 run identity mismatch")
 if m.get("lineage",{}).get("parent_manifest_sha256")!=PARENT_MANIFEST_SHA256: raise ValueError("H5 r4 direct parent mismatch")
 if sha((PARENT_DIR/"manifest.json").read_bytes())!=PARENT_MANIFEST_SHA256: raise ValueError("pinned H5 r3 parent changed")
 for rel,h in m["files_sha256"].items():
  if sha((d/rel).read_bytes())!=h: raise ValueError(f"frozen resource hash mismatch: {rel}")
 for name,h in m["code_files_sha256"].items():
  if sha((HERE/name).read_bytes())!=h: raise ValueError(f"implementation hash mismatch: {name}")
 for name,h in m["historical_h2_runtime_sha256"].items():
  if sha((d/"historical_h2_runtime"/name).read_bytes())!=h: raise ValueError(f"historical runtime hash mismatch: {name}")
 for rel in ("inputs/input_manifest.jsonl","source_manifests.json","schema.json","prompts/classifier.txt","prompts/parser.txt","prompts/repair.txt"):
  if (d/rel).read_bytes()!=(PARENT_DIR/rel).read_bytes(): raise ValueError(f"r4 inherited resource drift: {rel}")
 rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 if len(rows)!=20 or [x["case_id"] for x in rows]!=m["case_order"]: raise ValueError("H5 r4 case order mismatch")
 for name in EVIDENCE:
  if (d/name).exists(): raise ValueError(f"offline r4 must contain no {name}")
 inv=json.loads((d/"hash_inventory.json").read_text(encoding="utf-8"))
 if inv.get("manifest_sha256")!=sha(mb) or inv.get("direct_parent_manifest_sha256")!=PARENT_MANIFEST_SHA256: raise ValueError("r4 inventory identity mismatch")
 if inv.get("frozen_resources_sha256")!=m["files_sha256"] or inv.get("implementation_sha256")!=m["code_files_sha256"]: raise ValueError("r4 inventory maps differ from manifest")
 for name,h in inv.get("test_sha256",{}).items():
  if sha((HERE/name).read_bytes())!=h: raise ValueError(f"r4 test inventory mismatch: {name}")
 if set(inv.get("test_sha256",{}))!=set(TEST_FILES): raise ValueError("r4 exact test inventory membership mismatch")
 return {"status":"h5_r4_frozen_inputs_valid","run_id":RUN_ID,"manifest_sha256":sha(mb),"case_count":20,"direct_parent_manifest_sha256":PARENT_MANIFEST_SHA256,"receipts_present":False}
def empty_replay_summary(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); validate_frozen(d)
 rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 return {"coverage":0,"denominator":20,"complete":False,"overall_gate":False,"missing_cases":[x["case_id"] for x in rows]}
def freeze()->dict[str,Any]:
 if RUN_DIR.exists(): raise FileExistsError(f"immutable H5 r4 already exists: {RUN_DIR}")
 if sha((PARENT_DIR/"manifest.json").read_bytes())!=PARENT_MANIFEST_SHA256: raise ValueError("H5 r3 parent hash mismatch")
 parent=json.loads((PARENT_DIR/"manifest.json").read_text(encoding="utf-8"))
 for entry,want in ANCESTRY.items():
  pred={"h5_r2_manifest_sha256":"router_subscription_run_tuning_v3_h5_r2_20261009","h5_r1_manifest_sha256":"router_subscription_run_tuning_v3_h5_r1_20261009","h4_manifest_sha256":"router_subscription_run_tuning_v3_h4_20261009","h3_r10_manifest_sha256":"router_subscription_run_tuning_v3_h3_r10_20261009","h2_manifest_sha256":"router_subscription_run_tuning_v3_h2_20261008"}[entry]
  if sha((HERE/pred/"manifest.json").read_bytes())!=want: raise ValueError(f"historical ancestry mismatch: {entry}")
 frozen={rel:(PARENT_DIR/rel).read_bytes() for rel in parent["files_sha256"]}
 RUN_DIR.mkdir(parents=True)
 for rel,data in frozen.items():
  p=RUN_DIR/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 files=freeze_files(RUN_DIR,frozen); snap=RUN_DIR/"historical_h2_runtime";snap.mkdir(); snapshots={}
 for name,h in parent["historical_h2_runtime_sha256"].items():
  data=(PARENT_DIR/"historical_h2_runtime"/name).read_bytes()
  if sha(data)!=h: raise ValueError(f"r3 H2 snapshot mismatch: {name}")
  (snap/name).write_bytes(data);snapshots[name]=h
 # Retain exact inherited implementation closure and add versioned r4 files.
 code={name:sha((HERE/name).read_bytes()) for name in CODE_FILES if (HERE/name).is_file()}
 m={k:v for k,v in parent.items() if k not in ("run_id","lineage","files_sha256","code_files_sha256","historical_h2_runtime_sha256","parent_resource")}
 m.update(run_id=RUN_ID,lineage={"parent_run_id":PARENT_RUN_ID,"parent_manifest_sha256":PARENT_MANIFEST_SHA256,**ANCESTRY,"r2_actual_parent_manifest_sha256":"c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd","reason":"H5 r4 trust/fixture/replay correction; inherited r3 resources unchanged"},files_sha256=files,code_files_sha256=code,historical_h2_runtime_sha256=snapshots,gold_included=False,receipts_included=False,parent_resource={"manifest_sha256":PARENT_MANIFEST_SHA256,"run_id":PARENT_RUN_ID})
 (RUN_DIR/"manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 inv={"run_id":RUN_ID,"manifest_sha256":sha((RUN_DIR/"manifest.json").read_bytes()),"frozen_resources_sha256":files,"implementation_sha256":code,"direct_parent_manifest_sha256":PARENT_MANIFEST_SHA256,"test_sha256":{n:sha((HERE/n).read_bytes()) for n in TEST_FILES if (HERE/n).exists()},"historical_h2_runtime_sha256":snapshots}
 (RUN_DIR/"hash_inventory.json").write_text(json.dumps(inv,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 return {"manifest_sha256":inv["manifest_sha256"],"run_id":RUN_ID,"run_dir":str(RUN_DIR)}
if __name__=="__main__": print(json.dumps(freeze(),ensure_ascii=False,indent=2))
