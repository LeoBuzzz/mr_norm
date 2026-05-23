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
from mr_norm.apps.human_cli import HumanCliOptions, collect_interactive_options, run_human_norm_lookup
from mr_norm.data.normative_registry import (
    RegistryCoverageError,
    RegistryMissingMode,
    check_registry_coverage,
)
from mr_norm.tools.chunker import ChunkBuilder, MetadataExtractionError
from mr_norm.tools.document_knowledge_build import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    assemble_knowledge_index,
    build_document_annotations,
    extract_document_openings,
    knowledge_paths,
    run_knowledge_build,
)
from mr_norm.tools.marked_docs_sync import sync_marked_docs_with_input
from mr_norm.tools.rtf_processor import RtfProcessor, RtfReadError, atomic_write_json, pick_size_diverse_rtf_paths


def _add_registry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=None,
        help="Путь к normative_documents_registry.json (по умолчанию — src/mr_norm/data/…).",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Не использовать реестр нормативных документов (только extract_metadata).",
    )
    parser.add_argument(
        "--registry-missing",
        choices=[m.value for m in RegistryMissingMode],
        default=RegistryMissingMode.FAIL.value,
        help="Поведение при отсутствии файла в реестре (не для ПУЭ по имени файла).",
    )


def _registry_missing_from_args(args: argparse.Namespace) -> RegistryMissingMode:
    return RegistryMissingMode(str(getattr(args, "registry_missing", RegistryMissingMode.FAIL.value)))


def _add_knowledge_ollama_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Модель Ollama для аннотаций.")
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_BASE_URL, help="URL Ollama API.")
    parser.add_argument("--timeout", type=int, default=300, help="Таймаут запроса к Ollama, сек.")
    parser.add_argument("--max-input-chars", type=int, default=14000, help="Обрезка opening_text в промпте.")
    parser.add_argument("--limit", type=int, default=0, help="Обработать только первые N документов (0 = все).")
    parser.add_argument("--delay", type=float, default=0.0, help="Пауза между запросами к Ollama, сек.")


def _add_knowledge_args(sub: argparse._SubParsersAction) -> None:
    knowledge_openings = sub.add_parser(
        "knowledge-openings",
        help="Извлечь начала документов из qdrant_chunks.json (группировка по doc_id).",
    )
    knowledge_openings.add_argument(
        "--chunks",
        type=Path,
        default=None,
        help="Путь к qdrant_chunks.json (по умолчанию output/qdrant_chunks.json).",
    )
    knowledge_openings.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Куда записать document_openings.json (по умолчанию output/knowledge/…).",
    )

    knowledge_annotations = sub.add_parser(
        "knowledge-annotations",
        help="Сгенерировать краткие аннотации документов через Ollama.",
    )
    knowledge_annotations.add_argument("--openings", type=Path, default=None)
    knowledge_annotations.add_argument("--output", type=Path, default=None)
    knowledge_annotations.add_argument(
        "--resume",
        action="store_true",
        help="Пропускать doc_id, для которых аннотация уже есть в output.",
    )
    _add_knowledge_ollama_args(knowledge_annotations)

    knowledge_index = sub.add_parser(
        "knowledge-index",
        help="Собрать document_knowledge_index.json из openings + annotations.",
    )
    knowledge_index.add_argument("--openings", type=Path, default=None)
    knowledge_index.add_argument("--annotations", type=Path, default=None)
    knowledge_index.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Путь к document_knowledge_index.json (по умолчанию src/mr_norm/config/knowledge/…).",
    )

    knowledge_build = sub.add_parser(
        "knowledge-build",
        help="Полный цикл: openings → annotations (Ollama) → knowledge index.",
    )
    knowledge_build.add_argument("--skip-openings", action="store_true")
    knowledge_build.add_argument("--skip-annotations", action="store_true")
    knowledge_build.add_argument("--skip-index", action="store_true")
    knowledge_build.add_argument("--resume", action="store_true", help="Продолжить аннотации с --resume.")
    _add_knowledge_ollama_args(knowledge_build)


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
    _add_registry_args(chunk)

    build = sub.add_parser("build-chunks", help="Run RTF processing, chunking and quality report.")
    build.add_argument("--limit", type=int, default=None)
    build.add_argument("--max-chars", type=int, default=1600)
    _add_registry_args(build)
    build.add_argument("--per-file-timeout-sec", type=float, default=120.0)
    build.add_argument(
        "--smoke-diverse-n",
        type=int,
        default=None,
        metavar="N",
        help="Build chunks for N RTF files spread by file size (smallest…largest). Ignores --limit.",
    )

    reg_check = sub.add_parser(
        "registry-check",
        help="Сверка stem RTF в input с match_stems реестра до чанкования.",
    )
    reg_check.add_argument("--registry-path", type=Path, default=None)
    reg_check.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON-отчёт (по умолчанию output/reports/registry_coverage.json).",
    )

    sync_md = sub.add_parser(
        "sync-marked-docs",
        help="Синхронизация input/*.rtf с output/marked_docs (удаление хвостов без Word).",
    )
    sync_md.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON-отчёт (по умолчанию output/reports/marked_docs_sync.json).",
    )

    quality = sub.add_parser("quality-report", help="Build quality report for output/qdrant_chunks.json.")
    quality.add_argument("--scope", choices=["existing-output", "smoke", "full"], default="existing-output")

    compare = sub.add_parser("compare-baseline", help="Compare output/qdrant_chunks.json with rag_norm baseline.")
    compare.add_argument("--baseline", type=Path, default=None)

    _add_knowledge_args(sub)

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

    norm_lookup = sub.add_parser(
        "norm-lookup",
        help="Human-friendly norm lookup: mode menu, short status, answer and sources.",
    )
    norm_lookup.add_argument("--query", default="", help="Question text. Omit for interactive prompts.")
    norm_lookup.add_argument(
        "--mode-preset",
        choices=["deterministic", "ollama", "polza"],
        default="",
        help="Work mode preset. Omit for interactive menu.",
    )
    norm_lookup.add_argument("--doc-name", default="", help="Optional doc_name filter.")
    norm_lookup.add_argument("--limit", type=int, default=10)
    norm_lookup.add_argument("--profile", choices=["fast", "balanced", "deep"], default="balanced")
    norm_lookup.add_argument(
        "--final-answer-model",
        default="",
        help="Override final answer LLM model (ollama/polza presets).",
    )
    norm_lookup.add_argument("--collection-name", default=None, help="Override MR_NORM_QDRANT_COLLECTION.")
    norm_lookup.add_argument(
        "--keys-path",
        type=Path,
        default=None,
        help="Path to local keys file for Polza. Defaults to project-root keys when present.",
    )
    norm_lookup.add_argument(
        "--understand-query",
        choices=["auto", "off", "llm"],
        default="",
        help="Pre-retrieval query understanding: auto (catalog), llm (catalog+LLM), off.",
    )
    norm_lookup.add_argument(
        "--enable-pue",
        action="store_true",
        help="Enable ПУЭ topic/abbreviation aliases in query planning (or set MR_NORM_ENABLE_PUE_ALIASES=1).",
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
        elif args.command == "sync-marked-docs":
            result = run_sync_marked_docs(paths, args)
        elif args.command == "registry-check":
            result = run_registry_check(paths, args)
            if not result.get("ok_for_chunking"):
                exit_code = 1
        elif args.command == "chunk":
            result = run_chunk(
                paths,
                args.max_chars,
                use_registry=not args.no_registry,
                registry_path=args.registry_path,
                registry_missing=_registry_missing_from_args(args),
            )
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
                use_registry=not args.no_registry,
                registry_path=args.registry_path,
                registry_missing=_registry_missing_from_args(args),
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
        elif args.command == "knowledge-openings":
            result = run_knowledge_openings(paths, args)
            if result.get("documents", 0) == 0:
                exit_code = 1
        elif args.command == "knowledge-annotations":
            result = run_knowledge_annotations(paths, args)
            if result.get("errors", 0) > 0:
                exit_code = 1
        elif args.command == "knowledge-index":
            result = run_knowledge_index(paths, args)
            if result.get("index_documents", 0) == 0:
                exit_code = 1
        elif args.command == "knowledge-build":
            result = run_knowledge_build_cmd(paths, args)
            if result.get("annotations_errors", 0) > 0:
                exit_code = 1
        elif args.command == "norm-lookup":
            enable_pue_aliases = True if args.enable_pue else None
            base_options = HumanCliOptions(
                query=args.query,
                mode_preset=args.mode_preset,
                doc_name=args.doc_name,
                limit=args.limit,
                profile=args.profile,
                final_answer_model=args.final_answer_model or None,
                understand_query=args.understand_query,
                enable_pue_aliases=enable_pue_aliases,
            )
            if not base_options.query or not base_options.mode_preset:
                options = collect_interactive_options(base_options)
            else:
                options = base_options
            run_human_norm_lookup(
                options,
                resolve_indexing_config(args),
                keys_path=resolve_keys_path(paths, args.keys_path),
                project_paths=paths,
            )
            return exit_code
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except (RtfReadError, MetadataExtractionError, RegistryCoverageError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1

    if result.get("command") == "ingest-rtf":
        print(format_ingest_rtf_console_summary(result))
        print()
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
    parser.add_argument("--doc-id", default="", help="Фильтр по doc_id (стабильный id документа в корпусе).")
    parser.add_argument("--doc-name", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--heading-path-text", default="")
    parser.add_argument("--point-number", default="")
    parser.add_argument("--point-identity-key", default="")
    parser.add_argument("--chunk-id", default="")


def build_tool_request(args: argparse.Namespace) -> ToolRequest:
    filter_values = {
        "doc_id": args.doc_id,
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
    sync_report = processor.last_sync_report.to_dict() if processor.last_sync_report else {}
    report = {
        "command": "ingest-rtf",
        "limit": limit,
        "marked_docs_sync": sync_report,
        "documents_total": len(results),
        "documents_processed_ok": sum(1 for item in results if item.paragraphs > 0),
        "documents_skipped_existing": sum(1 for item in results if item.skipped_existing),
        "documents_failed": sum(1 for item in results if item.paragraphs == 0 and not item.skipped_existing),
        "documents_with_read_warnings": sum(1 for item in results if item.error),
        "word_cleanup": processor.last_word_cleanup,
        "smoke_diverse_paths": [str(p) for p in only_paths] if only_paths else None,
        "per_file_timeout_sec": per_file_timeout_sec,
        "elapsed_sec": round(time.perf_counter() - start, 3),
        "documents": [item.__dict__ for item in results],
    }
    report_path = paths.reports_dir / "rtf_processing_last.json"
    report["report_path"] = str(report_path)
    atomic_write_json(report_path, report)
    return report


def _preflight_registry_coverage(
    paths: ProjectPaths,
    *,
    registry_path: Path | None,
    structured_paths: list[Path] | None,
) -> None:
    report = check_registry_coverage(
        registry_path=registry_path or paths.normative_registry_json,
        structured_paths=structured_paths,
        input_dir=paths.input_dir,
    )
    if report.ok_for_chunking:
        return
    missing = [r.stem for r in report.missing[:20]]
    extra = f" (+{len(report.missing) - 20})" if len(report.missing) > 20 else ""
    raise RegistryCoverageError(
        f"Реестр не покрывает {len(report.missing)} файл(ов). "
        f"Примеры stem: {missing}{extra}. "
        f"Запустите: python -m mr_norm.apps.main registry-check"
    )


def run_sync_marked_docs(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    paths.ensure_output_dirs()
    report = sync_marked_docs_with_input(paths.input_dir, paths.marked_docs_dir)
    out_path = args.report or (paths.reports_dir / "marked_docs_sync.json")
    atomic_write_json(out_path, report.to_dict())
    return {
        "command": "sync-marked-docs",
        "report_path": str(out_path),
        **report.to_dict(),
    }


def _resolve_knowledge_paths(
    paths: ProjectPaths,
    args: argparse.Namespace,
    *,
    output_is_index: bool = False,
) -> tuple[Path, Path, Path]:
    default_openings, default_annotations, default_index = knowledge_paths(paths)
    openings = getattr(args, "openings", None) or default_openings
    annotations = getattr(args, "annotations", None) or default_annotations
    if output_is_index:
        index_path = getattr(args, "output", None) or default_index
    else:
        index_path = default_index
    if not openings.is_absolute():
        openings = paths.root / openings
    if not annotations.is_absolute():
        annotations = paths.root / annotations
    if not index_path.is_absolute():
        index_path = paths.root / index_path
    return openings, annotations, index_path


def run_knowledge_openings(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    chunks_path = args.chunks or paths.chunks_json
    if not chunks_path.is_absolute():
        chunks_path = paths.root / chunks_path
    default_openings, _, _ = knowledge_paths(paths)
    output_path = args.output or default_openings
    if not output_path.is_absolute():
        output_path = paths.root / output_path
    payload = extract_document_openings(chunks_path, output_path=output_path)
    return {
        "command": "knowledge-openings",
        "chunks_path": str(chunks_path),
        "output_path": str(output_path),
        "documents": len(payload.get("documents") or []),
        "kind_counts": (payload.get("meta") or {}).get("kind_counts", {}),
    }


def run_knowledge_annotations(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    openings_path, default_annotations, _ = _resolve_knowledge_paths(paths, args)
    annotations_path = args.output or default_annotations
    if not annotations_path.is_absolute():
        annotations_path = paths.root / annotations_path
    result = build_document_annotations(
        openings_path,
        output_path=annotations_path,
        model=args.model,
        base_url=args.base_url,
        timeout_sec=args.timeout,
        max_input_chars=args.max_input_chars,
        limit=args.limit,
        resume=bool(args.resume),
        delay_sec=args.delay,
    )
    return {
        "command": "knowledge-annotations",
        "openings_path": str(openings_path),
        "output_path": str(annotations_path),
        "documents": result.documents_total,
        "new_calls": result.new_calls,
        "errors": result.errors,
    }


def run_knowledge_index(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    openings_path, annotations_path, index_path = _resolve_knowledge_paths(
        paths, args, output_is_index=True
    )
    bundle = assemble_knowledge_index(openings_path, annotations_path, output_path=index_path)
    return {
        "command": "knowledge-index",
        "openings_path": str(openings_path),
        "annotations_path": str(annotations_path),
        "index_path": str(index_path),
        "index_documents": len(bundle.get("documents") or []),
        "schema_version": bundle.get("schema_version"),
    }


def run_knowledge_build_cmd(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    return run_knowledge_build(
        paths,
        skip_openings=args.skip_openings,
        skip_annotations=args.skip_annotations,
        skip_index=args.skip_index,
        resume_annotations=bool(args.resume),
        limit=args.limit,
        model=args.model,
        base_url=args.base_url,
        timeout_sec=args.timeout,
        max_input_chars=args.max_input_chars,
        delay_sec=args.delay,
    )


def run_registry_check(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    report = check_registry_coverage(
        registry_path=args.registry_path or paths.normative_registry_json,
        input_dir=paths.input_dir,
    )
    out_path = args.report or (paths.reports_dir / "registry_coverage.json")
    atomic_write_json(out_path, report.to_dict())
    return {
        "command": "registry-check",
        "report_path": str(out_path),
        "ok_for_chunking": report.ok_for_chunking,
        "files_checked": report.files_checked,
        "matched_count": len(report.matched),
        "pue_canonical_count": len(report.pue_canonical),
        "missing_count": len(report.missing),
        "orphan_registry_stems_count": len(report.orphan_registry_stems),
        "duplicate_stems_count": len(report.duplicate_stems_in_registry),
        "missing_stems_sample": [r.stem for r in report.missing[:30]],
    }


def run_chunk(
    paths: ProjectPaths,
    max_chars: int = 1600,
    structured_paths: list[Path] | None = None,
    scope: str = "existing-structured",
    *,
    use_registry: bool = True,
    registry_path: Path | None = None,
    registry_missing: RegistryMissingMode = RegistryMissingMode.FAIL,
) -> dict[str, Any]:
    if use_registry and registry_missing == RegistryMissingMode.FAIL:
        _preflight_registry_coverage(
            paths,
            registry_path=registry_path,
            structured_paths=structured_paths,
        )
    start = time.perf_counter()
    builder = ChunkBuilder(
        paths,
        max_chars=max_chars,
        use_registry=use_registry,
        registry_path=registry_path,
        registry_missing=registry_missing,
    )
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
        "registry_misses": list(builder.registry_misses),
        "elapsed_sec": round(elapsed, 3),
    }


def run_build_chunks(
    paths: ProjectPaths,
    limit: int | None = None,
    max_chars: int = 1600,
    only_paths: list[Path] | None = None,
    scope: str = "full",
    per_file_timeout_sec: float = 120.0,
    *,
    use_registry: bool = True,
    registry_path: Path | None = None,
    registry_missing: RegistryMissingMode = RegistryMissingMode.FAIL,
) -> dict[str, Any]:
    start = time.perf_counter()
    ingest_start = time.perf_counter()
    ingest = run_ingest(paths, limit=limit, only_paths=only_paths, per_file_timeout_sec=per_file_timeout_sec)
    ingest_elapsed = time.perf_counter() - ingest_start
    chunk_start = time.perf_counter()
    structured_paths = [Path(item["structured_path"]) for item in ingest.get("documents", []) if item.get("structured_path")]
    chunk = run_chunk(
        paths,
        max_chars=max_chars,
        structured_paths=structured_paths,
        scope=scope,
        use_registry=use_registry,
        registry_path=registry_path,
        registry_missing=registry_missing,
    )
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


def format_ingest_rtf_console_summary(report: dict[str, Any]) -> str:
    sync = report.get("marked_docs_sync") or {}
    word = report.get("word_cleanup") or {}
    total = int(report.get("documents_total") or 0)
    ok = int(report.get("documents_processed_ok") or 0)
    skipped = int(report.get("documents_skipped_existing") or 0)
    failed = int(report.get("documents_failed") or 0)
    new_ok = max(ok - skipped, 0)
    orphans = int(sync.get("orphans_removed_count") or len(sync.get("orphans_removed") or []))

    lines = [
        "=== ingest-rtf ===",
        f"Input (RTF в каталоге): {sync.get('input_rtf_count', '?')}",
        f"Синхронизация output: удалено файлов без RTF в input — {orphans}",
        f"Уже готовы в output (пропуск Word): {skipped}",
        f"Обработано Word (новые/перезапись): {new_ok}",
        f"Ошибки чтения: {failed}",
        f"Всего в этом прогоне: {total}",
    ]
    if word.get("files_timed_out"):
        lines.append(f"Таймауты Word: {word['files_timed_out']} (лимит {word.get('per_file_timeout_sec')} с)")
    if report.get("smoke_diverse_paths"):
        lines.append(f"Smoke (--smoke-diverse-n): {len(report['smoke_diverse_paths'])} файл(ов)")
    elif report.get("limit") is not None:
        lines.append(f"Лимит --limit: {report['limit']}")

    failed_docs = [
        d
        for d in report.get("documents", [])
        if int(d.get("paragraphs") or 0) == 0 and not d.get("skipped_existing")
    ]
    if failed_docs:
        lines.append("Файлы с ошибками:")
        for doc in failed_docs[:15]:
            name = Path(str(doc.get("source_file") or "")).name or "?"
            err = str(doc.get("error") or "неизвестная ошибка").strip()
            if len(err) > 160:
                err = err[:157] + "..."
            lines.append(f"  • {name}: {err}")
        if len(failed_docs) > 15:
            lines.append(f"  … ещё {len(failed_docs) - 15} (см. report JSON)")

    lines.append(f"Время: {report.get('elapsed_sec', '?')} с")
    lines.append(f"Полный отчёт: {report.get('report_path', 'output/reports/rtf_processing_last.json')}")
    return "\n".join(lines)


def to_printable_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("command") == "ingest-rtf":
        compact = {k: v for k, v in result.items() if k != "documents"}
        failed_docs = [
            d
            for d in result.get("documents", [])
            if int(d.get("paragraphs") or 0) == 0 and not d.get("skipped_existing")
        ]
        compact["documents_failed_preview"] = failed_docs[:20]
        return compact
    if "ingest" in result and isinstance(result["ingest"], dict):
        compact = dict(result)
        ingest = dict(compact["ingest"])
        docs = ingest.pop("documents", [])
        ingest["documents_preview"] = docs[:5]
        failed_docs = [
            d for d in docs if int(d.get("paragraphs") or 0) == 0 and not d.get("skipped_existing")
        ]
        ingest["documents_failed_preview"] = failed_docs[:20]
        compact["ingest"] = ingest
        return compact
    return result


if __name__ == "__main__":
    raise SystemExit(main())
