"""Project-level adapter for table-aware RTF chunking."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from mr_norm.config.paths import ProjectPaths
from mr_norm.rtf_tables.chunk import chunk_rtf, load_documents
from mr_norm.tools.chunker import backup_qdrant_chunks_json
from mr_norm.tools.rtf_processor import atomic_write_json


class RtfChunkBuilder:
    """Build ``qdrant_chunks.json`` directly from raw RTF files.

    Reading with latin-1 preserves every byte one-to-one; the parser itself
    decodes RTF hex escapes according to ``\\ansicpg``.
    """

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        registry_path: Path | None = None,
        use_registry: bool = True,
    ) -> None:
        self.paths = paths
        self.registry_path = registry_path or paths.normative_registry_json
        self.use_registry = use_registry
        self.files_processed: list[str] = []
        self.chunks_per_file: dict[str, int] = {}

    def build_all(
        self,
        rtf_paths: Iterable[Path] | None = None,
        *,
        limit: int | None = None,
        show_progress: bool = False,
    ) -> list[dict[str, Any]]:
        candidates = rtf_paths if rtf_paths is not None else self.paths.input_dir.glob("*.rtf")
        paths = sorted(path for path in candidates if not path.name.startswith("~$"))
        if limit is not None:
            paths = paths[: max(0, limit)]
        documents = load_documents(self.registry_path) if self.use_registry else []
        chunks: list[dict[str, Any]] = []
        self.files_processed = []
        self.chunks_per_file = {}
        started_at = time.perf_counter()
        total_documents = len(paths)
        for document_number, path in enumerate(paths, start=1):
            source = path.read_text(encoding="latin-1")
            file_chunks = chunk_rtf(source, path.name, documents=documents)
            chunks.extend(file_chunks)
            self.files_processed.append(path.name)
            self.chunks_per_file[path.name] = len(file_chunks)
            if show_progress:
                elapsed = time.perf_counter() - started_at
                eta_seconds = elapsed / document_number * (total_documents - document_number)
                finishes_at = datetime.now().astimezone() + timedelta(seconds=eta_seconds)
                percent = document_number / max(total_documents, 1) * 100
                print(
                    f"[chunk] documents {document_number}/{total_documents} {percent:6.2f}% | "
                    f"chunks={len(chunks)} | elapsed={_format_duration(elapsed)} | "
                    f"ETA={_format_duration(eta_seconds)} ({finishes_at:%H:%M:%S}) | {path.name}",
                    file=sys.stderr,
                    flush=True,
                )

        self.paths.ensure_output_dirs()
        backup_qdrant_chunks_json(self.paths.chunks_json)
        atomic_write_json(self.paths.chunks_json, chunks)
        return chunks


def _format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
