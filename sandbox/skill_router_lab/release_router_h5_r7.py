"""Trusted-host release preflight for H5 r7. Required command: <trusted-python.exe> -I -S <absolute release_router_h5_r7.py> --anchor-launcher-sha256 SHA --anchor-lock-sha256 SHA --anchor-manifest-sha256 SHA --action prepare status. -I -S is mandatory."""
import argparse, ast, hashlib, importlib.abc, importlib.util, json, os, runpy, sys
from pathlib import Path

TRUSTED_INTERPRETER = r"C:\Users\buleo\miniconda3\python.exe"
TRUSTED_SITELIB = r"C:\Users\buleo\miniconda3\Lib\site-packages"
REQUIRED_EXECUTED_ROOTS = [
    "h5_r7_fixture_wrapper.py",
    "release_router_h5_r7.py",
    "test_router_h5_r5_offline.py", "test_router_h5_r5_lifecycle_boundaries.py",
    "test_router_subscription_h5_r5_coordinator.py", "test_router_h5_r5_trust.py", "test_router_h5_r7_trust.py",
    "test_router_h5_offline.py", "test_router_h5_lifecycle_boundaries.py",
    "test_router_subscription_h5_coordinator.py", "test_router_h4_offline.py",
    "test_router_h4_lifecycle_boundaries.py", "test_router_subscription_h4_coordinator.py",
    "test_router_h3.py", "test_router_h3_r5.py", "test_router_h3_r6.py",
    "test_router_h3_r7.py", "test_router_h3_r8_provenance.py",
    "test_router_h3_r9_acceptance.py", "test_router_h3_r10_critical_identity.py",
    "test_router_draft_v2.py", "test_router_v2_replay_eval.py",
    "test_router_subscription_r10_coordinator.py", "freeze_router_v3_h5_r7.py",
    "router_subscription_lifecycle_h5_r7.py", "prepare_router_subscription_h5_r7.py",
    "replay_router_h4_for_h5_r7.py", "prepare_router_subscription_h4.py",
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
def _safe_bootstrap(here):
 if not (sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.flags.safe_path):
  raise SystemExit("unsafe bootstrap: rerun the absolute launcher with the trusted interpreter and -I -S")
 exe=Path(sys.executable).resolve(); prefix=Path(sys.base_prefix).resolve()
 if str(exe).casefold()!=str(Path(TRUSTED_INTERPRETER).resolve()).casefold() or prefix not in exe.parents:
  raise SystemExit("untrusted Python executable location")
 expected=[prefix/"python313.zip",prefix/"DLLs",prefix/"Lib",prefix]
 actual=[Path(entry).resolve() for entry in sys.path]
 if actual!=[x.resolve() for x in expected]: raise SystemExit("unexpected isolated interpreter sys.path")
 for resolved in actual:
  if resolved == here or here in resolved.parents or resolved == Path.cwd().resolve():
   raise SystemExit("unsafe import path before release preflight")

def _reject_local_shadows(here, pinned_paths, external_paths):
 imports=set()
 for rel in pinned_paths:
  tree=ast.parse((here/rel).read_text(encoding="utf-8"))
  for node in ast.walk(tree):
   if isinstance(node,ast.Import): imports.update(alias.name.split(".")[0] for alias in node.names)
   elif isinstance(node,ast.ImportFrom) and node.module: imports.add(node.module.split(".")[0])
 approved=set(pinned_paths)
 external_roots={Path(rel).parts[0].removesuffix(".py") for rel in external_paths if Path(rel).parts}
 imports.update(external_roots)
 forbidden={"sitecustomize.py","usercustomize.py","sitecustomize","usercustomize"}
 if any(entry.name.casefold() in forbidden for entry in here.iterdir()): raise SystemExit("customize module/package present in release root")
 for name in imports:
  module_file=name+".py"
  package=here/name
  if (here/module_file).exists() and module_file not in approved:
   raise SystemExit("unexpected local import shadow: "+module_file)
  if package.exists() and package.is_dir():
   raise SystemExit("unexpected local package directory shadow: "+name)
 for p in here.iterdir():
  if p.name.casefold().endswith(".pth"): raise SystemExit(".pth file present in release root")
  if p.is_symlink(): raise SystemExit("reparse/symlink path in release root: "+p.name)

class _PinnedRootFinder(importlib.abc.MetaPathFinder):
 def __init__(self, root, closure): self.root=root; self.closure=closure
 def find_spec(self, fullname, path=None, target=None):
  rel=fullname.replace(".","/")+".py"
  if rel not in self.closure: return None
  source=self.root/rel
  return importlib.util.spec_from_file_location(fullname,source)

def _verify_loaded_origins(here, dep_root, closure, deps, prefix):
 origins={}
 stdlib_roots={ (prefix/x).resolve() for x in ("Lib","DLLs") }
 stdlib_roots.add(prefix.resolve())
 for name,module in sorted(sys.modules.items()):
  filename=getattr(module,"__file__",None)
  if not filename: continue
  source=Path(filename).resolve()
  try: rel=source.relative_to(here).as_posix()
  except ValueError: rel=None
  if rel is not None:
   if rel not in closure or sha(source)!=closure[rel]: raise SystemExit("unverified local import origin: "+name)
   origins[name]=str(source); continue
  try: rel=source.relative_to(dep_root).as_posix()
  except ValueError: rel=None
  if rel is not None:
   if rel not in deps or sha(source)!=deps[rel]: raise SystemExit("unverified external import origin: "+name)
   origins[name]=str(source); continue
  if not any(source==root or root in source.parents for root in stdlib_roots):
   raise SystemExit("import origin outside pinned roots: "+name+" "+str(source))
  origins[name]=str(source)
 return {"interpreter":str(Path(sys.executable).resolve()),"sys_path":[str(Path(x).resolve()) for x in sys.path],"import_origins":origins}

def main():
 here=Path(__file__).resolve().parent
 _safe_bootstrap(here)
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--anchor-launcher-sha256",required=True);p.add_argument("--anchor-lock-sha256",required=True);p.add_argument("--anchor-manifest-sha256",required=True);p.add_argument("--action",choices=("freeze","replay","prepare"),required=True);a,forward=p.parse_known_args()
 run=here/"router_subscription_run_tuning_v3_h5_r7_20261010"; lockp=run/"release_lock.json"
 if sha(__file__)!=a.anchor_launcher_sha256: raise SystemExit("external launcher anchor mismatch")
 if sha(lockp)!=a.anchor_lock_sha256: raise SystemExit("external release-lock anchor mismatch")
 lock=json.loads(lockp.read_text(encoding="utf-8"))
 if lock.get("run_id")!="router-v3-h5-tuning-20261010-r7": raise SystemExit("r6 release-lock identity mismatch")
 if lock.get("manifest_sha256")!=a.anchor_manifest_sha256 or sha(run/"manifest.json")!=a.anchor_manifest_sha256: raise SystemExit("external manifest anchor mismatch")
 if sha(run/"hash_inventory.json")!=lock["hash_inventory_sha256"]: raise SystemExit("hash inventory anchor mismatch")
 manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
 for rel,want in manifest["files_sha256"].items():
  if sha(run/rel)!=want: raise SystemExit("frozen resource changed: "+rel)
 for rel,want in manifest["historical_h2_runtime_sha256"].items():
  if sha(run/"historical_h2_runtime"/rel)!=want: raise SystemExit("H2 snapshot changed: "+rel)
 for name in ("receipts.jsonl","request_envelopes.jsonl","receipt_envelope_bindings.jsonl"):
  if (run/name).exists(): raise SystemExit("offline r4 evidence stream appeared: "+name)
 expected_tree=lock["r7_content_tree_sha256"]
 actual_tree={p.relative_to(run).as_posix():sha(p) for p in run.rglob("*") if p.is_file() and p.name!="release_lock.json"}
 if actual_tree!=expected_tree: raise SystemExit("r6 content tree membership/hash mismatch")
 evidence_tree={p.relative_to(run/"evidence").as_posix():sha(p) for p in (run/"evidence").rglob("*") if p.is_file()}
 if evidence_tree!=lock["r7_evidence_sha256"]: raise SystemExit("r6 evidence tree membership/hash mismatch")
 expected=lock["executable_closure_sha256"]
 required=_derived_import_closure(here)
 _reject_local_shadows(here, expected, lock.get("external_dependencies_sha256",{}))
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
 # External third-party closure is pinned by the externally anchored lock and imported only after all checkout validation.
 dep_root=Path(TRUSTED_SITELIB).resolve()
 deps=lock.get("external_dependencies_sha256",{})
 actual_deps={rel:sha(dep_root/rel) for rel in deps if (dep_root/rel).is_file()}
 if actual_deps!=deps: raise SystemExit("external dependency closure mismatch")
 package_names={Path(rel).parts[0] for rel in deps if Path(rel).parts}
 for name in package_names:
  root=dep_root/name
  if not root.exists(): raise SystemExit("pinned external package missing: "+name)
  expected_package={rel:digest for rel,digest in deps.items() if rel==name or rel.startswith(name+"/")}
  if root.is_file():
   if expected_package!={name:sha(root)}: raise SystemExit("external module pin mismatch: "+name)
  else:
   found={p.relative_to(dep_root).as_posix():sha(p) for p in root.rglob("*") if p.is_file()}
   if found!=expected_package: raise SystemExit("external package membership mismatch: "+name)
 os.environ["ROUTER_H5_R7_PREFLIGHT_MANIFEST_SHA256"]=a.anchor_manifest_sha256
 sys.path.insert(0,str(dep_root))
 sys.meta_path.insert(0,_PinnedRootFinder(here,expected))
 if a.action=="freeze": runpy.run_module("freeze_router_v3_h5_r7",run_name="__main__")
 elif a.action=="replay": sys.argv=[str(here/"replay_router_h4_for_h5_r7.py")]+forward;runpy.run_module("replay_router_h4_for_h5_r7",run_name="__main__")
 else: sys.argv=[str(here/"prepare_router_subscription_h5_r7.py")]+forward;runpy.run_module("prepare_router_subscription_h5_r7",run_name="__main__")
 print("H5_R7_BOOTSTRAP_AUDIT "+json.dumps(_verify_loaded_origins(here,dep_root,expected,deps,Path(sys.base_prefix).resolve()),sort_keys=True),file=sys.stderr)
if __name__=="__main__": main()
