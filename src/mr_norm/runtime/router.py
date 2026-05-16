from __future__ import annotations

from mr_norm.retrieval.contracts import ToolRequest, clamp_limit
from mr_norm.retrieval.tools.point import is_point_lookup_filters
from mr_norm.runtime.contracts import PreparedQueryPlan, RuntimeRequest, ToolCallPlan
from mr_norm.runtime.profiles import ProfileConfig, get_profile_config


def _profile_allows_tool(profile: ProfileConfig, tool_name: str) -> bool:
    if tool_name == "point":
        return profile.use_point
    if tool_name == "payload":
        return profile.use_payload
    if tool_name == "vector":
        return profile.use_vector
    return False


def _plan_from_prepared(request: RuntimeRequest, profile: ProfileConfig) -> tuple[list[ToolCallPlan], list[str]]:
    plan = request.prepared_plan
    if plan is None or not plan.selected_tools:
        return [], []

    warnings: list[str] = []
    limit = clamp_limit(request.limit, default=profile.default_limit)
    filters = dict(request.filters or {})
    plans: list[ToolCallPlan] = []
    priority = 0

    for entry in plan.tool_queries:
        if entry.tool_name not in plan.selected_tools:
            continue
        if not _profile_allows_tool(profile, entry.tool_name):
            warnings.append(f"prepared tool {entry.tool_name} disabled by profile {profile.name}")
            continue
        queries = tuple(query.strip() for query in entry.queries if query.strip())
        if not queries:
            queries = (request.query.strip(),) if request.query.strip() else ()
        if not queries and entry.tool_name != "point":
            continue
        if entry.tool_name == "point" and not is_point_lookup_filters(filters) and not queries:
            continue

        plans.append(
            ToolCallPlan(
                tool_name=entry.tool_name,
                request=ToolRequest(
                    query=queries[0] if queries else request.query,
                    filters=filters,
                    limit=limit,
                    profile=profile.name,
                    trace_id=request.trace_id,
                ),
                reason=f"prepared query plan selected {entry.tool_name}",
                priority=priority,
                queries=queries,
            )
        )
        priority += 1

    return plans, warnings


def route_runtime(request: RuntimeRequest) -> tuple[list[ToolCallPlan], list[str]]:
    profile = get_profile_config(request.profile)
    limit = clamp_limit(request.limit, default=profile.default_limit)
    warnings: list[str] = []
    plans: list[ToolCallPlan] = []
    query = request.query.strip()
    filters = dict(request.filters or {})

    if request.prepared_plan is not None and request.prepared_plan.selected_tools:
        prepared_plans, prepared_warnings = _plan_from_prepared(request, profile)
        if prepared_plans:
            return prepared_plans, prepared_warnings
        warnings.extend(prepared_warnings)
        warnings.append("prepared query plan produced no runnable tools; using deterministic routing")

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
