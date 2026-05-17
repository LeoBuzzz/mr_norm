from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths

def _default_mapping_path() -> Path:
    return ProjectPaths.from_root(None).root / "tmp" / "knowledge_catalog_mapping.json"


@dataclass(frozen=True)
class KnowledgeCatalogLink:
    knowledge_doc_id: str
    catalog_id: str
    confidence: float = 0.0
    verified: bool = False
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_doc_id": self.knowledge_doc_id,
            "catalog_id": self.catalog_id,
            "confidence": round(self.confidence, 4),
            "verified": self.verified,
            "source": self.source,
        }


def load_knowledge_catalog_mapping(path: Path | None = None) -> dict[str, KnowledgeCatalogLink]:
    mapping_path = path or _default_mapping_path()
    if not mapping_path.is_file():
        return {}

    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    links: dict[str, KnowledgeCatalogLink] = {}
    for item in payload.get("links") or []:
        knowledge_doc_id = str(item.get("knowledge_doc_id") or "").strip()
        catalog_id = str(item.get("catalog_id") or "").strip()
        if not knowledge_doc_id or not catalog_id:
            continue
        links[knowledge_doc_id] = KnowledgeCatalogLink(
            knowledge_doc_id=knowledge_doc_id,
            catalog_id=catalog_id,
            confidence=float(item.get("confidence") or 0.0),
            verified=bool(item.get("verified")),
            source=str(item.get("source") or ""),
        )
    return links


def default_mapping_path(project_paths: ProjectPaths | None = None) -> Path:
    paths = project_paths or ProjectPaths.from_root(None)
    return paths.root / "tmp" / "knowledge_catalog_mapping.json"
