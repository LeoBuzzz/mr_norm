"""Fresh-process, pinned offline H4/H2/r10 replay adapter."""
from __future__ import annotations
import hashlib,json,os,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
CHILD=r'''
import json,sys
from pathlib import Path
here=Path.cwd();sys.path.insert(0,str(here))
import evaluate_router_v3_h3_replay_r10 as tech
import evaluate_router_v3_h3_semantic_replay_r10 as sem
import freeze_router_v3_h4 as h4
import router_subscription_lifecycle_h4 as life
import replay_router_h2_snapshot_r10 as h2
# Save r10 bindings, then explicitly bind H4 replay to the historical H4 verifier/lifecycle.
r10_run=tech.RUN_DIR;r10_id=tech.RUN_ID;r10_validate=tech.validate_frozen;r10_raw=tech.validate_raw_h3;r10_assemble=tech.assemble_plan_h3
h4_run=here/"router_subscription_run_tuning_v3_h4_20261009"
tech.RUN_DIR=h4_run;tech.RUN_ID=h4.RUN_ID;tech.validate_frozen=h4.validate_frozen;tech.validate_raw_h3=life.validate_raw_h3;tech.assemble_plan_h3=life.assemble_plan_h3
sem.RUN_DIR=h4_run;sem.RUN_ID=h4.RUN_ID;sem.validate_frozen=h4.validate_frozen;sem.replay_h3=tech.replay_h3;sem.replay_h2_snapshot=lambda run_dir=None:h2.replay_h2_snapshot()
h4_result=sem.semantic_replay_h3(h4_run)
# Restore the pinned r10 identity, then replay r10 and its bound H2 snapshot directly.
tech.RUN_DIR=r10_run;tech.RUN_ID=r10_id;tech.validate_frozen=r10_validate;tech.validate_raw_h3=r10_raw;tech.assemble_plan_h3=r10_assemble
sem.RUN_DIR=r10_run;sem.RUN_ID=r10_id;sem.validate_frozen=r10_validate;sem.replay_h3=tech.replay_h3
r10_result=sem.semantic_replay_h3(r10_run)
h2_result=h2.replay_h2_snapshot()
print(json.dumps({"h4":h4_result,"r10":r10_result,"h2":h2_result},ensure_ascii=False,sort_keys=True,separators=(",",":")))
'''

def canonical(obj): return (json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8")
def replay(output_dir:Path)->dict:
 output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
 env=os.environ.copy();env["PYTHONDONTWRITEBYTECODE"]="1";env["PYTHONIOENCODING"]="utf-8"
 outputs=[]
 for i in (1,2):
  proc=subprocess.run([sys.executable,"-c",CHILD],cwd=HERE,capture_output=True,env=env,text=True,encoding="utf-8")
  if proc.returncode: raise RuntimeError(f"fresh replay {i} failed: {proc.stderr}")
  parsed=json.loads(proc.stdout);outputs.append(canonical(parsed))
 if outputs[0]!=outputs[1]: raise RuntimeError("two fresh-process canonical replay outputs differ")
 output=output_dir/"historical_replay.json";output.write_bytes(outputs[0])
 (output_dir/"historical_replay_first.json").write_bytes(outputs[0]);(output_dir/"historical_replay_second.json").write_bytes(outputs[1])
 return {"command":[sys.executable,"-c","<pinned embedded adapter; see replay_router_h4_for_h5_r6.py>"],"interpreter":sys.version,"digest_sha256":hashlib.sha256(outputs[0]).hexdigest(),"bytes":len(outputs[0]),"output":str(output),"repeat_digest_sha256":hashlib.sha256(outputs[1]).hexdigest()}
def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument("--output-dir",type=Path,required=True);a=p.parse_args();print(json.dumps(replay(a.output_dir),indent=2))
if __name__=="__main__":main()
