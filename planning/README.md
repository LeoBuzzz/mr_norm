# MR Norm Planning

Эта папка хранит планы, заметки и решения по новой системе `mr_norm`.

Цель: строить обновленный нормативный RAG отдельно от рабочего `rag_norm`, не ломая текущий пайплайн. На первом этапе фокус только на детерминированной обработке RTF и улучшенном `qdrant_chunks.json`.

## Документы

- [Общий план](overall_plan.md) - архитектурная дорожная карта новой системы.
- [Этап 1: RTF и чанки](stage1_rtf_chunking_plan.md) - план первого простого `main` и двух tools: RTF processing и chunking.

## Исходные ограничения

- Рабочий `rag_norm` не изменяем.
- Новый вход RTF: `C:\PY\PythonProject\mr_norm\input\All_raw_docks`.
- Первый целевой артефакт: `qdrant_chunks.json`, лучше текущего в `rag_norm` по структуре, метаданным и пригодности для retrieval.
- `rag_norm` используется только как reference/baseline: `rtf_mark.py`, `processing.py`, `chunking.py`, `ingest_new_rtf.py` изучаются и сравниваются, но не редактируются.
- Payload нового `qdrant_chunks.json` должен сохранять совместимый слой `rag_norm` (`doc_name`, `doc_reg`, `doc_title_full`, `approving_act`, `headings`, `heading_path_text`, `point_number`, `point_scope`, `point_anchor`, `point_identity_key`), а новые поля `mr_norm` добавляются поверх него.

## Жесткое Правило: Без Fallback

- Fallback-логика не допускается без предварительного обсуждения и явного решения.
- Если основной скрипт не извлекает поле, структуру или документ корректно, это считается дефектом pipeline, а не поводом "добить" результат эвристикой.
- Нельзя вручную или отдельной догадкой заполнять то, что не смог корректно получить основной алгоритм.
- Нужно исправлять причину сбоя: чтение RTF, извлечение структуры, metadata extraction, heading/point parsing или качество исходного контракта.
- Отчеты качества должны показывать реальные ошибки pipeline, а не скрывать их за подстановками.

## Definition of Done для Этапа 1

Этап 1 считается завершенным не после генерации файла, а после итогового quality test:

- на одинаковом наборе RTF построены baseline-метрики для `rag_norm` и новые метрики для `mr_norm`;
- `output/qdrant_chunks.json` в `mr_norm` имеет 100% обязательных payload keys;
- новый результат не хуже baseline по количеству обработанных документов и лучше по ключевым дефектам: пустые metadata, пустые heading, missing point identity, служебные маркеры в chunk text, нестабильные/дублирующиеся ids;
- есть воспроизводимый JSON/Markdown отчет сравнения в `output/reports`.
