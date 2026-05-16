from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.eval.chunk_quality import ChunkQualityReporter, build_run_context, save_baseline_comparison
from mr_norm.indexing.qdrant_adapter import (
    build_qdrant_index,
    ensure_qdrant_payload_indexes,
    save_index_build_report,
    save_index_verify_report,
    verify_qdrant_payload_indexes,
)
from mr_norm.retrieval.compare import (
    run_retrieval_compare,
    run_retrieval_compare_batch,
    save_retrieval_compare_batch_report,
    save_retrieval_compare_report,
)
from mr_norm.retrieval.contracts import ToolRequest
from mr_norm.retrieval.tools.payload import run_payload_tool
from mr_norm.retrieval.tools.point import run_point_tool
from mr_norm.retrieval.tools.vector import run_vector_tool
from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.final_answer import build_final_answer
from mr_norm.runtime.llm_providers import build_pipeline_llm_providers
from mr_norm.runtime.pipeline import PipelineBatchDefaults, run_pipeline, run_pipeline_batch, save_pipeline_report
from mr_norm.runtime.planner import build_planner
from mr_norm.runtime.reranker import build_reranker
from mr_norm.runtime.tool_runner import run_runtime, run_runtime_batch, save_runtime_report
from mr_norm.tools.chunker import ChunkBuilder, MetadataExtractionError
from mr_norm.tools.rtf_processor import RtfProcessor, RtfReadError, atomic_write_json, pick_size_diverse_rtf_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mr_norm.apps.main")
    parser.add_argument("--root", type=Path, default=None, help="Project root, defaults to current mr_norm workspace.")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-rtf", help="Convert RTF files to marked TXT and structured JSON.")
    ingest.add_argument("--limit", type=int, default=None)
    ingest.add_argument("--per-file-timeout-sec", type=float, default=120.0)
    ingest.add_argument(
        "--smoke-diverse-n",
        type=int,
        default=None,
        metavar="N",
        help="Process N RTF files spread by file size (smallest…largest). Ignores --limit.",
    )

    chunk = sub.add_parser("chunk", help="Build qdrant_chunks.json from structured documents.")
    chunk.add_argument("--max-chars", type=int, default=1600)

    build = sub.add_parser("build-chunks", help="Run RTF processing, chunking and quality report.")
    build.add_argument("--limit", type=int, default=None)
    build.add_argument("--max-chars", type=int, default=1600)
    build.add_argument("--per-file-timeout-sec", type=float, default=120.0)
    build.add_argument(
        "--smoke-diverse-n",
        type=int,
        default=None,
        metavar="N",
        help="Build chunks for N RTF files spread by file size (smallest…largest). Ignores --limit.",
    )

    quality = sub.add_parser("quality-report", help="Build quality report for output/qdrant_chunks.json.")
    quality.add_argument("--scope", choices=["existing-output", "smoke", "full"], default="existing-output")

    compare = sub.add_parser("compare-baseline", help="Compare output/qdrant_chunks.json with rag_norm baseline.")
    compare.add_argument("--baseline", type=Path, default=None)

    index_verify = sub.add_parser("index-verify", help="Validate qdrant_chunks.json readiness for Qdrant indexing.")
    index_verify.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")

    index_build = sub.add_parser("index-build", help="Embed chunks and upsert them into Qdrant.")
    index_build.add_argument("--rebuild", action="store_true", help="Delete and recreate the target collection.")
    index_build.add_argument("--limit", type=int, default=None, help="Index only the first N chunks.")
    index_build.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")

    index_schema_verify = sub.add_parser(
        "index-schema-verify",
        help="Verify live Qdrant payload indexes needed by retrieval tools.",
    )
    index_schema_verify.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")

    index_ensure_payload = sub.add_parser(
        "index-ensure-payload-indexes",
        help="Create missing retrieval payload indexes without rebuilding or upserting points.",
    )
    index_ensure_payload.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")

    retrieval_vector = sub.add_parser("retrieval-vector", help="Run deterministic vector retrieval tool.")
    add_retrieval_args(retrieval_vector, query_required=True)

    retrieval_payload = sub.add_parser("retrieval-payload", help="Run deterministic payload retrieval tool.")
    add_retrieval_args(retrieval_payload, query_required=False)

    retrieval_point = sub.add_parser("retrieval-point", help="Run deterministic point retrieval tool.")
    add_retrieval_args(retrieval_point, query_required=False)

    retrieval_compare = sub.add_parser("retrieval-compare", help="Compare deterministic retrieval pipelines.")
    add_retrieval_args(retrieval_compare, query_required=False)
    retrieval_compare.add_argument(
        "--pipelines",
        default="point,payload,vector,hybrid",
        help="Comma-separated pipelines: point,payload,vector,hybrid.",
    )
    retrieval_compare.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")

    retrieval_compare_batch = sub.add_parser(
        "retrieval-compare-batch",
        help="Compare deterministic retrieval pipelines for a JSON question set.",
    )
    retrieval_compare_batch.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="Path to retrieval questions JSON. Defaults to tests/fixtures/retrieval_questions.json.",
    )
    retrieval_compare_batch.add_argument(
        "--pipelines",
        default="point,payload,vector,hybrid",
        help="Comma-separated pipelines: point,payload,vector,hybrid.",
    )
    retrieval_compare_batch.add_argument("--limit", type=int, default=5)
    retrieval_compare_batch.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")
    retrieval_compare_batch.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")

    rag_runtime = sub.add_parser("rag-runtime", help="Run deterministic RAG runtime evidence retrieval.")
    add_retrieval_args(rag_runtime, query_required=False)
    rag_runtime.add_argument("--mode", default="evidence", help="Runtime mode, default: evidence.")
    rag_runtime.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")

    rag_runtime_batch = sub.add_parser("rag-runtime-batch", help="Run deterministic RAG runtime for a JSON question set.")
    rag_runtime_batch.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="Path to retrieval questions JSON. Defaults to tests/fixtures/retrieval_questions.json.",
    )
    rag_runtime_batch.add_argument("--profile", choices=["fast", "balanced", "deep"], default="balanced")
    rag_runtime_batch.add_argument("--limit", type=int, default=10)
    rag_runtime_batch.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")
    rag_runtime_batch.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")

    rag_pipeline = sub.add_parser("rag-pipeline", help="Run deterministic RAG runtime plus post-retrieval pipeline.")
    add_retrieval_args(rag_pipeline, query_required=False)
    rag_pipeline.add_argument("--mode", default="evidence", help="Runtime mode, default: evidence.")
    rag_pipeline.add_argument(
        "--planner",
        choices=["deterministic", "prompt"],
        default="deterministic",
        help="Planner backend.",
    )
    rag_pipeline.add_argument(
        "--reranker",
        choices=["passthrough", "score", "prompt"],
        default="passthrough",
        help="Reranker backend.",
    )
    rag_pipeline.add_argument(
        "--final-answer",
        dest="final_answer_backend",
        choices=["evidence", "prompt"],
        default="evidence",
        help="Final answer backend.",
    )
    rag_pipeline.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")
    rag_pipeline.add_argument(
        "--llm-provider",
        choices=["none", "ollama", "polza"],
        default="none",
        help="Optional live LLM provider for prompt backends. Default: none.",
    )
    rag_pipeline.add_argument("--planner-model", default="", help="Override planner LLM model id.")
    rag_pipeline.add_argument("--reranker-model", default="", help="Override reranker LLM model id.")
    rag_pipeline.add_argument(
        "--final-answer-model",
        default="",
        help="Override final answer LLM model id.",
    )
    rag_pipeline.add_argument(
        "--keys-path",
        type=Path,
        default=None,
        help="Path to local keys file for Polza. Defaults to project-root keys when present.",
    )

    rag_pipeline_batch = sub.add_parser(
        "rag-pipeline-batch",
        help="Run full RAG pipeline for a JSON question set with evaluation metrics.",
    )
    rag_pipeline_batch.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="Path to retrieval questions JSON. Defaults to tests/fixtures/retrieval_questions.json.",
    )
    rag_pipeline_batch.add_argument("--profile", choices=["fast", "balanced", "deep"], default="balanced")
    rag_pipeline_batch.add_argument("--limit", type=int, default=10)
    rag_pipeline_batch.add_argument("--mode", default="evidence", help="Runtime mode, default: evidence.")
    rag_pipeline_batch.add_argument(
        "--planner",
        choices=["deterministic", "prompt"],
        default="deterministic",
        help="Planner backend.",
    )
    rag_pipeline_batch.add_argument(
        "--reranker",
        choices=["passthrough", "score", "prompt"],
        default="passthrough",
        help="Reranker backend.",
    )
    rag_pipeline_batch.add_argument(
        "--final-answer",
        dest="final_answer_backend",
        choices=["evidence", "prompt"],
        default="evidence",
        help="Final answer backend.",
    )
    rag_pipeline_batch.add_argument(
        "--llm-provider",
        choices=["none", "ollama", "polza"],
        default="none",
        help="Optional live LLM provider for prompt backends. Default: none.",
    )
    rag_pipeline_batch.add_argument("--planner-model", default="", help="Override planner LLM model id.")
    rag_pipeline_batch.add_argument("--reranker-model", default="", help="Override reranker LLM model id.")
    rag_pipeline_batch.add_argument(
        "--final-answer-model",
        default="",
        help="Override final answer LLM model id.",
    )
    rag_pipeline_batch.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")
    rag_pipeline_batch.add_argument("--save-report", action="store_true", help="Save JSON and Markdown reports.")
    rag_pipeline_batch.add_argument(
        "--keys-path",
        type=Path,
        default=None,
        help="Path to local keys file for Polza. Defaults to project-root keys when present.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = ProjectPaths.from_root(args.root)
    paths.ensure_output_dirs()
    exit_code = 0

    try:
        if args.command == "ingest-rtf":
            only_paths = None
            if args.smoke_diverse_n is not None:
                only_paths = pick_size_diverse_rtf_paths(paths.input_dir, args.smoke_diverse_n)
            result = run_ingest(
                paths,
                limit=args.limit if only_paths is None else None,
                only_paths=only_paths,
                per_file_timeout_sec=args.per_file_timeout_sec,
            )
        elif args.command == "chunk":
            result = run_chunk(paths, args.max_chars)
        elif args.command == "build-chunks":
            only_paths = None
            scope = "full"
            if args.smoke_diverse_n is not None:
                only_paths = pick_size_diverse_rtf_paths(paths.input_dir, args.smoke_diverse_n)
                scope = "smoke"
            elif args.limit is not None:
                scope = f"limit:{args.limit}"
            result = run_build_chunks(
                paths,
                args.limit if only_paths is None else None,
                args.max_chars,
                only_paths=only_paths,
                scope=scope,
                per_file_timeout_sec=args.per_file_timeout_sec,
            )
        elif args.command == "quality-report":
            result = ChunkQualityReporter(paths).report(
                run_context=build_run_context(command="quality-report", paths=paths, scope=args.scope)
            )
        elif args.command == "compare-baseline":
            result = save_baseline_comparison(paths, args.baseline)
        elif args.command == "index-verify":
            result = save_index_verify_report(paths, resolve_indexing_config(args))
        elif args.command == "index-build":
            result = save_index_build_report(
                paths,
                build_qdrant_index(
                    paths,
                    resolve_indexing_config(args),
                    rebuild=args.rebuild,
                    limit=args.limit,
                ),
            )
        elif args.command == "index-schema-verify":
            result = verify_qdrant_payload_indexes(resolve_indexing_config(args))
            if not result.get("passes"):
                exit_code = 1
        elif args.command == "index-ensure-payload-indexes":
            result = ensure_qdrant_payload_indexes(resolve_indexing_config(args))
            if not result.get("passes"):
                exit_code = 1
        elif args.command == "retrieval-vector":
            result = run_vector_tool(build_tool_request(args), resolve_indexing_config(args)).to_dict()
        elif args.command == "retrieval-payload":
            result = run_payload_tool(build_tool_request(args), resolve_indexing_config(args)).to_dict()
        elif args.command == "retrieval-point":
            result = run_point_tool(build_tool_request(args), resolve_indexing_config(args)).to_dict()
        elif args.command == "retrieval-compare":
            result = run_retrieval_compare(
                build_tool_request(args),
                resolve_indexing_config(args),
                pipelines=args.pipelines,
            )
            if args.save_report:
                result = save_retrieval_compare_report(result, paths.reports_dir)
        elif args.command == "retrieval-compare-batch":
            questions_path = resolve_questions_path(paths, args.questions)
            result = run_retrieval_compare_batch(
                load_retrieval_questions(questions_path),
                resolve_indexing_config(args),
                pipelines=args.pipelines,
                limit=args.limit,
            )
            result["questions_path"] = str(questions_path)
            if args.save_report:
                result = save_retrieval_compare_batch_report(result, paths.reports_dir)
        elif args.command == "rag-runtime":
            runtime_request = RuntimeRequest(
                query=args.query,
                filters=build_tool_request(args).filters,
                limit=args.limit,
                profile=args.profile,
                trace_id=args.trace_id,
                mode=args.mode,
            )
            result = run_runtime(runtime_request, resolve_indexing_config(args)).to_dict()
            if getattr(args, "save_report", False):
                result = save_runtime_report(result, paths.reports_dir)
        elif args.command == "rag-runtime-batch":
            questions_path = resolve_questions_path(paths, args.questions)
            result = run_runtime_batch(
                load_retrieval_questions(questions_path),
                resolve_indexing_config(args),
                profile=args.profile,
                limit=args.limit,
            )
            result["questions_path"] = str(questions_path)
            if args.save_report:
                result = save_runtime_report(result, paths.reports_dir, prefix="rag_runtime_batch")
        elif args.command == "rag-pipeline":
            runtime_request = RuntimeRequest(
                query=args.query,
                filters=build_tool_request(args).filters,
                limit=args.limit,
                profile=args.profile,
                trace_id=args.trace_id,
                mode=args.mode,
            )
            llm_providers = build_pipeline_llm_providers(
                args.llm_provider,
                planner_model=args.planner_model or None,
                reranker_model=args.reranker_model or None,
                final_answer_model=args.final_answer_model or None,
                planner_backend=args.planner,
                reranker_backend=args.reranker,
                final_answer_backend=args.final_answer_backend,
                keys_path=resolve_keys_path(paths, args.keys_path),
            )
            pipeline = run_pipeline(
                runtime_request,
                resolve_indexing_config(args),
                planner=build_planner(args.planner, provider=llm_providers.planner),
                reranker=build_reranker(args.reranker, provider=llm_providers.reranker),
                final_answer=build_final_answer(
                    args.final_answer_backend,
                    provider=llm_providers.final_answer,
                ),
            )
            result = pipeline.to_dict()
            if getattr(args, "save_report", False):
                result = save_pipeline_report(result, paths.reports_dir)
        elif args.command == "rag-pipeline-batch":
            questions_path = resolve_questions_path(paths, args.questions)
            result = run_pipeline_batch(
                load_retrieval_questions(questions_path),
                resolve_indexing_config(args),
                defaults=PipelineBatchDefaults(
                    profile=args.profile,
                    limit=args.limit,
                    planner_backend=args.planner,
                    reranker_backend=args.reranker,
                    final_answer_backend=args.final_answer_backend,
                    llm_provider=args.llm_provider,
                    planner_model=args.planner_model or None,
                    reranker_model=args.reranker_model or None,
                    final_answer_model=args.final_answer_model or None,
                    keys_path=resolve_keys_path(paths, args.keys_path),
                ),
            )
            result["questions_path"] = str(questions_path)
            if args.save_report:
                result = save_pipeline_report(result, paths.reports_dir, prefix="rag_pipeline_batch")
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except (RtfReadError, MetadataExtractionError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(to_printable_result(result), ensure_ascii=False, indent=2))
    return exit_code


def resolve_indexing_config(args: argparse.Namespace) -> IndexingConfig:
    config = IndexingConfig.from_env()
    collection_name = getattr(args, "collection_name", None)
    if collection_name:
        return replace(config, collection_name=collection_name)
    return config


def add_retrieval_args(parser: argparse.ArgumentParser, *, query_required: bool) -> None:
    parser.add_argument("--query", default="", required=query_required)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--profile", choices=["fast", "balanced", "deep"], default="fast")
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")
    parser.add_argument("--filename", default="")
    parser.add_argument("--doc-name", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--heading-path-text", default="")
    parser.add_argument("--point-number", default="")
    parser.add_argument("--point-identity-key", default="")
    parser.add_argument("--chunk-id", default="")


def build_tool_request(args: argparse.Namespace) -> ToolRequest:
    filter_values = {
        "filename": args.filename,
        "doc_name": args.doc_name,
        "text": args.text,
        "heading_path_text": args.heading_path_text,
        "point_number": args.point_number,
        "point_identity_key": args.point_identity_key,
        "chunk_id": args.chunk_id,
    }
    filters = {key: value for key, value in filter_values.items() if value}
    return ToolRequest(
        query=args.query,
        filters=filters,
        limit=args.limit,
        profile=args.profile,
        trace_id=args.trace_id,
    )


def resolve_keys_path(paths: ProjectPaths, keys_path: Path | None) -> Path | None:
    if keys_path is not None:
        path = keys_path if keys_path.is_absolute() else paths.root / keys_path
        return path if path.is_file() else None
    candidate = paths.root / "keys"
    return candidate if candidate.is_file() else None


def resolve_questions_path(paths: ProjectPaths, questions_path: Path | None) -> Path:
    path = questions_path or paths.root / "tests" / "fixtures" / "retrieval_questions.json"
    if not path.is_absolute():
        path = paths.root / path
    return path


def load_retrieval_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("retrieval questions file must contain a JSON array")
    return data


def run_ingest(
    paths: ProjectPaths,
    limit: int | None = None,
    only_paths: list[Path] | None = None,
    per_file_timeout_sec: float = 120.0,
) -> dict[str, Any]:
    start = time.perf_counter()
    processor = RtfProcessor(paths)
    results = processor.process_all(
        limit=limit,
        only_paths=only_paths,
        per_file_timeout_sec=per_file_timeout_sec,
    )
    report = {
        "command": "ingest-rtf",
        "documents_total": len(results),
        "documents_processed_ok": sum(1 for item in results if item.paragraphs > 0),
        "documents_failed": sum(1 for item in results if item.paragraphs == 0),
        "documents_with_read_warnings": sum(1 for item in results if item.error),
        "word_cleanup": processor.last_word_cleanup,
        "smoke_diverse_paths": [str(p) for p in only_paths] if only_paths else None,
        "per_file_timeout_sec": per_file_timeout_sec,
        "elapsed_sec": round(time.perf_counter() - start, 3),
        "documents": [item.__dict__ for item in results],
    }
    atomic_write_json(paths.reports_dir / "rtf_processing_last.json", report)
    return report


def run_chunk(
    paths: ProjectPaths,
    max_chars: int = 1600,
    structured_paths: list[Path] | None = None,
    scope: str = "existing-structured",
) -> dict[str, Any]:
    start = time.perf_counter()
    builder = ChunkBuilder(paths, max_chars=max_chars)
    chunks = builder.build_all(structured_paths=structured_paths)
    elapsed = time.perf_counter() - start
    report = ChunkQualityReporter(paths).report(
        chunks,
        run_context=build_run_context(
            command="chunk",
            paths=paths,
            scope=scope,
            input_paths=structured_paths,
            elapsed_sec=elapsed,
        ),
    )
    return {
        "command": "chunk",
        "scope": scope,
        "chunks_total": len(chunks),
        "chunks_json": str(paths.chunks_json),
        "quality_report": report.get("report_path"),
        "quality_markdown_report": report.get("markdown_report_path"),
        "quality_gate_passes": report.get("passes_quality_gate"),
        "blocking_defects": report.get("blocking_defects"),
        "metadata_manifest_md": str(paths.metadata_manifest_md),
        "pue_canonical_applied_count": len(builder.manifest_pue),
        "metadata_fallback_count": len(builder.manifest_other),
        "elapsed_sec": round(elapsed, 3),
    }


def run_build_chunks(
    paths: ProjectPaths,
    limit: int | None = None,
    max_chars: int = 1600,
    only_paths: list[Path] | None = None,
    scope: str = "full",
    per_file_timeout_sec: float = 120.0,
) -> dict[str, Any]:
    start = time.perf_counter()
    ingest_start = time.perf_counter()
    ingest = run_ingest(paths, limit=limit, only_paths=only_paths, per_file_timeout_sec=per_file_timeout_sec)
    ingest_elapsed = time.perf_counter() - ingest_start
    chunk_start = time.perf_counter()
    structured_paths = [Path(item["structured_path"]) for item in ingest.get("documents", []) if item.get("structured_path")]
    chunk = run_chunk(paths, max_chars=max_chars, structured_paths=structured_paths, scope=scope)
    chunk_elapsed = time.perf_counter() - chunk_start
    report = {
        "command": "build-chunks",
        "scope": scope,
        "limit": limit,
        "smoke_diverse_paths": [str(p) for p in only_paths] if only_paths else None,
        "ingest": ingest,
        "chunk": chunk,
        "timing": {
            "total_sec": round(time.perf_counter() - start, 3),
            "rtf_processing_sec": round(ingest_elapsed, 3),
            "chunking_and_report_sec": round(chunk_elapsed, 3),
        },
    }
    atomic_write_json(paths.reports_dir / "build_chunks_last.json", report)
    return report


def to_printable_result(result: dict[str, Any]) -> dict[str, Any]:
    if "ingest" in result and isinstance(result["ingest"], dict):
        compact = dict(result)
        ingest = dict(compact["ingest"])
        docs = ingest.pop("documents", [])
        ingest["documents_preview"] = docs[:5]
        compact["ingest"] = ingest
        return compact
    return result


if __name__ == "__main__":
    raise SystemExit(main())
