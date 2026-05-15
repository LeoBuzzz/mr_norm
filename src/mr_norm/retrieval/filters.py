from __future__ import annotations

from typing import Any


KEYWORD_FIELDS = {
    "chunk_id",
    "doc_name",
    "filename",
    "point_identity_key",
    "point_number",
}
TEXT_FIELDS = {"heading_path_text", "text"}
SUPPORTED_FILTER_FIELDS = KEYWORD_FIELDS | TEXT_FIELDS


def doc_name_variants(value: Any) -> list[str] | str:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = [value]
    variants: list[str] = []
    for raw in raw_values:
        text = str(raw).strip()
        if not text:
            continue
        for candidate in (text, text.upper()):
            if candidate not in variants:
                variants.append(candidate)
    if len(variants) == 1:
        return variants[0]
    return variants


def expand_doc_name_filter_variants(filters: dict[str, Any] | None) -> dict[str, Any]:
    expanded = dict(filters or {})
    if expanded.get("doc_name"):
        expanded["doc_name"] = doc_name_variants(expanded["doc_name"])
    return expanded


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in (filters or {}).items():
        if key not in SUPPORTED_FILTER_FIELDS:
            continue
        if isinstance(value, str):
            prepared = value.strip()
            if prepared:
                normalized[key] = prepared
        elif isinstance(value, list):
            prepared_list = [str(item).strip() for item in value if str(item).strip()]
            if prepared_list:
                normalized[key] = prepared_list
        elif value is not None:
            normalized[key] = str(value).strip()
    return normalized


def build_filter_spec(filters: dict[str, Any] | None) -> dict[str, Any]:
    must: list[dict[str, Any]] = []
    for key, value in normalize_filters(filters).items():
        kind = "text" if key in TEXT_FIELDS else "keyword"
        if isinstance(value, list):
            must.append({"field": key, "kind": kind, "any": value})
        else:
            must.append({"field": key, "kind": kind, "value": value})
    return {"must": must}


def build_payload_filter_spec(
    query: str,
    filters: dict[str, Any] | None,
    *,
    search_fields: list[str] | None = None,
) -> dict[str, Any]:
    spec = build_filter_spec(filters)
    query_text = (query or "").strip()
    if query_text:
        fields = search_fields or ["text", "heading_path_text", "doc_name", "filename"]
        spec["should"] = [
            {"field": field, "kind": "text" if field in TEXT_FIELDS else "keyword", "value": query_text}
            for field in fields
            if field in SUPPORTED_FILTER_FIELDS
        ]
    return spec


def filter_spec_to_trace(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "must": list(spec.get("must") or []),
        "should": list(spec.get("should") or []),
    }
