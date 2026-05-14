# Общий План MR Norm

## Цель

Создать новую детерминированную систему обработки нормативных документов и RAG-инструментов в `C:\PY\PythonProject\mr_norm`, не меняя рабочий проект `rag_norm`.

Новая система должна постепенно заменить набор больших скриптов на понятные блоки:

- ingestion документов;
- нормализация структуры документа;
- качественное чанкование;
- построение индексов;
- retrieval tools;
- трассировка времени и качества;
- будущие skills для проверки документов и подготовки нормативных обзоров.

## Принципы

- Сначала получить лучший корпусный артефакт, затем строить retrieval.
- Каждый этап должен иметь вход, выход, отчет качества и время выполнения.
- LLM не должна быть скрытой зависимостью базового ingest. На первом этапе только детерминированные правила.
- Настройки и промпты не должны быть разбросаны по коду.
- Старый `rag_norm` используется как reference implementation и baseline, но не как место для новых изменений.
- Fallback-логика запрещена без отдельного обсуждения. Если основной pipeline не смог корректно получить данные, нужно исправлять pipeline, а не подставлять результат эвристикой или догадкой.
- Качество должно измерять реальные дефекты обработки. Нельзя маскировать пустые metadata, заголовки, пункты или утверждающие акты искусственными подстановками.

## Целевая структура проекта

```text
mr_norm/
  input/
    All_raw_docks/
  output/
    marked_docs/
    qdrant_chunks.json
    reports/
  planning/
  src/
    mr_norm/
      apps/
      config/
      corpus/
      indexing/
      retrieval/
        tools/
      runtime/
      knowledge_graph/
      skills/
      tools/
      eval/
  tests/
```

На первом этапе `src/` может быть минимальным. Важно сразу разделить роли:

- `apps/main.py` - простой CLI-оркестратор.
- `tools/rtf_processor` - RTF -> структурированный текст/marked text.
- `tools/chunker` - structured text -> chunks JSON.
- `eval/chunk_quality` - отчет качества чанков.
- `tests/` - unit/integration/acceptance тесты качества маркирования, payload и итогового сравнения с `rag_norm`.

После Этапа 1 структура расширяется:

- `indexing/` - Qdrant adapter, embeddings config, import/export коллекций.
- `retrieval/tools/` - независимые deterministic tools `vector`, `payload`, `point`, `graph`.
- `runtime/` - router/orchestrator, planner profile, reranker, final answer pipeline.
- `knowledge_graph/` - concept extraction lifecycle, graph snapshot build/query.
- `skills/` - продуктовые навыки поверх tools: `norm_lookup`, `document_check`, `topic_review`, `corpus_librarian`.

## Дорожная Карта

1. **Этап 1: RTF -> qdrant_chunks.json**
   - Построить простой `main`.
   - Tool 1: обработка RTF из `input/All_raw_docks`.
   - Tool 2: чанкование и запись `qdrant_chunks.json`.
   - Добавить отчет качества и итоговый acceptance test, сравнивающий `mr_norm/output/qdrant_chunks.json` с baseline из `rag_norm` на одинаковом наборе документов.

2. **Этап 2: Quality gates**
   - Проверять doc identity, headings, point identity, chunk completeness.
   - Формировать JSON/Markdown reports по каждому запуску.
   - Завести небольшой набор ручных эталонов по документам.

3. **Этап 3: Индексация**
   - Подготовить адаптер Qdrant.
   - Разнести payload schema и embedding config.
   - Проверить, что новый `qdrant_chunks.json` пригоден для vector, payload и point lookup.

4. **Этап 4: Retrieval tools**
   - Реализовать независимые tools: `vector`, `payload`, `point`, позднее `graph`, аналогичные текущим tool-id из `rag_norm/llm_data_stores_and_retrieval.json`.
   - У каждого tool должен быть единый контракт запроса, ответа, trace и метрик.
   - Покрыть tools contract tests: входной DTO -> результаты -> trace -> ошибки.

5. **Этап 5: RAG runtime**
   - Собрать простой deterministic router и tool runner вместо монолитной логики `LLM_RAG_FLOW.py`.
   - Добавить профили `fast`, `balanced`, `deep`.
   - Только после этого подключать planner LLM, reranker и final answer как заменяемые runtime-слои.
   - Перенести prompt packs из `rag_norm/handlers/*.json` в конфигурируемые assets с JSON schema tests.

6. **Этап 6: Skills**
   - `norm_lookup` - ответы по нормативке.
   - `document_check` - проверка документа на соответствие нормативке.
   - `topic_review` - обзор нормативной темы.
   - `corpus_librarian` - документные аннотации, openings, виды документов, инвентаризация корпуса.
   - `graph_explorer` - объяснение терминов, аббревиатур и связей из graph snapshot.

## Целевая Архитектура Tools

В `rag_norm` retrieval tools уже описаны как `vector`, `payload`, `graph`, `point` в `llm_data_stores_and_retrieval.json`, но фактическая реализация смешана с orchestration в `LLM_RAG_FLOW.py` и `LLM_RAG_IMPR.py`. В `mr_norm` они должны стать независимыми модулями с единым контрактом.

Базовый контракт tool:

```text
ToolRequest:
  query: str
  filters: dict
  limit: int
  profile: fast|balanced|deep
  trace_id: str

ToolResult:
  items: list[ChunkRef]
  trace: ToolTrace
  metrics: ToolMetrics
  warnings: list[str]
```

`ChunkRef` должен ссылаться на стабильные поля из нового `qdrant_chunks.json`: `chunk_id`, `doc_id`, `point_id`, `filename`, `doc_name`, `heading_path_text`, `point_number`, `text`, `score`, `source_tool`.

Планируемые tools:

- `vector`: семантический поиск по Qdrant и embedding profile. Источник идей: `vectorization.py`, `LLM_RAG_FLOW.py`, Qdrant config из `config.py`.
- `payload`: точный и полнотекстовый поиск по payload/text, включая `doc_name`, `doc_kind`, `heading_path_text`, `point_number`. Источник идей: payload search в `LLM_RAG_FLOW.py` и plain report `MatchText`.
- `point`: поиск конкретного пункта/подпункта с учетом `point_number`, `point_scope`, `point_identity_key`, `doc_name` и `retrieval_anchor_heading`.
- `graph`: поиск по `knowledge_graph_snapshot.json`: термины, аббревиатуры, `IDENTITY`, `CONDITIONAL_IDENTITY`, `DEFINITION_AT`, `REFERENCES`. Источник идей: `knowledge_graph/graph_snapshot_query.py`.
- `opening`: получение первых смысловых chunks документа для вопроса о документе целиком. Источник идей: `handlers/extract_document_openings.py`, `handlers/document_kind.py`.

Тесты tools:

- unit tests для нормализации запроса, фильтров и парсинга `point_number`;
- contract tests для каждого tool на маленьком fixture `qdrant_chunks.json`;
- mocked Qdrant tests для `vector` и `payload`, чтобы не требовать живую БД в обычном pytest;
- fixture graph snapshot для `graph`, включая ambiguous abbreviation и fallback без LLM;
- trace tests: каждый tool возвращает используемые filters, лимиты, latency и причину пустого результата.

## Целевая Архитектура Skills

В `rag_norm` продуктовые навыки существуют неявно: часть в `LLM_RAG_FLOW.py`, часть в handler JSON, часть в graph scripts и corpus utilities. В `mr_norm` skill должен быть тонким orchestrator над tools, с входной/выходной schema, trace и тестами.

Планируемые skills:

- `norm_lookup`: ответ по нормативке. Использует `vector`, `payload`, `point`, опционально `graph`, затем rerank и final answer. Аналог текущего `LLM_RAG_FLOW.py`.
- `document_check`: проверка документа или фрагмента на соответствие нормативным требованиям. Использует retrieval tools, citation/point integrity, quality gates и отчет с найденными нормами.
- `topic_review`: обзор нормативной темы по нескольким документам. Использует multi-query retrieval, document diversity, graph expansion и финальную структурированную сводку.
- `corpus_librarian`: обслуживание корпуса: document annotations, openings, document kind, metadata audit, orphan report. Аналоги: `handlers/build_document_annotations.py`, `handlers/extract_document_openings.py`, `scripts/analyze_chunk_metadata.py`, `corpus_orphan_cleanup.py`.
- `graph_explorer`: объяснение понятий, аббревиатур и связей. Использует `knowledge_graph_snapshot.json` и graph tool без обязательного Qdrant.
- `plain_report_lookup`: отдельный будущий skill для DOCX/plain reports, если понадобится перенос `ingest_plain_reports.py`, `plain_report_chunking.py`, `LLM_RAG_plain_report.py`.

Тесты skills:

- schema tests: вход/выход каждого skill валидируется без LLM;
- golden tests на фиксированных вопросах и маленьком corpus fixture;
- no-LLM tests: deterministic router выбирает ожидаемые tools и возвращает trace;
- LLM-contract tests: planner/final-answer prompt packs проверяются на JSON schema и обязательные поля, но не требуют реального вызова LLM;
- citation tests: ответы ссылаются только на `chunk_id`, `doc_name`, `point_number`, которые реально были в retrieved evidence;
- regression tests: набор вопросов из `benchmarks/data` или нового `tests/fixtures/questions` не ухудшается по recall@k и coverage.

## Перенос Возможностей из `rag_norm`

Порядок переноса после Stage 1:

1. `vectorization.py` -> `indexing/qdrant_adapter.py`, `indexing/embeddings.py`, `apps/index.py`.
2. `llm_data_stores_and_retrieval.json` -> `retrieval/tool_registry.json` и typed contracts.
3. `LLM_RAG_FLOW.py` -> `runtime/tool_runner.py`, `runtime/router.py`, `runtime/reranker.py`, `runtime/final_answer.py`.
4. `handlers/*.json` -> `config/prompts/` с версиями prompt packs и schema tests.
5. `handlers/document_kind.py`, `extract_document_openings.py`, `build_document_annotations.py` -> `corpus/librarian.py`.
6. `knowledge_graph/*`, `concept_incremental.py`, `extract_definitions_all_marked.py` -> `knowledge_graph/` с отдельными offline build steps.
7. `telegram_bot.py`, `vk_bot.py` -> `apps/telegram_bot.py`, `apps/vk_bot.py` только после стабилизации `skills/norm_lookup`.

Правило переноса: сначала выделяется контракт и тест на fixture, потом переносится реализация, потом сравнивается поведение с `rag_norm` baseline.

## Baseline из `rag_norm`

Текущий RTF-процесс:

- `rtf_mark.py` читает RTF через Microsoft Word COM и использует `OutlineLevel` для заголовков.
- `processing.py` чистит текст, размечает заголовки, пункты и блоки `// ... \`.
- `chunking.py` извлекает chunks из marked TXT, добавляет `doc_name`, `doc_reg`, `heading_path_text`, `point_number`, `point_scope`, `point_anchor`.
- `ingest_new_rtf.py` связывает RTF -> TXT -> chunks -> Qdrant -> concepts -> graph.

Сильные стороны baseline:

- Уже использует структуру Word для заголовков.
- Есть payload для заголовков, пунктов и doc metadata.
- Есть атомарная запись JSON и инкрементальная логика.

Слабые стороны baseline:

- Логика размечания и чанкования сильно связана с эвристическими маркерами `//`, `***`, `#`.
- Длина чанка режется по символам, а не по смысловым единицам нормы.
- Структура документа не хранится как отдельное дерево.
- Нет обязательного отчета качества по документам и чанкам.
- Нет явного schema/version для `qdrant_chunks.json`.

## Контракт Совместимости с `rag_norm`

Новый `mr_norm` может иметь собственную расширенную schema/version, но должен сохранять payload-слой, понятный текущим retrieval/eval инструментам `rag_norm`:

- document metadata: `filename`, `doc_name`, `doc_reg`, `doc_title_full`, `approving_act`, `metadata_source`, `metadata_confidence`;
- heading payload: `headings`, `nearest_heading`, `heading_path_text`, `chapter_heading`, `chapter_number`, `chapter_title`, `section_heading`, `section_number`, `section_title`, `article_heading`, `article_number`, `article_title`, `retrieval_anchor_heading`;
- point identity: `point_number`, `point_scope`, `point_anchor`, `point_identity_key`;
- chunk position/split: `chunk_index`, `chunk_start`, `part_index`, `total_parts`, `is_split` для разрезанных chunks.

Маркирование в `mr_norm` должно проектироваться от этого payload назад: заголовки, пункты и structured paragraphs нужны для надежного заполнения этих полей, а не ради сохранения старых служебных markers.

## Итоговый Quality Gate

Перед переходом к индексации нужно иметь воспроизводимый тест/команду сравнения:

```text
python -m mr_norm.apps.main compare-baseline
```

или эквивалентный pytest acceptance-тест, который:

- берет одинаковый набор документов для `rag_norm` и `mr_norm`;
- сравнивает наличие обязательных payload keys;
- сравнивает доли пустых `doc_name`, `heading_path_text`, `point_number`;
- сравнивает дубли `doc_name + point_number + text_hash`;
- проверяет отсутствие служебных markers `//`, `***`, одиночного `\` в итоговом chunk text;
- проверяет стабильность `chunk_id` при повторном запуске `mr_norm`;
- падает, если новый `qdrant_chunks.json` не лучше baseline по согласованному набору метрик.
