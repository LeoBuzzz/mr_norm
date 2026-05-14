from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Find the workspace root from an installed or source checkout."""
    current = (start or Path(__file__)).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "planning").is_dir() and (candidate / "input").is_dir():
            return candidate
    return Path.cwd()


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    input_dir: Path
    output_dir: Path
    marked_docs_dir: Path
    reports_dir: Path
    chunks_json: Path
    baseline_chunks_json: Path
    metadata_manifest_md: Path

    @classmethod
    def from_root(cls, root: Path | None = None) -> "ProjectPaths":
        project_root = (root or find_project_root()).resolve()
        output_dir = project_root / "output"
        reports_dir = output_dir / "reports"
        return cls(
            root=project_root,
            input_dir=project_root / "input" / "All_raw_docks",
            output_dir=output_dir,
            marked_docs_dir=output_dir / "marked_docs",
            reports_dir=reports_dir,
            chunks_json=output_dir / "qdrant_chunks.json",
            baseline_chunks_json=project_root.parent / "rag_norm" / "qdrant_chunks.json",
            metadata_manifest_md=reports_dir / "metadata_fallback_manifest.md",
        )

    def ensure_output_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.marked_docs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


DEFAULT_PATHS = ProjectPaths.from_root()
