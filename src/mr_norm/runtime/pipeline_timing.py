from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class PipelineStageTimings:
    gost_prefetch_sec: float = 0.0
    query_planning_sec: float = 0.0
    retrieval_sec: float = 0.0
    planner_sec: float = 0.0
    rerank_sec: float = 0.0
    final_evidence_select_sec: float = 0.0
    final_answer_sec: float = 0.0
    retry_sec: float = 0.0
    gost_merge_answer_sec: float = 0.0
    norm_lookup_sec: float = 0.0
    answer_judge_sec: float = 0.0
    evidence_judge_sec: float = 0.0
    total_sec: float = 0.0

    def merge(self, other: PipelineStageTimings) -> PipelineStageTimings:
        merged = PipelineStageTimings()
        for field in fields(PipelineStageTimings):
            left = float(getattr(self, field.name) or 0.0)
            right = float(getattr(other, field.name) or 0.0)
            if field.name == "total_sec":
                setattr(merged, field.name, max(left, right) or left + right)
            else:
                setattr(merged, field.name, left + right)
        return merged

    def with_total(self) -> PipelineStageTimings:
        payload = self.to_dict()
        payload["total_sec"] = round(
            sum(
                value
                for key, value in payload.items()
                if key != "total_sec"
            ),
            3,
        )
        return PipelineStageTimings(**payload)

    def to_dict(self) -> dict[str, float]:
        return {field.name: round(float(getattr(self, field.name) or 0.0), 3) for field in fields(self)}


def tool_timings_from_runtime(tool_results: dict[str, object]) -> dict[str, float]:
    timings: dict[str, float] = {}
    for tool_name, result in (tool_results or {}).items():
        metrics = getattr(result, "metrics", None)
        elapsed = getattr(metrics, "elapsed_sec", None) if metrics is not None else None
        if elapsed is None and isinstance(result, dict):
            elapsed = ((result.get("metrics") or {}).get("elapsed_sec"))
        if elapsed is not None:
            timings[str(tool_name)] = round(float(elapsed), 3)
    return timings
