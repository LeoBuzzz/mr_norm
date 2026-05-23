from __future__ import annotations

from mr_norm.config.paths import ProjectPaths, find_project_root
from mr_norm.tools.chunker import ChunkBuilder, resolve_document_id
from mr_norm.tools.rtf_processor import make_paragraph
from mr_norm.tools.schema import StructuredDocument


def test_resolve_document_id_from_registry_key() -> None:
    doc_id = resolve_document_id({"registry_key": "pr_548_12072018", "doc_name": "Краткое"})
    assert doc_id == resolve_document_id({"registry_key": "pr_548_12072018", "doc_name": "Другое имя"})


def test_pue_chapters_share_doc_id() -> None:
    paths = ProjectPaths.from_root(find_project_root())
    builder = ChunkBuilder(paths, max_chars=1600, use_registry=True)

    def pue_doc(filename: str) -> StructuredDocument:
        return StructuredDocument(
            source_file="pue.rtf",
            filename=filename,
            paragraphs=[
                make_paragraph(0, "ПРАВИЛА УСТРОЙСТВА ЭЛЕКТРОУСТАНОВОК", outline_level=1),
                make_paragraph(1, "1.1.1. Требование заземления."),
            ],
        )

    id_a = builder.build_document_chunks(pue_doc("ПУЭ глава 1.1.txt"))[0]["payload"]["doc_id"]
    id_b = builder.build_document_chunks(pue_doc("ПУЭ глава 2.5 другая.txt"))[0]["payload"]["doc_id"]
    assert id_a == id_b
    assert id_a.startswith("doc_")
