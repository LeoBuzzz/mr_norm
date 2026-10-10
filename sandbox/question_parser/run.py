"""Dry-run entry: classify, check, assemble. Skills are not called."""

from __future__ import annotations

from typing import Any

from question_parser.catalog import Catalog, load_catalog
from question_parser.contracts import SkillQueryPlan, TechnicalFailure
from question_parser.draft import assemble_skill_query_plan, validate_semantic_draft
from question_parser.interpret import interpret
from question_parser.sources import DraftValidationError, build_source_manifest


def dry_run(query: str, *, context: dict[str, Any] | None = None, catalog: Catalog | None = None, draft: dict[str, Any] | None = None, complexity: str | None = None) -> SkillQueryPlan | TechnicalFailure:
    """Return a checked plan, a clarification, a deferral, or a technical error.

    Pass `draft` to check a model payload. Without it, the deterministic reader
    builds the draft. Neither path executes a skill.
    """
    catalog = catalog or load_catalog()
    try:
        if draft is None:
            complexity, draft, manifest = interpret(query, catalog, context)
        else:
            manifest = build_source_manifest(query, context)
            complexity = complexity or "complex"
        checked = validate_semantic_draft(
            draft,
            manifest,
            candidate_ids={item.doc_id for item in catalog.documents},
            term_ids={item.meaning_id for item in catalog.meanings},
        )
    except DraftValidationError as error:
        return TechnicalFailure(error.code, str(error))
    return assemble_skill_query_plan(query, complexity or "simple", checked, manifest, catalog)
