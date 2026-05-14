from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "mr_chunks_v1"


@dataclass
class ParagraphRecord:
    paragraph_index: int
    text: str
    outline_level: int = 10
    style_name: str = ""
    is_heading: bool = False
    heading_level: int | None = None
    point_number: str = ""
    char_start: int = 0
    char_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredDocument:
    source_file: str
    filename: str
    paragraphs: list[ParagraphRecord] = field(default_factory=list)
    read_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "filename": self.filename,
            "read_error": self.read_error,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructuredDocument":
        return cls(
            source_file=str(data.get("source_file") or ""),
            filename=str(data.get("filename") or ""),
            read_error=str(data.get("read_error") or ""),
            paragraphs=[
                ParagraphRecord(
                    paragraph_index=int(p.get("paragraph_index", i)),
                    text=str(p.get("text") or ""),
                    outline_level=int(p.get("outline_level", 10)),
                    style_name=str(p.get("style_name") or ""),
                    is_heading=bool(p.get("is_heading")),
                    heading_level=p.get("heading_level"),
                    point_number=str(p.get("point_number") or ""),
                    char_start=int(p.get("char_start", 0)),
                    char_end=int(p.get("char_end", 0)),
                )
                for i, p in enumerate(data.get("paragraphs") or [])
            ],
        )

    @classmethod
    def from_json_path(cls, path: Path) -> "StructuredDocument":
        import json

        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
