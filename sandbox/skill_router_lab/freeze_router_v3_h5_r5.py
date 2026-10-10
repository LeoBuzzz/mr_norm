"""Create and validate immutable H5 r5 resources; release authorization is external."""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path
from typing import Any
from router_subscription_eval import freeze_files
HERE=Path(__file__).resolve().parent
PARENT_DIR=HERE/"router_subscription_run_tuning_v3_h5_r4_20261010"
RUN_DIR=HERE/"router_subscription_run_tuning_v3_h5_r5_20261010"
RUN_ID="router-v3-h5-tuning-20261010-r5"
PARENT_RUN_ID="router-v3-h5-tuning-20261010-r4"
PARENT_MANIFEST_SHA256="c0472cea7a9c72a2ebcabef444b5bb205492c124bda917f108aee2d03da6623b"
ANCESTRY={"h5_r2_manifest_sha256":"0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194","h5_r1_manifest_sha256":"c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd","h4_manifest_sha256":"4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa","h3_r10_manifest_sha256":"ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc","h2_manifest_sha256":"e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"}
CODE_FILES=['evaluate_router_v2_tuning.py', 'evaluate_router_v3_h3_replay_r10.py', 'evaluate_router_v3_h3_replay_r5.py', 'evaluate_router_v3_h3_replay_r6.py', 'evaluate_router_v3_h3_replay_r7.py', 'evaluate_router_v3_h3_replay_r8.py', 'evaluate_router_v3_h3_replay_r9.py', 'evaluate_router_v3_h3_semantic_replay_r10.py', 'evaluate_router_v3_h3_semantic_replay_r5.py', 'evaluate_router_v3_h3_semantic_replay_r6.py', 'evaluate_router_v3_h3_semantic_replay_r7.py', 'evaluate_router_v3_h3_semantic_replay_r8.py', 'evaluate_router_v3_h3_semantic_replay_r9.py', 'evaluate_router_v3_semantic_replay.py', 'freeze_router_v3_h3_r10.py', 'freeze_router_v3_h3_r5.py', 'freeze_router_v3_h3_r6.py', 'freeze_router_v3_h3_r7.py', 'freeze_router_v3_h3_r8.py', 'freeze_router_v3_h3_r9.py', 'freeze_router_v3_h4.py', 'freeze_router_v3_h5.py', 'freeze_router_v3_h5_r4.py', 'freeze_router_v3_h5_r5.py', 'prepare_router_subscription_h4.py', 'prepare_router_subscription_h5.py', 'prepare_router_subscription_h5_r4.py', 'prepare_router_subscription_h5_r5.py', 'prepare_router_subscription_r10.py', 'release_router_h5_r4.py', 'release_router_h5_r5.py', 'replay_router_h2_snapshot.py', 'replay_router_h2_snapshot_r10.py', 'replay_router_h2_snapshot_r8.py', 'replay_router_h2_snapshot_r9.py', 'replay_router_h4_for_h5_r4.py', 'replay_router_h4_for_h5_r5.py', 'router_contracts.py', 'router_draft.py', 'router_draft_h3.py', 'router_planning.py', 'router_prompts.py', 'router_prompts_v2.py', 'router_prompts_v3.py', 'router_prompts_v3_h3.py', 'router_prompts_v3_h4.py', 'router_prompts_v3_h5.py', 'router_request_envelope_v3.py', 'router_subscription_eval.py', 'router_subscription_eval_v3.py', 'router_subscription_lifecycle_h3_r10.py', 'router_subscription_lifecycle_h3_r5.py', 'router_subscription_lifecycle_h3_r6.py', 'router_subscription_lifecycle_h3_r7.py', 'router_subscription_lifecycle_h3_r8.py', 'router_subscription_lifecycle_h3_r9.py', 'router_subscription_lifecycle_h4.py', 'router_subscription_lifecycle_h5.py', 'router_subscription_lifecycle_h5_r4.py', 'router_subscription_lifecycle_h5_r5.py', 'router_subscription_lifecycle_v3.py', 'router_v2_runtime.py', 'router_validation_policy.py', 'run_router_v2_offline_gate.py', 'test_router_draft_v2.py', 'test_router_h3.py', 'test_router_h3_r10_critical_identity.py', 'test_router_h3_r5.py', 'test_router_h3_r6.py', 'test_router_h3_r7.py', 'test_router_h3_r8_provenance.py', 'test_router_h3_r9_acceptance.py', 'test_router_h4_lifecycle_boundaries.py', 'test_router_h4_offline.py', 'test_router_h5_lifecycle_boundaries.py', 'test_router_h5_offline.py', 'test_router_h5_r4_lifecycle_boundaries.py', 'test_router_h5_r4_offline.py', 'test_router_h5_r4_trust.py', 'test_router_h5_r5_lifecycle_boundaries.py', 'test_router_h5_r5_offline.py', 'test_router_h5_r5_trust.py', 'test_router_subscription_h4_coordinator.py', 'test_router_subscription_h5_coordinator.py', 'test_router_subscription_h5_r4_coordinator.py', 'test_router_subscription_h5_r5_coordinator.py', 'test_router_subscription_r10_coordinator.py', 'test_router_v2_replay_eval.py']
TEST_FILES=['test_router_draft_v2.py', 'test_router_h3.py', 'test_router_h3_r10_critical_identity.py', 'test_router_h3_r5.py', 'test_router_h3_r6.py', 'test_router_h3_r7.py', 'test_router_h3_r8_provenance.py', 'test_router_h3_r9_acceptance.py', 'test_router_h4_lifecycle_boundaries.py', 'test_router_h4_offline.py', 'test_router_h5_lifecycle_boundaries.py', 'test_router_h5_offline.py', 'test_router_h5_r4_lifecycle_boundaries.py', 'test_router_h5_r4_offline.py', 'test_router_h5_r4_trust.py', 'test_router_h5_r5_lifecycle_boundaries.py', 'test_router_h5_r5_offline.py', 'test_router_h5_r5_trust.py', 'test_router_subscription_h4_coordinator.py', 'test_router_subscription_h5_coordinator.py', 'test_router_subscription_h5_r4_coordinator.py', 'test_router_subscription_h5_r5_coordinator.py', 'test_router_subscription_r10_coordinator.py', 'test_router_v2_replay_eval.py']
EVIDENCE=("receipts.jsonl","request_envelopes.jsonl","receipt_envelope_bindings.jsonl")
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def tree_hashes(root:Path)->dict[str,str]:
 return {p.relative_to(root).as_posix():sha(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}
def validate_frozen(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); mb=(d/"manifest.json").read_bytes(); m=json.loads(mb)
 if m.get("run_id")!=RUN_ID: raise ValueError("H5 r5 run identity mismatch")
 if m.get("lineage",{}).get("parent_manifest_sha256")!=PARENT_MANIFEST_SHA256: raise ValueError("H5 r5 direct parent mismatch")
 if sha((PARENT_DIR/"manifest.json").read_bytes())!=PARENT_MANIFEST_SHA256: raise ValueError("pinned H5 r4 parent changed")
 for rel,h in m["files_sha256"].items():
  if sha((d/rel).read_bytes())!=h: raise ValueError(f"frozen resource hash mismatch: {rel}")
 for name,h in m["code_files_sha256"].items():
  if sha((HERE/name).read_bytes())!=h: raise ValueError(f"implementation hash mismatch: {name}")
 for name,h in m["test_files_sha256"].items():
  if sha((HERE/name).read_bytes())!=h: raise ValueError(f"test hash mismatch: {name}")
 for name,h in m["historical_h2_runtime_sha256"].items():
  if sha((d/"historical_h2_runtime"/name).read_bytes())!=h: raise ValueError(f"historical runtime hash mismatch: {name}")
 for rel in ("inputs/input_manifest.jsonl","source_manifests.json","schema.json","prompts/classifier.txt","prompts/parser.txt","prompts/repair.txt"):
  if (d/rel).read_bytes()!=(PARENT_DIR/rel).read_bytes(): raise ValueError(f"r5 inherited resource drift: {rel}")
 rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 if len(rows)!=20 or [x["case_id"] for x in rows]!=m["case_order"]: raise ValueError("H5 r5 case order mismatch")
 for name in EVIDENCE:
  if (d/name).exists(): raise ValueError(f"offline r4 must contain no {name}")
 inv=json.loads((d/"hash_inventory.json").read_text(encoding="utf-8"))
 if inv.get("manifest_sha256")!=sha(mb) or inv.get("direct_parent_manifest_sha256")!=PARENT_MANIFEST_SHA256: raise ValueError("r4 inventory identity mismatch")
 if inv.get("frozen_resources_sha256")!=m["files_sha256"] or inv.get("implementation_sha256")!=m["code_files_sha256"]: raise ValueError("r4 inventory maps differ from manifest")
 for name,h in inv.get("test_sha256",{}).items():
  if sha((HERE/name).read_bytes())!=h: raise ValueError(f"r4 test inventory mismatch: {name}")
 if set(inv.get("test_sha256",{}))!=set(TEST_FILES): raise ValueError("r4 exact test inventory membership mismatch")
 return {"status":"h5_r5_frozen_inputs_valid","run_id":RUN_ID,"manifest_sha256":sha(mb),"case_count":20,"direct_parent_manifest_sha256":PARENT_MANIFEST_SHA256,"receipts_present":False}
def empty_replay_summary(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); validate_frozen(d)
 rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 return {"coverage":0,"denominator":20,"complete":False,"overall_gate":False,"missing_cases":[x["case_id"] for x in rows]}
def freeze()->dict[str,Any]:
 if RUN_DIR.exists(): raise FileExistsError(f"immutable H5 r5 already exists: {RUN_DIR}")
 if sha((PARENT_DIR/"manifest.json").read_bytes())!=PARENT_MANIFEST_SHA256: raise ValueError("H5 r4 parent hash mismatch")
 parent=json.loads((PARENT_DIR/"manifest.json").read_text(encoding="utf-8"))

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
 m={k:v for k,v in json.loads((PARENT_DIR/"manifest.json").read_text(encoding="utf-8")).items() if k not in ("run_id","lineage","files_sha256","code_files_sha256","historical_h2_runtime_sha256","parent_resource")}
 tests={name:sha((HERE/name).read_bytes()) for name in TEST_FILES}
 code={name:digest for name,digest in code.items() if name not in tests}
 m.update(run_id=RUN_ID,lineage={"parent_run_id":PARENT_RUN_ID,"parent_manifest_sha256":PARENT_MANIFEST_SHA256,"reason":"H5 r5 proof/closure correction; inherited r4 resources and lineage unchanged"},files_sha256=files,code_files_sha256=code,test_files_sha256=tests,historical_h2_runtime_sha256=snapshots,gold_included=False,receipts_included=False,parent_resource={"manifest_sha256":PARENT_MANIFEST_SHA256,"run_id":PARENT_RUN_ID})
 (RUN_DIR/"manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 inv={"run_id":RUN_ID,"manifest_sha256":sha((RUN_DIR/"manifest.json").read_bytes()),"frozen_resources_sha256":files,"implementation_sha256":code,"direct_parent_manifest_sha256":PARENT_MANIFEST_SHA256,"test_sha256":tests,"historical_h2_runtime_sha256":snapshots}
 (RUN_DIR/"hash_inventory.json").write_text(json.dumps(inv,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 return {"manifest_sha256":inv["manifest_sha256"],"run_id":RUN_ID,"run_dir":str(RUN_DIR)}
if __name__=="__main__": print(json.dumps(freeze(),ensure_ascii=False,indent=2))
