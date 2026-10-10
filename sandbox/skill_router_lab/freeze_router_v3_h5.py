"""Freeze and validate the immutable H5 r3 lineage-correction run."""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path
from typing import Any
from router_draft import SEMANTIC_DRAFT_SCHEMA
from router_prompts_v3_h5 import CLASSIFIER_V3_SYSTEM, PARSER_V3_SYSTEM, REPAIR_V3_SYSTEM
from router_subscription_eval import freeze_files

HERE=Path(__file__).resolve().parent
PARENT_DIR=HERE/"router_subscription_run_tuning_v3_h5_r2_20261009"
RUN_DIR=HERE/"router_subscription_run_tuning_v3_h5_r3_20261009"
RUN_ID="router-v3-h5-tuning-20261009-r3"
PARENT_RUN_ID="router-v3-h5-tuning-20261009-r2"
PARENT_MANIFEST_SHA256="0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194"
H4_MANIFEST_SHA256="4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa"
H2_MANIFEST_SHA256="e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862"
R10_MANIFEST_SHA256="ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc"
EXPECTED_MANIFEST_SHA256="2617500135233dd4d0903e8e6c3bda7e668acc55d993f3d6fe64ff17f428bdf7"
PROMPTS={"prompts/classifier.txt":CLASSIFIER_V3_SYSTEM.encode(),"prompts/parser.txt":PARSER_V3_SYSTEM.encode(),"prompts/repair.txt":REPAIR_V3_SYSTEM.encode()}
CODE_FILES=["router_prompts_v3_h5.py","router_subscription_lifecycle_h5.py","prepare_router_subscription_h5.py","router_draft.py","router_draft_h3.py","router_validation_policy.py","router_subscription_lifecycle_v3.py","router_subscription_eval.py","router_subscription_eval_v3.py","router_request_envelope_v3.py","evaluate_router_v2_tuning.py","evaluate_router_v3_semantic_replay.py"]
TEST_FILES=["test_router_h5_offline.py","test_router_h5_lifecycle_boundaries.py","test_router_subscription_h5_coordinator.py"]
def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

def validate_frozen(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); mb=(d/"manifest.json").read_bytes()
 if sha(mb)!=EXPECTED_MANIFEST_SHA256:raise ValueError("H5 r3 exact manifest SHA mismatch")
 m=json.loads(mb)
 if m.get("run_id")!=RUN_ID:raise ValueError("H5 r3 run identity mismatch")
 lin=m.get("lineage",{})
 expected={"parent_run_id":PARENT_RUN_ID,"parent_manifest_sha256":PARENT_MANIFEST_SHA256,"h4_manifest_sha256":H4_MANIFEST_SHA256,"h2_manifest_sha256":H2_MANIFEST_SHA256,"h3_r10_manifest_sha256":R10_MANIFEST_SHA256}
 for key,value in expected.items():
  if lin.get(key)!=value:raise ValueError(f"H5 r3 lineage mismatch: {key}")
 if sha((PARENT_DIR/"manifest.json").read_bytes())!=PARENT_MANIFEST_SHA256:raise ValueError("pinned H5 r2 parent changed")
 for rel,h in m["files_sha256"].items():
  if sha((d/rel).read_bytes())!=h:raise ValueError(f"frozen resource hash mismatch: {rel}")
 for name,h in m["code_files_sha256"].items():
  if sha((HERE/name).read_bytes())!=h:raise ValueError(f"implementation hash mismatch: {name}")
 for name,h in m["historical_h2_runtime_sha256"].items():
  if sha((d/"historical_h2_runtime"/name).read_bytes())!=h:raise ValueError(f"historical runtime hash mismatch: {name}")
 for rel in ("inputs/input_manifest.jsonl","source_manifests.json","schema.json","prompts/classifier.txt","prompts/parser.txt","prompts/repair.txt"):
  if (d/rel).read_bytes()!=(PARENT_DIR/rel).read_bytes():raise ValueError(f"H5 r3 inherited resource drifted from H5 r2: {rel}")
 rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 if len(rows)!=20 or [x["case_id"] for x in rows]!=m["case_order"]:raise ValueError("H5 frozen input count/order mismatch")
 if (d/"gold.json").exists() or any({"expected","gold","split"}&set(x) for x in rows):raise ValueError("gold leaked into inputs")
 inv=json.loads((d/"hash_inventory.json").read_text(encoding="utf-8"))
 if inv.get("manifest_sha256")!=sha(mb) or inv.get("parent_manifest_sha256")!=PARENT_MANIFEST_SHA256:raise ValueError("H5 inventory identity mismatch")
 for name,h in inv["test_sha256"].items():
  if sha((HERE/name).read_bytes())!=h:raise ValueError(f"H5 test inventory hash mismatch: {name}")
 if inv.get("frozen_resources_sha256")!=m["files_sha256"] or inv.get("implementation_sha256")!=m["code_files_sha256"]:raise ValueError("H5 inventory differs from manifest hash maps")
 if (d/"receipts.jsonl").exists():raise ValueError("H5 r3 immutable run must have zero receipts")
 return {"status":"h5_r3_frozen_inputs_valid","run_id":RUN_ID,"manifest_sha256":sha(mb),"case_count":20,"direct_parent_run_id":PARENT_RUN_ID,"direct_parent_manifest_sha256":PARENT_MANIFEST_SHA256,"receipts_present":False}

def empty_replay_summary(run_dir:Path|None=None)->dict[str,Any]:
 d=Path(run_dir or RUN_DIR); rows=[json.loads(x) for x in (d/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
 return {"coverage":0,"denominator":20,"complete":False,"overall_gate":False,"missing_cases":[x["case_id"] for x in rows]}

def freeze()->dict[str,Any]:
 if RUN_DIR.exists():raise FileExistsError(f"immutable H5 r3 already exists: {RUN_DIR}")
 mb=(PARENT_DIR/"manifest.json").read_bytes()
 if sha(mb)!=PARENT_MANIFEST_SHA256:raise ValueError("H5 r2 exact parent hash mismatch")
 pm=json.loads(mb); relative=list(pm["files_sha256"])
 frozen={rel:(PARENT_DIR/rel).read_bytes() for rel in relative}
 RUN_DIR.mkdir(parents=True)
 for rel,b in frozen.items():
  t=RUN_DIR/rel;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(b)
 files=freeze_files(RUN_DIR,frozen); snap=RUN_DIR/"historical_h2_runtime";snap.mkdir(); snapshots={}
 for name,h in pm["historical_h2_runtime_sha256"].items():
  b=(PARENT_DIR/"historical_h2_runtime"/name).read_bytes()
  if sha(b)!=h:raise ValueError(f"parent historical snapshot mismatch: {name}")
  (snap/name).write_bytes(b);snapshots[name]=h
 code={n:sha((HERE/n).read_bytes()) for n in CODE_FILES}
 lineage={"parent_run_id":PARENT_RUN_ID,"parent_manifest_sha256":PARENT_MANIFEST_SHA256,"h4_manifest_sha256":H4_MANIFEST_SHA256,"h3_r10_manifest_sha256":R10_MANIFEST_SHA256,"h2_manifest_sha256":H2_MANIFEST_SHA256,"reason":"H5 r3 corrects pinned H4/H2 lineage while preserving immutable r2 inputs and prompts"}
 manifest={k:v for k,v in pm.items() if k not in ("run_id","lineage","files_sha256","code_files_sha256","historical_h2_runtime_sha256","parent_resource")}
 manifest.update(run_id=RUN_ID,lineage=lineage,files_sha256=files,code_files_sha256=code,historical_h2_runtime_sha256=snapshots,gold_included=False,receipts_included=False,parent_resource={"manifest_sha256":PARENT_MANIFEST_SHA256,"run_id":PARENT_RUN_ID})
 (RUN_DIR/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 tests={n:sha((HERE/n).read_bytes()) for n in TEST_FILES}
 inv={"run_id":RUN_ID,"manifest_sha256":sha((RUN_DIR/"manifest.json").read_bytes()),"frozen_resources_sha256":files,"implementation_sha256":code,"test_sha256":tests,"historical_h2_runtime_sha256":snapshots,"parent_manifest_sha256":PARENT_MANIFEST_SHA256}
 (RUN_DIR/"hash_inventory.json").write_text(json.dumps(inv,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 return {"manifest_sha256":inv["manifest_sha256"],"run_id":RUN_ID,"run_dir":str(RUN_DIR)}
if __name__=="__main__":print(json.dumps(freeze(),ensure_ascii=False,indent=2))
