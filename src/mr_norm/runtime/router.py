from __future__ import annotations

from mr_norm.retrieval.contracts import ToolRequest, clamp_limit
from mr_norm.retrieval.tools.point import is_point_lookup_filters
from mr_norm.runtime.contracts import RuntimeRequest, ToolCallPlan
from mr_norm.runtime.profiles import ProfileConfig, get_profile_config


def route_runtime(request: RuntimeRequest) -> tuple[list[ToolCallPlan], list[str]]:
    profile = get_profile_config(request.profile)
    limit = clamp_limit(request.limit, default=profile.default_limit)
    warnings: list[str] = []
    plans: list[ToolCallPlan] = []
    query = request.query.strip()
    filters = dict(request.filters or {})

    base_request = ToolRequest(
        query=request.query,
        filters=filters,
        limit=limit,
        profile=profile.name,
        trace_id=request.trace_id,
    )

    has_stable_point = bool(filters.get("chunk_id") or filters.get("point_identity_key"))
    has_point_scope = has_stable_point or is_point_lookup_filters(filters)
    has_query = bool(query)

    if not has_query and not has_point_scope:
        warnings.append("runtime requires a non-empty query or stable point filters")
        return [], warnings

    priority = 0

    if profile.use_point and has_point_scope:
        plans.append(
            ToolCallPlan(
                tool_name="point",
                request=base_request,
                reason="stable point filters present",
                priority=priority,
            )
        )
        priority += 1

    if profile.use_payload and (has_query or has_point_scope):
        plans.append(
            ToolCallPlan(
                tool_name="payload",
                request=base_request,
                reason="text or scoped payload lookup",
                priority=priority,
            )
        )
        priority += 1

    vector_allowed = profile.use_vector and (has_query or not profile.vector_requires_query)
    if vector_allowed and has_query:
        plans.append(
            ToolCallPlan(
                tool_name="vector",
                request=base_request,
                reason="semantic vector lookup for non-empty query",
                priority=priority,
            )
        )
        priority += 1

    if not plans:
        warnings.append("router produced an empty tool plan for the given request and profile")

    return plans, warnings
