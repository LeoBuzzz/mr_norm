from __future__ import annotations
import json, os, shutil
from pathlib import Path
from router_subscription_lifecycle_h5_r4 import FileSubscriptionRunnerH5R4
from router_subscription_eval_v3 import compare_case
from router_draft import build_source_manifest
from freeze_router_v3_h5_r4 import RUN_DIR

def _copy(tmp_path):
    target=tmp_path/"r4";shutil.copytree(RUN_DIR,target);return target

def _row(run, case_id):
    return next(json.loads(x) for x in (run/"inputs/input_manifest.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(x)["case_id"]==case_id)

def _execute(run,row,draft,complexity="complex"):
    fresh=run.parent/(run.name+"_"+str(len(list(run.parent.iterdir()))))
    shutil.copytree(RUN_DIR,fresh)
    seq=iter([json.dumps({"complexity":complexity}),json.dumps(draft,ensure_ascii=False)])
    prior=os.environ.get("ROUTER_H5_R4_SYNTHETIC_TEST_MODE")
    os.environ["ROUTER_H5_R4_SYNTHETIC_TEST_MODE"]="1"
    try: return FileSubscriptionRunnerH5R4(fresh,lambda envelope: next(seq)).run_case(row)
    finally:
        if prior is None: os.environ.pop("ROUTER_H5_R4_SYNTHETIC_TEST_MODE",None)
        else: os.environ["ROUTER_H5_R4_SYNTHETIC_TEST_MODE"]=prior

def test_d03_condition_and_inherited_evidence_are_scored_behaviorally(tmp_path):
    run=_copy(tmp_path);row=_row(run,"D03")
    base={"dialog":"continue","tasks":[{"operation":"all","source":["current@2:4","turn_1@5:6"],"topic":"защитное заземление","document":"doc_1e4a178aa5114b30","conditions":[{"source":"current@2:4","kind":"condition","polarity":"include"}]}]}
    good=_execute(run,row,base)
    gold=json.loads((run/"evaluation/router_plan_cases_tuning_v2.json").read_text(encoding="utf-8"))["cases"]
    expected=next(case for case in gold if case["id"]=="D03")
    score=compare_case(expected,good["plan"],row)
    assert good["status"]=="valid" and score["plan_match"] is True
    bad=dict(base);bad["tasks"]=[dict(base["tasks"][0])];bad["tasks"][0].pop("conditions")
    worse=_execute(run,row,bad)
    bad_score=compare_case(expected,worse["plan"],row)
    assert worse["status"]=="valid" and bad_score["plan_match"] is False
    no_history=dict(base);no_history["tasks"]=[dict(base["tasks"][0])];no_history["tasks"][0]["source"]=["current@2:4"]
    missing=_execute(run,row,no_history)
    missing_score=compare_case(expected,missing["plan"],row)
    assert missing["status"]=="valid" and missing_score["plan_match"] is False

def test_d04_inherited_document_and_new_point_remain_continue(tmp_path):
    run=_copy(tmp_path);row=_row(run,"D04")
    draft={"dialog":"continue","tasks":[{"operation":"text","source":["current@2:3","turn_1@4:4"],"topic":"пункт 3.2","document":"doc_1e4a178aa5114b30","point":"current@3:3"}]}
    result=_execute(run,row,draft,complexity="simple")
    assert result["status"]=="valid" and result["plan"]["dialog_action"]=="continue"
    assert result["plan"]["tasks"][0]["point_ref"]=="3.2"


def test_explicit_correction_and_independent_new_request_do_not_share_stale_conditions(tmp_path):
    pristine=_copy(tmp_path);base=_row(pristine,"D03")
    correction=dict(base);correction["question"]="Нет, ищи только в ПУЭ"
    correction["context"]={"turns":[{"turn_id":"turn_1","user_text":"Ищи только по правилам компании"}]}
    correction["source_manifest"]=build_source_manifest(correction["question"],correction["context"],None)
    c_tokens=correction["source_manifest"]["tokens"]["current"]
    c_ref=f"current@1:{len(c_tokens)}"
    correction_draft={"dialog":"correct","tasks":[{"operation":"all","source":[c_ref],"topic":"требования к заземлению","document":"doc_1e4a178aa5114b30","conditions":[{"source":c_ref,"kind":"condition","polarity":"include"}]}]}
    corrected=_execute(pristine,correction,correction_draft)
    assert corrected["status"]=="valid" and corrected["plan"]["dialog_action"]=="correct"
    assert len(corrected["plan"]["tasks"][0]["constraints"])==1
    assert "правила компании" not in corrected["plan"]["tasks"][0]["constraints"][0]["text"]
    fresh=dict(base);fresh["question"]="Отдельно найди заземление"
    fresh["context"]=correction["context"]
    fresh["source_manifest"]=build_source_manifest(fresh["question"],fresh["context"],None)
    f_tokens=fresh["source_manifest"]["tokens"]["current"]
    f_ref=f"current@{len(f_tokens)}:{len(f_tokens)}"
    new_draft={"dialog":"new","tasks":[{"operation":"search","source":[f_ref],"topic":"заземление","document":"corpus"}]}
    independent=_execute(pristine,fresh,new_draft)
    assert independent["status"]=="valid" and independent["plan"]["dialog_action"]=="new"
    assert independent["plan"]["tasks"][0]["constraints"]==[]
