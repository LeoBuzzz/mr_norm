"""Lexical-first definition query over the explicitly supplied local index."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from definition_index import DefinitionIndex
import base64,hashlib,json,re,unicodedata

def _json_field(value,default):
    if not isinstance(value,str): return value if value is not None else default
    try: return json.loads(value)
    except (ValueError,TypeError): return default

def _present(row):
    terms=DefinitionIndex._searchable_terms(row)
    return {"entry_id":row.get("entry_id"),"term_verbatim":row.get("term_verbatim"),"primary_term":terms[-1],"definition":row.get("definition_text"),"quote":row.get("quote"),"quote_spans":_json_field(row.get("quote_spans"),[]),"display_separators":_json_field(row.get("display_separators"),[]),"field_spans":_json_field(row.get("field_spans"),{}),"source":{"doc_id":row.get("doc_id"),"registry_key":row.get("registry_key"),"title":row.get("title"),"source_hash":row.get("source_hash")},"doc_id":row.get("doc_id"),"locator":_json_field(row.get("locator"),{}),"applicable_scope":_json_field(row.get("applicable_scope"),{}),"notes":_json_field(row.get("notes"),{}),"aliases":_json_field(row.get("aliases"),[]),"alias_evidence":row.get("alias_evidence",[]),"chunk_mapping":row.get("chunk_mapping",[]),"semantic_reason":row.get("semantic_reason"),"kind":row.get("kind")}

@dataclass
class DefinitionSearchContext:
    index: DefinitionIndex
    scope_doc_ids: tuple[str,...]=()
    source_reader: Any=None
    normalizer: Any=None
    dense_adapter: Any=None
    reranker: Any=None
    context_matcher: Any=None
    source_snapshot: Any=None

@dataclass
class DefinitionSearchResult:
    status:str; mode:str; term:str; scope_doc_ids:list[str]; items:list[dict]; next_cursor:str|None; coverage:dict; diagnostics:dict

def rank_definition_entries(entries,request):
    norm=lambda x:" ".join(re.sub(r"[^\w]+"," ",unicodedata.normalize("NFKC",str(x or "")).casefold().replace("ё","е"),flags=re.UNICODE).split())
    term=norm(request["term"])
    return sorted(entries,key=lambda e:(0 if term in {norm(t) for t in DefinitionIndex._searchable_terms(e)} else 1,e.get("doc_id",""),e.get("entry_id","")))

def _local_bge_rank(query,entries):
    """Rerank every full definition through overlapping local BGE windows when installed."""
    try:
        from hybrid_retrieval import DEFAULT_RERANKER, rerank_local
        from transformers import AutoTokenizer
        if not DEFAULT_RERANKER.is_dir(): return None
        tokenizer=AutoTokenizer.from_pretrained(str(DEFAULT_RERANKER),local_files_only=True)
    except Exception:
        return None
    qlen=len(tokenizer.encode(query,add_special_tokens=False)); window=max(64,min(384,512-qlen-24)); stride=max(1,window-64)
    windows=[]; parent={}
    for idx,item in enumerate(entries):
        content="\n".join(str(v or "") for v in (item.get("term_verbatim"),item.get("definition_text"),item.get("quote"),item.get("applicable_scope")))
        ids=tokenizer.encode(content,add_special_tokens=False); starts=list(range(0,max(1,len(ids)),stride)) if ids else [0]
        for wi,start in enumerate(starts):
            if start>=len(ids) and ids: break
            cid=f"{idx}:{wi}"; parent[cid]=idx
            windows.append({"chunk_id":cid,"doc_name":item.get("title") or item.get("registry_key"),"text":tokenizer.decode(ids[start:start+window],skip_special_tokens=True)})
            if start+window>=len(ids): break
    ranked,device,elapsed=rerank_local(query,windows); scores={}
    for row in ranked:
        i=parent[row["chunk_id"]]; scores[i]=max(scores.get(i,float("-inf")),float(row["reranker_score"]))
    order=sorted(range(len(entries)),key=lambda i:(-scores.get(i,float("-inf")),entries[i].get("doc_id",""),entries[i].get("entry_id","")))
    return [entries[i] for i in order],{"device":device,"elapsed_sec":elapsed,"window_count":len(windows),"scores":{entries[i].get("entry_id"):scores.get(i) for i in order}}

def _bm25_definition_candidates(query,entries):
    """Rank every eligible complete definition entry with the shared local BM25."""
    from hybrid_retrieval import bm25_search
    chunks=[{"chunk_id":str(row["entry_id"]),"text":"\n".join(str(row.get(k) or "") for k in ("term_verbatim","definition_text","quote","applicable_scope","notes"))} for row in entries]
    by_id={str(row["entry_id"]):row for row in entries}
    ranked=bm25_search(chunks,query,top_k=max(1,len(chunks)))
    ids=[str(item["chunk"]["chunk_id"]) for item in ranked]
    if len(ids)!=len(set(ids)) or any(eid not in by_id for eid in ids): raise ValueError("bm25_changed_candidate_identity")
    return [by_id[eid] for eid in ids],{"scores":{str(item["chunk"]["chunk_id"]):item["score"] for item in ranked},"scanned_entries":len(chunks)}

def _dense_definition_candidates(adapter,query,scope,entries):
    """Injected adapter contract: return [{entry_id, score}] from supplied rows only."""
    if not callable(adapter): raise ValueError("dense_adapter_not_callable")
    response=adapter(query=query,scope_doc_ids=tuple(scope),candidates=entries)
    if not isinstance(response,list): raise ValueError("dense_adapter_must_return_list")
    by_id={str(row["entry_id"]):row for row in entries}; seen=set(); scored=[]
    for item in response:
        if not isinstance(item,dict) or not isinstance(item.get("entry_id"),str): raise ValueError("dense_adapter_row_invalid")
        eid=item["entry_id"]
        if eid not in by_id or eid in seen: raise ValueError("dense_adapter_out_of_scope_or_duplicate_entry")
        score=float(item.get("score"))
        if not __import__("math").isfinite(score): raise ValueError("dense_adapter_score_invalid")
        seen.add(eid); scored.append((score,eid))
    scored.sort(key=lambda pair:(-pair[0],by_id[pair[1]].get("doc_id",""),pair[1]))
    return [by_id[eid] for _,eid in scored],{"scores":{eid:score for score,eid in scored},"scanned_entries":len(entries),"returned_entries":len(scored)}

NORMALIZER_VERSION="unicode-nfkc-casefold-yo-v1"
SORT_VERSION="definition-search-exact-lemma-dense-bm25-bge-v3"

def _parse_query(query,mode):
    text=" ".join(str(query).strip().split()); all_mode=mode=="all_variants"
    if mode=="auto" and re.search(r"\b(все|варианты|вариантов)\b",text,re.I): all_mode=True
    text=re.sub(r"^(?:пожалуйста\s+)?(?:найди|найти|дай|дайте|покажи|покажите)\s+", "",text,flags=re.I)
    text=re.sub(r"^(?:все\s+(?:варианты\s+)?(?:определения|формулировки|дефиниции)|определение|что\s+такое|кто\s+такой|что\s+означает)\s+", "",text,flags=re.I)
    text=text.strip(" ?.!«»\"'")
    request_context=None
    marker=re.search(r"\s+(?:в\s+контексте|в\s+смысле|для\s+целей|согласно|по\s+документу)\s+",text,flags=re.I)
    if marker:
        request_context=text[marker.end():].strip(); text=text[:marker.start()].strip()
    return text,("all_variants" if all_mode else "best"),request_context

def _cursor_encode(offset,fingerprint): return base64.urlsafe_b64encode(json.dumps({"offset":offset,"fingerprint":fingerprint},separators=(",",":")).encode()).decode().rstrip("=")
def _cursor_decode(cursor,fingerprint):
    try: data=json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
    except Exception as e: raise ValueError("invalid_cursor") from e
    if data.get("fingerprint")!=fingerprint: raise ValueError("stale_cursor")
    if type(data.get("offset")) is not int or data["offset"]<0: raise ValueError("invalid_cursor")
    return data["offset"]

def _check_source_freshness(context,scope):
    reader=context.source_reader
    if reader is None: return {"status":"registered_snapshot_only","checked_doc_ids":[]}
    records=context.index.source_fingerprints(scope)
    drift=[]
    for did in scope:
        try:
            if callable(reader): current=reader(did)
            elif hasattr(reader,"source_for_doc"): current=reader.source_for_doc(did)
            elif hasattr(reader,"get"): current=reader.get(did)
            else: raise TypeError("source_reader must be callable, mapping-like, or expose source_for_doc")
            if current is None: drift.append({"doc_id":did,"reason":"fresh_source_missing"}); continue
            observed={key:getattr(current,key,None) if not isinstance(current,dict) else current.get(key) for key in ("source_hash","raw_hash","structure_hash")}
            expected=records.get(did)
            if expected is None or any(observed[k] is None or observed[k]!=expected[k] for k in expected):
                drift.append({"doc_id":did,"reason":"source_fingerprint_changed_or_incomplete","expected":expected,"observed":observed})
        except Exception as exc:
            drift.append({"doc_id":did,"reason":"source_reader_error","detail":str(exc)})
    return {"status":"stale" if drift else "matched","checked_doc_ids":list(scope),"drift":drift}

def definition_search(query,*,context:DefinitionSearchContext,doc_id=None,mode="auto",cursor=None,page_size=50):
    term,chosen,request_context=_parse_query(query,mode)
    if not term: raise ValueError("query must contain one term")
    if chosen not in ("best","all_variants"): raise ValueError("mode must be auto, best, or all_variants")
    scope=[doc_id] if doc_id else list(context.scope_doc_ids or getattr(context.index,"active_doc_ids",lambda:[])())
    if not scope: return DefinitionSearchResult("partial",chosen,term,[],[],None,context.index.coverage(),{"reason":"explicit source snapshot scope is required"})
    if doc_id and context.scope_doc_ids and doc_id not in context.scope_doc_ids:
        return DefinitionSearchResult("ambiguous",chosen,term,list(context.scope_doc_ids),[],None,context.index.coverage(),{"reason":"doc_id lies outside the explicit source snapshot"})
    freshness=_check_source_freshness(context,scope)
    if freshness["status"]=="stale":
        return DefinitionSearchResult("needs_review",chosen,term,scope,[],None,context.index.coverage(),{"reason":"the active source no longer matches the indexed source snapshot","source_freshness":freshness})
    if page_size<1: raise ValueError("page_size must be positive")
    hits=[]; retrieval_method="exact_term_or_typed_alias"
    for did in scope:
        hits.extend(r for r in context.index.lookup_term(term,did) if r.get("kind") in {"definition","note_definition"})
        lemma=getattr(context.index,"lookup_lemma_matches",None)
        if lemma:
            hits.extend(r for r in lemma(term,did,context.normalizer) if r.get("kind") in {"definition","note_definition"})
    hits=list({r.get("entry_id"):r for r in hits}.values())
    hits=rank_definition_entries(hits,{"term":term,"context":request_context})
    retrieval_diagnostics={}
    fallback_used=False
    if not hits and chosen=="best":
        all_rows=getattr(context.index,"_complete_rows",lambda _doc_id=None:[])(None)
        candidates=[r for r in all_rows if r.get("kind") in {"definition","note_definition"} and r.get("doc_id") in scope]
        if candidates and context.dense_adapter is not None:
            try:
                hits,retrieval_diagnostics=_dense_definition_candidates(context.dense_adapter,term,scope,candidates)
                retrieval_method="injected_dense_over_all_completed_scoped_definitions"
                fallback_used=bool(hits)
            except (ValueError,TypeError,KeyError) as exc:
                retrieval_diagnostics={"dense_error":str(exc)}
                hits=[]
        if candidates and not hits:
            try:
                hits,retrieval_diagnostics=_bm25_definition_candidates(term,candidates)
                retrieval_method="full_scoped_bm25_definition_search"
                fallback_used=bool(hits)
            except Exception as exc:
                retrieval_diagnostics={**retrieval_diagnostics,"bm25_error":str(exc)}
    if request_context:
        if not context.context_matcher:
            coverage=context.index.coverage()
            return DefinitionSearchResult("needs_review",chosen,term,scope,[],None,coverage,{"reason":"applicability context requires an injected source-aware matcher","request_context":request_context})
        matches=[context.context_matcher(item,request_context) for item in hits]
        if any(v is None for v in matches):
            return DefinitionSearchResult("needs_review",chosen,term,scope,[],None,context.index.coverage(),{"reason":"applicability could not be resolved for every candidate","request_context":request_context})
        hits=[item for item,matched in zip(hits,matches) if matched]
    ranking_method=retrieval_method+"_then_stable_source_order"
    ranking_diagnostics={}
    if chosen=="best" and len(hits)>1:
        if context.reranker:
            reranked=context.reranker(hits,{"term":term,"scope_doc_ids":scope})
            if {r.get("entry_id") for r in reranked}!={r.get("entry_id") for r in hits}: raise ValueError("reranker must preserve the complete scoped candidate set")
            hits=reranked; ranking_method="injected_reranker_over_complete_scoped_candidates"
        else:
            try: local=_local_bge_rank(term,hits)
            except Exception as exc: local=None; ranking_diagnostics={"local_bge_error":str(exc)}
            if local is not None:
                hits,ranking_diagnostics=local
                ranking_method="local_bge_full-definition_windows"
    coverage=context.index.coverage(); snapshot=context.source_snapshot or getattr(context.index,"snapshot_fingerprint",lambda:coverage)(); fingerprint=hashlib.sha256(json.dumps({"term":term,"context":request_context,"scope":scope,"mode":chosen,"coverage":coverage,"snapshot":snapshot,"source_freshness":freshness,"normalizer_version":NORMALIZER_VERSION,"sort_version":SORT_VERSION,"ranking_method":ranking_method,"candidate_order":[x.get("entry_id") for x in hits],"retrieval_diagnostics":retrieval_diagnostics,"ranking_diagnostics":ranking_diagnostics},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    reference_gaps=getattr(context.index,"reference_gaps",lambda _term,_scope:[])(term,scope)
    fingerprint=hashlib.sha256((fingerprint+json.dumps(reference_gaps,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
    offset=_cursor_decode(cursor,fingerprint) if cursor else 0
    total=len(hits); alternates=total
    doc_complete=getattr(context.index,"doc_complete",lambda d:coverage.get("corpus_complete",False))
    incomplete_docs=[did for did in scope if not doc_complete(did)]
    if chosen=="best" and incomplete_docs: page=[]; next_cursor=None
    elif chosen=="best" and fallback_used:
        page=[]; next_cursor=None
    elif chosen=="best": page=hits[:1]; next_cursor=None
    else:
        page=hits[offset:offset+page_size]
        next_cursor=_cursor_encode(offset+len(page),fingerprint) if offset+len(page)<total else None
    status="needs_review" if fallback_used and not incomplete_docs and not reference_gaps else ("partial" if incomplete_docs or reference_gaps else ("ok" if page else ("not_found" if coverage.get("corpus_complete") else "partial")))
    diagnostics={"indexed_matches":total,"alternate_count":max(0,total-1) if chosen=="best" else total,"incomplete_scope_doc_ids":incomplete_docs,"completeness":"only audited and atomically completed indexed documents; extraction coverage governs corpus claims","source_freshness":freshness,"reference_gaps":reference_gaps,"retrieval":retrieval_method,"retrieval_details":retrieval_diagnostics,"ranking":ranking_method,"ranking_details":ranking_diagnostics,"dense_fallback":"typed exact/alias, whole-term morphology, injected scoped dense adapter, then full-scope BM25","request_context":request_context}
    if fallback_used: diagnostics["proposed_candidates"]=[_present(x) for x in hits]; diagnostics["reason"]="Dense/BM25 relevance is not proof of term equivalence or sense; candidates require source-aware review."
    return DefinitionSearchResult(status,chosen,term,scope,[_present(x) for x in page],next_cursor,coverage,diagnostics)
