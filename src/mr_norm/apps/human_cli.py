from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mr_norm.config.indexing import IndexingConfig
from mr_norm.config.paths import ProjectPaths
from mr_norm.runtime.contracts import QueryUnderstandingResult
from mr_norm.skills.norm_lookup import NormLookupRequest, NormLookupResult, run_norm_lookup

DEFAULT_OLLAMA_FINAL_ANSWER_MODEL = "qwen3:30b"

MODE_PRESET_LABELS: dict[str, str] = {
    "deterministic": "Deterministic / no-cost (evidence summary)",
    "ollama": "Ollama LLM answer (local)",
    "polza": "Polza LLM answer (cloud)",
}

MODE_PRESET_CHOICES: dict[str, str] = {
    "1": "deterministic",
    "2": "ollama",
    "3": "polza",
}


@dataclass(frozen=True)
class HumanCliOptions:
    query: str = ""
    mode_preset: str = ""
    doc_name: str = ""
    limit: int = 10
    profile: str = "balanced"
    final_answer_model: str | None = None
    understand_query: str = ""


def apply_mode_preset(
    preset: str,
    *,
    final_answer_model: str | None = None,
) -> dict[str, Any]:
    if preset == "deterministic":
        return {
            "planner_backend": "deterministic",
            "reranker_backend": "score",
            "final_answer_backend": "evidence",
            "llm_provider": "none",
            "planner_model": None,
            "reranker_model": None,
            "final_answer_model": None,
            "understand_query_mode": "auto",
        }
    if preset == "ollama":
        return {
            "planner_backend": "deterministic",
            "reranker_backend": "score",
            "final_answer_backend": "prompt",
            "llm_provider": "ollama",
            "planner_model": None,
            "reranker_model": None,
            "final_answer_model": final_answer_model or DEFAULT_OLLAMA_FINAL_ANSWER_MODEL,
            "understand_query_mode": "llm",
        }
    if preset == "polza":
        return {
            "planner_backend": "deterministic",
            "reranker_backend": "score",
            "final_answer_backend": "prompt",
            "llm_provider": "polza",
            "planner_model": None,
            "reranker_model": None,
            "final_answer_model": final_answer_model,
            "understand_query_mode": "llm",
        }
    raise ValueError(f"unsupported mode preset: {preset}")


def resolve_understand_query_mode(options: HumanCliOptions, preset_config: dict[str, Any]) -> str:
    if options.understand_query.strip():
        return options.understand_query.strip()
    return str(preset_config.get("understand_query_mode") or "auto")


def build_norm_lookup_request(options: HumanCliOptions) -> NormLookupRequest:
    preset = options.mode_preset or "deterministic"
    preset_config = apply_mode_preset(preset, final_answer_model=options.final_answer_model)
    filters: dict[str, Any] = {}
    if options.doc_name.strip():
        filters["doc_name"] = options.doc_name.strip()
    understand_query_mode = resolve_understand_query_mode(options, preset_config)
    return NormLookupRequest(
        query=options.query.strip(),
        filters=filters,
        profile=options.profile,
        limit=options.limit,
        understand_query_mode=understand_query_mode,
        **{key: value for key, value in preset_config.items() if key != "understand_query_mode"},
    )


def render_mode_menu() -> str:
    lines = [
        "MR Norm — поиск по нормативным документам",
        "",
        "Выберите режим работы:",
        f"  1 — {MODE_PRESET_LABELS['deterministic']}",
        f"  2 — {MODE_PRESET_LABELS['ollama']}",
        f"  3 — {MODE_PRESET_LABELS['polza']}",
        "",
    ]
    return "\n".join(lines)


def prompt_for_mode_choice(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> str:
    print_fn(render_mode_menu())
    while True:
        choice = input_fn("Режим [1]: ").strip() or "1"
        preset = MODE_PRESET_CHOICES.get(choice)
        if preset:
            return preset
        print_fn("Введите 1, 2 или 3.")


def collect_interactive_options(
    base: HumanCliOptions | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> HumanCliOptions:
    current = base or HumanCliOptions()
    mode_preset = current.mode_preset or prompt_for_mode_choice(input_fn=input_fn, print_fn=print_fn)

    query = current.query.strip()
    if not query:
        while not query:
            query = input_fn("Вопрос: ").strip()
            if not query:
                print_fn("Вопрос не может быть пустым.")

    doc_name = current.doc_name
    if not doc_name.strip():
        doc_name = input_fn("Фильтр doc_name (Enter — без фильтра): ").strip()

    limit = current.limit
    limit_text = input_fn(f"Лимит источников [{limit}]: ").strip()
    if limit_text:
        limit = max(1, int(limit_text))

    profile = current.profile
    profile_text = input_fn(f"Профиль fast|balanced|deep [{profile}]: ").strip()
    if profile_text:
        profile = profile_text

    return HumanCliOptions(
        query=query,
        mode_preset=mode_preset,
        doc_name=doc_name,
        limit=limit,
        profile=profile,
        final_answer_model=current.final_answer_model,
    )


def render_run_summary(request: NormLookupRequest) -> str:
    doc_filter = request.filters.get("doc_name", "")
    filter_line = f"фильтр doc_name: {doc_filter}" if doc_filter else "фильтр: нет"
    model_suffix = ""
    if request.final_answer_model:
        model_suffix = f", final_answer_model={request.final_answer_model}"
    return (
        "Параметры запроса:\n"
        f"  профиль: {request.profile}\n"
        f"  режим: {request.llm_provider} / final_answer={request.final_answer_backend}\n"
        f"  planner={request.planner_backend}, reranker={request.reranker_backend}\n"
        f"  understand_query={request.understand_query_mode}\n"
        f"  limit={request.limit}, {filter_line}{model_suffix}"
    )


def render_query_understanding(understanding: QueryUnderstandingResult) -> str:
    lines = [
        "",
        "ПОНИМАНИЕ ЗАПРОСА",
        "-" * 72,
        f"  исходный вопрос: {understanding.original_query}",
        f"  поисковый запрос: {understanding.search_query}",
    ]
    if understanding.document_hints:
        lines.append(f"  подсказки по документу: {', '.join(understanding.document_hints)}")
    if understanding.resolved_doc_names:
        lines.append(
            f"  распознанный документ: {understanding.resolved_doc_names[0]} "
            f"(confidence={understanding.confidence:.2f})"
        )
    else:
        lines.append("  распознанный документ: не выбран (поиск без фильтра doc_name)")
    if understanding.ambiguous:
        lines.append("  статус: неоднозначно, фильтр doc_name не применён")
    if understanding.point_number_hints:
        lines.append(f"  пункт: {', '.join(understanding.point_number_hints)}")
    if understanding.tool_hints:
        lines.append(f"  инструменты: {', '.join(understanding.tool_hints)}")
    top_candidates = understanding.candidates[:3]
    if top_candidates:
        lines.append("  кандидаты каталога:")
        for candidate in top_candidates:
            lines.append(
                f"    - {candidate.get('doc_name', '')} "
                f"(score={candidate.get('score', 0)}, id={candidate.get('catalog_id', '')})"
            )
    return "\n".join(lines)


def render_human_norm_lookup_result(result: NormLookupResult) -> str:
    lines = [
        "",
        "=" * 72,
        "ОТВЕТ",
        "=" * 72,
        result.answer.strip() or "_Пустой ответ_",
        "",
        "ИСТОЧНИКИ",
        "-" * 72,
    ]
    if not result.citations:
        lines.append("  (нет валидных цитат)")
    else:
        for index, citation in enumerate(result.citations, start=1):
            doc = citation.doc_name or "—"
            point = citation.point_number or "—"
            lines.append(f"  {index}. п. {point} — {doc}")
            lines.append(f"     chunk_id: {citation.chunk_id}")

    runtime = result.pipeline.runtime
    trace = result.trace
    lines.extend(
        [
            "",
            "ОБРАБОТКА",
            "-" * 72,
            f"  найдено фрагментов: {runtime.metrics.items_returned}",
            f"  после rerank: {len(result.evidence)}",
            f"  инструменты: {', '.join(trace.selected_tools) or '—'}",
            f"  fusion: {trace.runtime_fusion or 'none'}",
            f"  backends: planner={trace.planner_backend}, "
            f"reranker={trace.reranker_backend}, final_answer={trace.final_answer_backend}",
            f"  предупреждений: {len(result.warnings)}",
        ]
    )
    if result.warnings:
        lines.extend(["", "WARNINGS", "-" * 72])
        for warning in result.warnings:
            lines.append(f"  - {warning}")
    lines.append("")
    return "\n".join(lines)


def run_human_norm_lookup(
    options: HumanCliOptions,
    config: IndexingConfig | None,
    *,
    keys_path: Path | None = None,
    project_paths: ProjectPaths | None = None,
    print_fn: Callable[[str], None] = print,
) -> NormLookupResult:
    request = build_norm_lookup_request(options)
    if not request.query:
        raise ValueError("query is required")
    print_fn(render_run_summary(request))
    print_fn("Понимание запроса и поиск...")
    result = run_norm_lookup(
        request,
        config,
        keys_path=keys_path,
        project_paths=project_paths,
    )
    if result.understanding is not None:
        print_fn(render_query_understanding(result.understanding))
    print_fn(render_human_norm_lookup_result(result))
    return result
