# Инструкция по эксплуатации MR Norm

Короткая памятка по запуску текущей RAG-системы: deterministic retrieval, full pipeline, batch-оценка, live LLM-режимы и программный слой `norm_lookup`.

## 1. Предпосылки

- Запускать команды из корня проекта.
- Qdrant должен быть доступен и содержать актуальную коллекцию.
- Обычные тесты и smoke-команды по умолчанию не требуют платных LLM-вызовов.
- Для Polza используйте `POLZA_AI_API_KEY` или локальный файл `keys` в корне проекта. Не коммитьте `keys`.

Базовая проверка:

```powershell
python -m pytest
```

Голден-проверки:

```powershell
python -m pytest tests/test_golden_quality_gates.py tests/test_runtime_tool_runner.py::test_run_runtime_batch_golden_questions_fixture tests/test_skills_norm_lookup.py::test_norm_lookup_golden_fixture_shape -q
```

## 2. Индексация и проверка индекса

Если корпус изменился, типовой путь подготовки такой:

```powershell
python -m mr_norm.apps.main ingest-rtf
python -m mr_norm.apps.main build-chunks
python -m mr_norm.apps.main index-build
python -m mr_norm.apps.main index-verify
```

Проверка payload indexes:

```powershell
python -m mr_norm.apps.main index-schema-verify
```

## 3. Deterministic runtime

`rag-runtime` запускает deterministic retrieval без LLM.

```powershell
python -m mr_norm.apps.main rag-runtime `
  --query "требования к заземлению" `
  --profile balanced `
  --limit 5 `
  --doc-name "Правила устройства электроустановок" `
  --save-report
```

Batch-режим использует `tests/fixtures/retrieval_questions.json`, если `--questions` не указан:

```powershell
python -m mr_norm.apps.main rag-runtime-batch `
  --profile balanced `
  --limit 5 `
  --save-report
```

## 4. Full pipeline

`rag-pipeline` добавляет planner, reranker и final answer поверх runtime. Без LLM:

```powershell
python -m mr_norm.apps.main rag-pipeline `
  --query "требования к заземлению" `
  --profile balanced `
  --planner deterministic `
  --reranker score `
  --final-answer evidence `
  --llm-provider none `
  --save-report
```

Batch-оценка full pipeline:

```powershell
python -m mr_norm.apps.main rag-pipeline-batch `
  --profile balanced `
  --limit 5 `
  --planner deterministic `
  --reranker score `
  --final-answer evidence `
  --llm-provider none `
  --save-report
```

Отчёты сохраняются в `output/reports` в JSON и Markdown. Для pipeline batch в отчёте есть метрики: returned items, citations, warning count, fallback count, backend trace и empty answer rate.

## 5. Live LLM-режимы

LLM включается только при `--llm-provider ollama` или `--llm-provider polza` и prompt-бэкендах.

Ollama final answer:

```powershell
python -m mr_norm.apps.main rag-pipeline `
  --query "заземление" `
  --profile balanced `
  --planner deterministic `
  --reranker score `
  --final-answer prompt `
  --llm-provider ollama `
  --final-answer-model qwen3:30b
```

Polza final answer:

```powershell
python -m mr_norm.apps.main rag-pipeline `
  --query "заземление" `
  --profile balanced `
  --planner deterministic `
  --reranker score `
  --final-answer prompt `
  --llm-provider polza
```

Planner и reranker через LLM:

```powershell
python -m mr_norm.apps.main rag-pipeline `
  --query "заземление" `
  --profile balanced `
  --planner prompt `
  --reranker prompt `
  --final-answer evidence `
  --llm-provider ollama
```

Если `--planner-model`, `--reranker-model` или `--final-answer-model` не указаны, используется цепочка primary -> fallback из профилей. Явно указанный model id отключает цепочку для этой роли.

Live smoke:

```powershell
python scripts/run_live_llm_smoke.py
```

## 6. Человеческий CLI (norm-lookup)

Для работы «глазами человека», без полного JSON в консоли:

```powershell
python -m mr_norm.apps.main norm-lookup
```

Перед поиском (режимы `auto` / `llm`) система пытается распознать целевой документ по каталогу корпуса (`output/document_catalog.json`), а не по произвольному сокращению вроде «ПУЭ». При низкой уверенности фильтр `doc_name` **не применяется**, чтобы не сузить поиск неверно.

При запуске без аргументов CLI предложит:

1. **Deterministic / no-cost** — evidence summary, без LLM.
2. **Ollama** — LLM-ответ локально (`qwen3:30b` по умолчанию для final answer).
3. **Polza** — LLM-ответ через облако.

Затем запросит вопрос, опциональный фильтр `doc_name` и `limit`. На выходе:

- короткие параметры запроса;
- блок **ОТВЕТ**;
- список **ИСТОЧНИКОВ** (пункт, документ, chunk_id);
- краткая **ОБРАБОТКА** (tools, fusion, backends, warnings).

One-shot без меню:

```powershell
python -m mr_norm.apps.main norm-lookup `
  --query "расскажи об оперативном персонале" `
  --mode-preset ollama `
  --profile balanced `
  --limit 5 `
  --understand-query llm
```

Deterministic без LLM:

```powershell
python -m mr_norm.apps.main norm-lookup `
  --query "требования к заземлению" `
  --mode-preset deterministic `
  --doc-name "Правила устройства электроустановок" `
  --understand-query auto
```

Флаги `--understand-query`:
- `auto` — каталог документов + эвристики (по умолчанию в deterministic preset);
- `llm` — каталог + LLM выбирает только из candidates (ollama/polza preset);
- `off` — без предобработки.

Старые команды `rag-pipeline` / `rag-runtime` остаются для отладки и отчётов в JSON.

## 7. norm_lookup из Python

`norm_lookup` - тонкий программный слой над `run_pipeline`. Он не обращается к retrieval tools напрямую.

```python
from mr_norm.config.indexing import IndexingConfig
from mr_norm.skills.norm_lookup import NormLookupRequest, run_norm_lookup

result = run_norm_lookup(
    NormLookupRequest(
        query="требования к заземлению",
        filters={"doc_name": "Правила устройства электроустановок"},
        profile="balanced",
        limit=5,
        planner_backend="deterministic",
        reranker_backend="score",
        final_answer_backend="evidence",
        llm_provider="none",
    ),
    IndexingConfig.from_env(),
)

print(result.answer)
print([citation.chunk_id for citation in result.citations])
```

На выходе: `answer`, `citations`, `evidence`, `trace`, `warnings` и полный `pipeline` result.

## 8. Практические правила

- Для дешёвых и воспроизводимых прогонов оставляйте `--llm-provider none`.
- Для отчётов используйте `--save-report`; результаты лежат в `output/reports`.
- Если LLM вернул нестрогий JSON, post-processing пытается извлечь JSON и нормализовать частые отклонения.
- Prompt-бэкенды при ошибках откатываются к deterministic, passthrough или evidence-only поведению и пишут warnings.
- Не храните секреты в git. Файл `keys` предназначен только для локальной машины.
