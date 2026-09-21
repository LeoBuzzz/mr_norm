from __future__ import annotations

import os
import re

_ENV_DISABLE_INTENT_ROUTING = "MR_NORM_DISABLE_INTENT_ROUTING"
_ENV_DISABLE_DOC_POINT_BOOST = "MR_NORM_DISABLE_DOC_POINT_BOOST"
_ENV_DISABLE_RRF_WEIGHTS = "MR_NORM_DISABLE_RRF_WEIGHTS"
_ENV_DISABLE_AUTO_REQUIRED_TOKENS = "MR_NORM_DISABLE_AUTO_REQUIRED_TOKENS"
_ENV_DISABLE_PROFILE_RERANKER = "MR_NORM_DISABLE_PROFILE_RERANKER"
_ENV_ENABLE_TAIL_POINT_FALLBACK = "MR_NORM_ENABLE_TAIL_POINT_FALLBACK"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def intent_routing_enabled() -> bool:
    """When disabled, planner keeps default hybrid tool order."""
    return not _env_truthy(_ENV_DISABLE_INTENT_ROUTING)


def doc_point_boost_enabled() -> bool:
    """When disabled, reranker skips doc/point reordering."""
    return not _env_truthy(_ENV_DISABLE_DOC_POINT_BOOST)


def rrf_weights_enabled() -> bool:
    """When disabled, hybrid fusion treats all tools as weight 1.0."""
    return not _env_truthy(_ENV_DISABLE_RRF_WEIGHTS)


def auto_required_tokens_enabled() -> bool:
    """When disabled, payload must-tokens stay exact-phrase only."""
    return not _env_truthy(_ENV_DISABLE_AUTO_REQUIRED_TOKENS)


def tail_point_fallback_enabled() -> bool:
    """Enable the optional fallback that searches tail point candidates."""
    return _env_truthy(_ENV_ENABLE_TAIL_POINT_FALLBACK)


def default_reranker_backend(profile: str) -> str:
    """Profile-aware reranker default. Override via MR_NORM_DISABLE_PROFILE_RERANKER."""
    if _env_truthy(_ENV_DISABLE_PROFILE_RERANKER):
        return "passthrough"
    if profile == "deep":
        return "prompt"
    if profile == "balanced":
        return "score"
    return "passthrough"


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
    "когда нужно",
    "когда должен",
    "когда должны",
    "в какой срок",
    "разрешен ли",
    "разрешён ли",
    "разрешен ли",
    "допускается ли",
    "можно ли",
)


EXPLICIT_DOC_REFERENCE_PATTERNS = (
    re.compile(r"(?:№|n[oº\.]\s*)\s*\d", re.IGNORECASE),
    re.compile(r"\b(?:пункт|п\.|подпункт|статья|ст\.|раздел|приложение)\s*[\d_.]", re.IGNORECASE),
    re.compile(r"\b(?:гост|сто)\s*[\d\-–]", re.IGNORECASE),
    re.compile(r"\b\d+\s*-\s*фз\b", re.IGNORECASE),
    re.compile(r"postanovlen|prikaz|postanov|minenergo|минэнерго|правительств", re.IGNORECASE),
    re.compile(r"согласно\s+", re.IGNORECASE),
    re.compile(r"в\s+соответствии\s+с\s+", re.IGNORECASE),
    re.compile(r"«[^»]{8,}»"),
    re.compile(r"\b(?:данный|этот|настоящий)\s+(?:приказ|постановление|документ|акт|гост)\b", re.IGNORECASE),
)


def query_has_explicit_doc_reference(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in EXPLICIT_DOC_REFERENCE_PATTERNS)


def effective_doc_confidence_threshold(query: str) -> float:
    """Natural queries (no act title/number in text) may apply doc filter at 0.50."""
    return 0.55 if query_has_explicit_doc_reference(query) else 0.50


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
    if mode == "document_lookup" and not query_has_explicit_doc_reference(original_query):
        if looks_like_requirement_query(original_query) or any(
            marker in (original_query or "").lower()
            for marker in ("кто ", "как ", "сколько", "когда ", "где ")
        ):
            return "requirement"
    return mode
