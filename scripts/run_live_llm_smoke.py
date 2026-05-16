#!/usr/bin/env python
"""Live LLM smoke runner (Ollama + Polza). Not part of normal pytest."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.runtime.contracts import RuntimeRequest
from mr_norm.runtime.final_answer import PromptPackFinalAnswer
from mr_norm.runtime.llm_clients import (
    LLMRequest,
    OllamaChatClient,
    PolzaChatClient,
    load_polza_api_key,
    parse_json_object,
)
from mr_norm.runtime.llm_profiles import format_role_model_chain, resolve_role_models, resolve_role_profile
from mr_norm.runtime.llm_providers import (
    build_final_answer_llm_provider,
    build_planner_llm_provider,
)
from mr_norm.runtime.planner import PromptPackPlanner
from mr_norm.runtime.prompts import load_prompt_pack_by_role


def safe_print(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))


def section(title: str) -> None:
    safe_print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def ok(label: str, detail: str = "") -> None:
    safe_print(f"[PASS] {label}" + (f" — {detail}" if detail else ""))


def fail(label: str, exc: BaseException) -> None:
    safe_print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")


def run_case(name: str, fn) -> bool:
    started = time.perf_counter()
    try:
        fn()
        ok(name, f"{time.perf_counter() - started:.1f}s")
        return True
    except Exception as exc:
        fail(name, exc)
        return False


def print_model_matrix() -> None:
    section("Model chains (primary -> fallback)")
    for provider in ("ollama", "polza"):
        for role in ("planner", "reranker", "final_answer"):
            safe_print(f"  {provider} {role}: {format_role_model_chain(provider, role)}")


def test_ollama_client_fast() -> None:
    model = resolve_role_models("ollama", "planner")[-1]
    client = OllamaChatClient(model=model, timeout_sec=180.0)
    response = client.chat(
        LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": 'Return only JSON: {"status":"ok","provider":"ollama"}',
                }
            ],
            model=model,
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
    )
    payload = parse_json_object(response.content)
    assert payload.get("status") == "ok"


def test_polza_client() -> None:
    models = resolve_role_models("polza", "final_answer")
    client = PolzaChatClient(model=models[0], timeout_sec=120.0)
    response = client.chat(
        LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": 'Return only JSON: {"status":"ok","provider":"polza"}',
                }
            ],
            model=models[0],
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
    )
    payload = parse_json_object(response.content)
    assert payload.get("status") == "ok"


def test_ollama_planner_provider_with_fallback_chain() -> None:
    profile = resolve_role_profile("ollama", "planner")
    provider = build_planner_llm_provider(
        "ollama",
        resolve_role_models("ollama", "planner"),
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
    )
    pack = load_prompt_pack_by_role("planner")
    plan = PromptPackPlanner(provider=provider).plan(
        RuntimeRequest(query="заземление", profile="balanced", limit=5),
        None,
    )
    assert plan.selected_tools
    safe_print("  chain: " + format_role_model_chain("ollama", "planner"))
    safe_print("  planner tools: " + ", ".join(plan.selected_tools))


def test_polza_final_answer_provider_with_fallback_chain() -> None:
    profile = resolve_role_profile("polza", "final_answer")
    provider = build_final_answer_llm_provider(
        "polza",
        resolve_role_models("polza", "final_answer"),
        temperature=profile.temperature,
        max_tokens=min(profile.max_tokens, 1024),
        keys_path=ROOT / "keys",
    )
    evidence = [
        RetrievedItem(
            chunk_id="chunk_smoke_1",
            doc_name="ПУЭ",
            point_number="1.7.1",
            text="Требования к заземлению электроустановок.",
            source_tool="payload",
        )
    ]
    result = PromptPackFinalAnswer(provider=provider).answer(
        RuntimeRequest(query="заземление", limit=1),
        evidence,
    )
    assert result.answer.strip()
    safe_print("  chain: " + format_role_model_chain("polza", "final_answer"))
    safe_print("  answer preview: " + result.answer[:200].replace("\n", " "))


def run_cli_pipeline(args: list[str]) -> None:
    cmd = [sys.executable, "-m", "mr_norm.apps.main", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"exit {proc.returncode}\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )
    payload = json.loads(proc.stdout)
    final = payload.get("final_answer") or {}
    warnings = payload.get("warnings") or []
    safe_print("  items: " + str(len((payload.get("runtime") or {}).get("items") or [])))
    safe_print("  answer preview: " + str(final.get("answer", ""))[:200].replace("\n", " "))
    safe_print("  citations: " + str(len(final.get("citations") or [])))
    if warnings:
        safe_print("  warnings: " + repr(warnings[:3]))


def main() -> int:
    safe_print("Live LLM smoke - mr_norm")
    print_model_matrix()

    try:
        load_polza_api_key(keys_path=ROOT / "keys")
        ok("Polza API key resolved")
    except Exception as exc:
        fail("Polza API key resolved", exc)
        return 1

    results: list[bool] = []
    results.append(run_case("Ollama client (planner fallback model JSON)", test_ollama_client_fast))
    results.append(run_case("Polza client (final_answer primary JSON)", test_polza_client))
    results.append(
        run_case("Ollama planner provider (default chain)", test_ollama_planner_provider_with_fallback_chain)
    )
    results.append(
        run_case("Polza final_answer provider (default chain)", test_polza_final_answer_provider_with_fallback_chain)
    )

    base_cli = [
        "--root",
        str(ROOT),
        "rag-pipeline",
        "--collection-name",
        "mr_norm_docs_bge_m3",
        "--query",
        "заземление",
        "--profile",
        "balanced",
        "--limit",
        "3",
    ]

    results.append(
        run_case(
            "CLI rag-pipeline Ollama final (fast: qwen3:30b only)",
            lambda: run_cli_pipeline(
                base_cli
                + [
                    "--planner",
                    "deterministic",
                    "--reranker",
                    "score",
                    "--final-answer",
                    "prompt",
                    "--llm-provider",
                    "ollama",
                    "--final-answer-model",
                    "qwen3:30b",
                ]
            ),
        )
    )
    results.append(
        run_case(
            "CLI rag-pipeline Polza final (default chain)",
            lambda: run_cli_pipeline(
                base_cli
                + [
                    "--planner",
                    "deterministic",
                    "--reranker",
                    "score",
                    "--final-answer",
                    "prompt",
                    "--llm-provider",
                    "polza",
                ]
            ),
        )
    )
    results.append(
        run_case(
            "CLI rag-pipeline Ollama planner+reranker (default chain)",
            lambda: run_cli_pipeline(
                base_cli
                + [
                    "--planner",
                    "prompt",
                    "--reranker",
                    "prompt",
                    "--final-answer",
                    "evidence",
                    "--llm-provider",
                    "ollama",
                ]
            ),
        )
    )

    passed = sum(results)
    total = len(results)
    section(f"Summary: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
