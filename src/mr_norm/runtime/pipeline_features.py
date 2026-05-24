from __future__ import annotations

import os
import re

_ENV_DISABLE_INTENT_ROUTING = "MR_NORM_DISABLE_INTENT_ROUTING"
_ENV_DISABLE_DOC_POINT_BOOST = "MR_NORM_DISABLE_DOC_POINT_BOOST"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def intent_routing_enabled() -> bool:
    """When disabled, planner keeps default hybrid tool order."""
    return not _env_truthy(_ENV_DISABLE_INTENT_ROUTING)


def doc_point_boost_enabled() -> bool:
    """When disabled, reranker skips doc/point reordering."""
    return not _env_truthy(_ENV_DISABLE_DOC_POINT_BOOST)


REQUIREMENT_QUERY_PATTERNS = (
    "какие требования",
    "какие обязательства",
    "что должно",
    "что должны",
    "какие функции",
    "какие виды",
    "какие группы",
    "какие решения",
    "какие действия",
    "какие меры",
    "какие условия",
    "какие параметры",
    "какие требования предъявляются",
    "какие требования установлены",
    "какие требования определены",
)


def looks_like_requirement_query(query: str) -> bool:
    norm = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not norm:
        return False
    return any(pattern in norm for pattern in REQUIREMENT_QUERY_PATTERNS)


def normalize_routing_question_type(question_type: str, original_query: str) -> str:
    """Map regulation_scope/document_lookup to requirement when query asks for norms."""
    mode = (question_type or "factual").strip()
    if mode in {"regulation_scope", "document_lookup"} and looks_like_requirement_query(original_query):
        return "requirement"
    return mode
