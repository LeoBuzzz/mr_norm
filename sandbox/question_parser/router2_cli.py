"""Interactive terminal entry point for router2."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import replace
from datetime import datetime
from pathlib import Path

# Some user environments contain an optional chardet version newer than the
# range advertised by requests.  Router2 does not use chardet directly; keep
# that harmless environment warning from obscuring the actual answer.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* doesn't match a supported version!",
    module=r"requests(?:\..*)?",
)

ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "src", ROOT / "sandbox"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mr_norm.config.indexing import IndexingConfig
from question_parser.router2_runtime import (
    DEFAULT_ANSWER_FALLBACK_MODELS,
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ROUTER_FALLBACK_MODELS,
    DEFAULT_ROUTER_MODEL,
    run_router2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Router2: question parser, Qdrant skills and final LLM")
    parser.add_argument("--query", help="Run one question and exit")
    parser.add_argument("--router-model", default=DEFAULT_ROUTER_MODEL)
    parser.add_argument("--answer-model", default=DEFAULT_ANSWER_MODEL)
    parser.add_argument("--router-fallback-models", default=",".join(DEFAULT_ROUTER_FALLBACK_MODELS))
    parser.add_argument("--answer-fallback-models", default=",".join(DEFAULT_ANSWER_FALLBACK_MODELS))
    parser.add_argument("--collection", help="Override Qdrant collection")
    parser.add_argument("--no-trace", action="store_true", help="Do not write a JSON trace")
    return parser


def _save_trace(payload: dict) -> Path:
    trace_dir = ROOT / "tmp" / "router2_traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    destination = trace_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = IndexingConfig.from_env()
    if args.collection:
        config = replace(config, collection_name=args.collection)
    pending = None
    pending_question = ""
    router_fallbacks = tuple(model.strip() for model in args.router_fallback_models.split(",") if model.strip())
    answer_fallbacks = tuple(model.strip() for model in args.answer_fallback_models.split(",") if model.strip())
    print(f"router2 | Qdrant: {config.collection_name}")
    print(f"router: {args.router_model} -> {' -> '.join(router_fallbacks) or 'без резерва'}")
    print(f"answer: {args.answer_model} -> {' -> '.join(answer_fallbacks) or 'без резерва'}")
    print("Введите вопрос; new — новый вопрос, exit — выход.")
    while True:
        try:
            user_text = args.query if args.query is not None else input("router2> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if user_text.casefold() in {"exit", "quit", "выход"}:
            return 0
        if user_text.casefold() == "new":
            pending = None
            pending_question = ""
            print("Контекст уточнения очищен.")
            if args.query is not None:
                return 0
            continue
        if not user_text:
            if args.query is not None:
                return 2
            continue
        try:
            if pending is not None:
                options = pending["clarification"]["option_ids"]
                chosen = options[int(user_text) - 1] if user_text.isdigit() and 1 <= int(user_text) <= len(options) else user_text
                result = run_router2(
                    pending_question, router_model=args.router_model, answer_model=args.answer_model,
                    router_fallback_models=router_fallbacks, answer_fallback_models=answer_fallbacks,
                    config=config, selected_option=chosen, prior_plan=pending,
                )
                pending = None
            else:
                result = run_router2(
                    user_text, router_model=args.router_model, answer_model=args.answer_model,
                    router_fallback_models=router_fallbacks, answer_fallback_models=answer_fallbacks,
                    config=config,
                )
            if result.clarification:
                pending = result.plan
                pending_question = result.question
                print(result.clarification.get("question") or "Уточните значение:")
                for index, option in enumerate(result.clarification.get("option_ids") or [], 1):
                    print(f"  {index}. {option}")
            else:
                print(result.answer)
                for citation in result.citations:
                    print(f"  Источник: {citation.get('doc_name') or 'документ'}, п. {citation.get('point_number') or '—'}")
                for warning in result.warnings:
                    print(f"  Примечание: {warning}")
            if not args.no_trace:
                print(f"Трасса: {_save_trace(result.to_dict())}")
        except Exception as exc:
            print(f"Ошибка router2: {type(exc).__name__}: {exc}", file=sys.stderr)
            if args.query is not None:
                return 1
        if args.query is not None:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
