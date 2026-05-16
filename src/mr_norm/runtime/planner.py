from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mr_norm.runtime.contracts import PlannerPlan, RuntimeRequest, RuntimeResult
from mr_norm.runtime.prompts import load_prompt_pack_by_role
from mr_norm.runtime.router import route_runtime

ALLOWED_RUNTIME_TOOLS = frozenset({"point", "payload", "vector"})
PlannerProvider = Callable[[RuntimeRequest, RuntimeResult | None, dict[str, Any]], Mapping[str, Any]]


class Planner(Protocol):
    backend_name: str

    def plan(self, request: RuntimeRequest, runtime: RuntimeResult | None = None) -> PlannerPlan: ...


class DeterministicPlanner:
    backend_name = "deterministic"

    def plan(self, request: RuntimeRequest, runtime: RuntimeResult | None = None) -> PlannerPlan:
        tool_plan, warnings = route_runtime(request)
        selected_tools = [step.tool_name for step in tool_plan]
        routing_reasons = [f"{step.tool_name}: {step.reason}" for step in tool_plan]
        if runtime is not None:
            runtime_tools = list(runtime.trace.selected_tools)
            if runtime_tools and runtime_tools != selected_tools:
                warnings = list(warnings) + [
                    "planner tools differ from runtime trace: "
                    f"planner={selected_tools}, runtime={runtime_tools}"
                ]
        return PlannerPlan(
            selected_tools=selected_tools,
            routing_reasons=routing_reasons,
            filter_hints=dict(request.filters),
            warnings=list(warnings),
        )


def _parse_planner_payload(payload: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    warnings: list[str] = []
    raw_tools = payload.get("selected_tools")
    if not isinstance(raw_tools, list):
        raise ValueError("selected_tools must be a list")
    selected_tools: list[str] = []
    for tool_name in raw_tools:
        name = str(tool_name).strip()
        if name not in ALLOWED_RUNTIME_TOOLS:
            warnings.append(f"planner ignored unknown tool: {name!r}")
            continue
        if name not in selected_tools:
            selected_tools.append(name)

    raw_reasons = payload.get("routing_reasons")
    if not isinstance(raw_reasons, list):
        raise ValueError("routing_reasons must be a list")
    routing_reasons = [str(reason) for reason in raw_reasons]

    return selected_tools, routing_reasons, warnings


class PromptPackPlanner:
    backend_name = "prompt"

    def __init__(self, *, provider: PlannerProvider | None = None) -> None:
        self._pack = load_prompt_pack_by_role("planner")
        self._provider = provider

    def plan(self, request: RuntimeRequest, runtime: RuntimeResult | None = None) -> PlannerPlan:
        if self._provider is None:
            fallback = DeterministicPlanner().plan(request, runtime)
            return PlannerPlan(
                selected_tools=fallback.selected_tools,
                routing_reasons=fallback.routing_reasons,
                filter_hints=fallback.filter_hints,
                warnings=fallback.warnings
                + ["prompt planner provider not configured; used deterministic routing"],
            )

        try:
            payload = self._provider(request, runtime, self._pack)
            selected_tools, routing_reasons, warnings = _parse_planner_payload(payload)
        except Exception as exc:
            fallback = DeterministicPlanner().plan(request, runtime)
            return PlannerPlan(
                selected_tools=fallback.selected_tools,
                routing_reasons=fallback.routing_reasons,
                filter_hints=fallback.filter_hints,
                warnings=fallback.warnings + [f"prompt planner failed: {type(exc).__name__}: {exc}"],
            )

        if not selected_tools:
            fallback = DeterministicPlanner().plan(request, runtime)
            return PlannerPlan(
                selected_tools=fallback.selected_tools,
                routing_reasons=fallback.routing_reasons,
                filter_hints=fallback.filter_hints,
                warnings=warnings + ["prompt planner returned no valid tools; used deterministic routing"],
            )

        return PlannerPlan(
            selected_tools=selected_tools,
            routing_reasons=routing_reasons,
            filter_hints=dict(request.filters),
            warnings=warnings,
        )


def build_planner(backend: str, *, provider: PlannerProvider | None = None) -> Planner:
    if backend == "deterministic":
        return DeterministicPlanner()
    if backend == "prompt":
        return PromptPackPlanner(provider=provider)
    raise ValueError(f"unsupported planner backend: {backend}")
