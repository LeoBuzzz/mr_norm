# LLM Smoke Commands

Manual checks only. Normal `pytest` stays mocked and does not call Ollama or Polza.

## Model defaults and fallbacks

When `--planner-model`, `--reranker-model`, or `--final-answer-model` are omitted, the runtime tries the primary model first and then the fallback if the call fails.

| Role | Ollama primary | Ollama fallback | Polza primary | Polza fallback |
|------|----------------|-----------------|---------------|----------------|
| planner | `qwen3:30b` | `qwen3:8b` | `qwen/qwen3.5-flash-02-23` | `qwen/qwen3.6-flash` |
| reranker | `qwen3:30b` | `qwen3:8b` | `qwen/qwen3.5-flash-02-23` | `google/gemini-3.1-flash-lite` |
| final_answer | `llama-3.3-70b-Instruct:latest` | `qwen3:30b` | `deepseek/deepseek-v4-flash` | `qwen/qwen3.5-flash-02-23` |

Explicit `--*-model` disables the fallback chain and uses only the given model.

Premium manual override (not a default fallback): `anthropic/claude-sonnet-4.6` via `--final-answer-model`.

## Prerequisites

- Ollama running locally with models from `ollama list`.
- For Polza: set `POLZA_AI_API_KEY` or keep a local untracked `keys` file in project root.
- Qdrant collection configured as for `rag-runtime`.

## Automated live smoke runner

Fast path without waiting for local Llama 70B:

```bash
python scripts/run_live_llm_smoke.py
```

The script uses profile defaults with automatic fallback. For Ollama final answer it uses `qwen3:30b` explicitly to avoid a long Llama-only smoke run.

## Ollama final answer (profile defaults, may try Llama first)

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner deterministic ^
  --reranker score ^
  --final-answer prompt ^
  --llm-provider ollama
```

## Ollama final answer (fast smoke, skips Llama)

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner deterministic ^
  --reranker score ^
  --final-answer prompt ^
  --llm-provider ollama ^
  --final-answer-model qwen3:30b
```

## Ollama planner + reranker (defaults with fallback)

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner prompt ^
  --reranker prompt ^
  --final-answer evidence ^
  --llm-provider ollama
```

## Polza final answer (defaults with fallback)

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner deterministic ^
  --reranker score ^
  --final-answer prompt ^
  --llm-provider polza
```

## Notes

- Keep `--llm-provider none` for deterministic/no-cost runs.
- Prompt backends still fall back to deterministic/evidence behavior if all LLM models fail.
- Never commit `keys`; prefer `POLZA_AI_API_KEY` in environment for CI or shared machines.
