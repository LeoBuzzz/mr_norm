from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.eval.chunk_quality import ChunkQualityReporter, save_baseline_comparison
from mr_norm.tools.chunker import ChunkBuilder, MetadataExtractionError
from mr_norm.tools.rtf_processor import RtfProcessor, RtfReadError, atomic_write_json, pick_size_diverse_rtf_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mr_norm.apps.main")
    parser.add_argument("--root", type=Path, default=None, help="Project root, defaults to current mr_norm workspace.")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest-rtf", help="Convert RTF files to marked TXT and structured JSON.")
    ingest.add_argument("--limit", type=int, default=None)
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

    sub.add_parser("quality-report", help="Build quality report for output/qdrant_chunks.json.")

    compare = sub.add_parser("compare-baseline", help="Compare output/qdrant_chunks.json with rag_norm baseline.")
    compare.add_argument("--baseline", type=Path, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = ProjectPaths.from_root(args.root)
    paths.ensure_output_dirs()

    try:
        if args.command == "ingest-rtf":
            only_paths = None
            if args.smoke_diverse_n is not None:
                only_paths = pick_size_diverse_rtf_paths(paths.input_dir, args.smoke_diverse_n)
            result = run_ingest(paths, limit=args.limit if only_paths is None else None, only_paths=only_paths)
        elif args.command == "chunk":
            result = run_chunk(paths, args.max_chars)
        elif args.command == "build-chunks":
            result = run_build_chunks(paths, args.limit, args.max_chars)
        elif args.command == "quality-report":
            result = ChunkQualityReporter(paths).report()
        elif args.command == "compare-baseline":
            result = save_baseline_comparison(paths, args.baseline)
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except (RtfReadError, MetadataExtractionError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(to_printable_result(result), ensure_ascii=False, indent=2))
    return 0


def run_ingest(paths: ProjectPaths, limit: int | None = None, only_paths: list[Path] | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    processor = RtfProcessor(paths)
    results = processor.process_all(limit=limit, only_paths=only_paths)
    report = {
        "command": "ingest-rtf",
        "documents_total": len(results),
        "documents_processed_ok": sum(1 for item in results if item.paragraphs > 0),
        "documents_failed": sum(1 for item in results if item.paragraphs == 0),
        "documents_with_read_warnings": sum(1 for item in results if item.error),
        "word_cleanup": processor.last_word_cleanup,
        "smoke_diverse_paths": [str(p) for p in only_paths] if only_paths else None,
        "elapsed_sec": round(time.perf_counter() - start, 3),
        "documents": [item.__dict__ for item in results],
    }
    atomic_write_json(paths.reports_dir / "rtf_processing_last.json", report)
    return report


def run_chunk(
    paths: ProjectPaths,
    max_chars: int = 1600,
    structured_paths: list[Path] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    builder = ChunkBuilder(paths, max_chars=max_chars)
    chunks = builder.build_all(structured_paths=structured_paths)
    report = ChunkQualityReporter(paths).report(chunks)
    return {
        "command": "chunk",
        "chunks_total": len(chunks),
        "chunks_json": str(paths.chunks_json),
        "quality_report": report.get("report_path"),
        "metadata_manifest_md": str(paths.metadata_manifest_md),
        "pue_canonical_applied_count": len(builder.manifest_pue),
        "metadata_fallback_count": len(builder.manifest_other),
        "elapsed_sec": round(time.perf_counter() - start, 3),
    }


def run_build_chunks(paths: ProjectPaths, limit: int | None = None, max_chars: int = 1600) -> dict[str, Any]:
    start = time.perf_counter()
    ingest_start = time.perf_counter()
    ingest = run_ingest(paths, limit=limit)
    ingest_elapsed = time.perf_counter() - ingest_start
    chunk_start = time.perf_counter()
    structured_paths = [Path(item["structured_path"]) for item in ingest.get("documents", []) if item.get("structured_path")]
    chunk = run_chunk(paths, max_chars=max_chars, structured_paths=structured_paths)
    chunk_elapsed = time.perf_counter() - chunk_start
    report = {
        "command": "build-chunks",
        "limit": limit,
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
