# LLM Smoke Commands

Manual checks only. Normal `pytest` stays mocked and does not call Ollama or Polza.

## Prerequisites

- Ollama running locally with models from `ollama list`.
- For Polza: set `POLZA_AI_API_KEY` or keep a local untracked `keys` file in project root.
- Qdrant collection configured as for `rag-runtime`.

## Ollama final answer smoke

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner deterministic ^
  --reranker score ^
  --final-answer prompt ^
  --llm-provider ollama ^
  --final-answer-model llama-3.3-70b-Instruct:latest
```

## Ollama planner + reranker smoke

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner prompt ^
  --reranker prompt ^
  --final-answer evidence ^
  --llm-provider ollama ^
  --planner-model qwen3:30b ^
  --reranker-model qwen3:30b
```

## Polza long-context final answer smoke

```bash
python -m mr_norm.apps.main rag-pipeline ^
  --query "заземление" ^
  --profile balanced ^
  --planner deterministic ^
  --reranker score ^
  --final-answer prompt ^
  --llm-provider polza ^
  --final-answer-model deepseek/deepseek-v4-flash
```

## Notes

- Keep `--llm-provider none` for deterministic/no-cost runs.
- Prompt backends still fall back to deterministic/evidence behavior if LLM call fails.
- Never commit `keys`; prefer `POLZA_AI_API_KEY` in environment for CI or shared machines.
