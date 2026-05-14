# Этап 1: RTF Processing и Chunking

## Цель Этапа

Построить в `mr_norm` минимальную систему:

```text
input/All_raw_docks/*.rtf
  -> output/marked_docs/*.txt
  -> output/qdrant_chunks.json
  -> output/reports/chunk_quality_*.json
```

Задача не просто повторить `rag_norm`, а получить более качественный `qdrant_chunks.json`: с устойчивой идентификацией документа, заголовков, пунктов, чанков и с измеримым отчетом качества.

## Первый `main`

CLI должен быть простым:

```text
python -m mr_norm.apps.main ingest-rtf
python -m mr_norm.apps.main chunk
python -m mr_norm.apps.main build-chunks
python -m mr_norm.apps.main quality-report
python -m mr_norm.apps.main compare-baseline
```

На первом шаге допустим один объединенный режим `build-chunks`, который делает RTF -> marked docs -> chunks -> report.
Команда `compare-baseline` или одноименный acceptance-тест обязательны для закрытия этапа: они должны доказать, что `mr_norm/output/qdrant_chunks.json` лучше baseline из `rag_norm`, а не просто существует.

## Tool 1: RTF Processor

Вход:

- `C:\PY\PythonProject\mr_norm\input\All_raw_docks`.

Выход:

- `output/marked_docs/<filename>.txt`;
- промежуточный structured JSON по документу, если это быстро сделать без усложнения.

Что взять из `rag_norm`:

- Идею чтения через Word COM из `rtf_mark.py`, потому что она сохраняет `OutlineLevel`.
- Часть очистки из `processing.py`: удаление мусора, колонтитулов, технических строк, повторных пустых строк.

Что улучшить:

- Не полагаться только на текстовые маркеры `#`, `//`, `***` как на единственную структуру.
- Сохранять список абзацев с полями: `paragraph_index`, `text`, `outline_level`, `style_name`, `is_heading`, `candidate_point_number`.
- Фиксировать ошибки чтения по каждому RTF в отдельном report, а не только в логе.
- Не терять оригинальный порядок и источник каждого абзаца.
- Маркирование должно служить downstream payload: по нему должны надежно восстанавливаться `headings`, `nearest_heading`, `heading_path_text`, `point_number`, `point_scope`, `point_identity_key`.
- Fallback при чтении RTF, metadata extraction или построении payload не допускается без отдельного обсуждения. Если Word COM, metadata extraction или heading/point parsing не дают корректный результат, это дефект реализации, который нужно исправлять в основном pipeline.

Минимальная payload-схема абзаца:

```json
{
  "paragraph_index": 0,
  "text": "...",
  "outline_level": 10,
  "style_name": "",
  "is_heading": false,
  "heading_level": null,
  "point_number": ""
}
```

## Tool 2: Chunker

Вход:

- marked/structured documents после Tool 1.

Выход:

- `output/qdrant_chunks.json`.

Базовая схема чанка:

```json
{
  "schema_version": "mr_chunks_v1",
  "chunk_id": "stable id",
  "text": "...",
  "payload": {
    "source_file": "...rtf",
    "filename": "...txt",
    "doc_id": "stable doc id",
    "doc_name": "...",
    "doc_reg": "...",
    "doc_title_full": "...",
    "approving_act": "...",
    "metadata_source": "initial_lines_title",
    "metadata_confidence": "high|medium|low",
    "doc_kind": "law|decree|order|gost|pue|unknown",
    "doc_number": "",
    "doc_date": "",
    "authority": "",
    "headings": [],
    "heading_path": [],
    "heading_path_text": "",
    "nearest_heading": "",
    "chapter_heading": "",
    "chapter_number": "",
    "chapter_title": "",
    "section_heading": "",
    "section_number": "",
    "section_title": "",
    "article_heading": "",
    "article_number": "",
    "article_title": "",
    "retrieval_anchor_heading": "",
    "point_number": "",
    "point_scope": "",
    "point_anchor": "",
    "point_identity_key": "",
    "point_id": "",
    "chunk_index": 0,
    "chunk_start": 0,
    "char_start": 0,
    "char_end": 0,
    "token_estimate": 0,
    "is_complete_point": true,
    "split_reason": ""
  }
}
```

Что взять из `rag_norm`:

- `extract_metadata_from_initial_pages` как baseline для `doc_name`, `doc_reg`, `doc_title_full`.
- `extract_point_number`, `build_structured_heading_payload`, `build_point_identity_payload`.
- Идею atomic write JSON.

Обязательный compatibility layer из `rag_norm`:

- metadata: `filename`, `doc_name`, `doc_reg`, `doc_title_full`, `approving_act`, `metadata_source`, `metadata_confidence`;
- headings: `headings`, `nearest_heading`, `heading_path_text`, `chapter_heading`, `chapter_number`, `chapter_title`, `section_heading`, `section_number`, `section_title`, `article_heading`, `article_number`, `article_title`, `retrieval_anchor_heading`;
- point identity: `point_number`, `point_scope`, `point_anchor`, `point_identity_key`;
- chunk/split: `chunk_index`, `chunk_start`, `part_index`, `total_parts`, `is_split`.

Новые поля `mr_norm` (`schema_version`, `source_file`, `doc_id`, `doc_kind`, `heading_path`, `point_id`, `chunk_id`, `char_start`, `char_end`, `token_estimate`, `is_complete_point`, `split_reason`) добавляются поверх совместимого слоя и не заменяют его.

Что улучшить:

- Строить heading tree один раз и привязывать каждый chunk к узлу дерева, а не искать ближайший заголовок регуляркой по тексту.
- Делать chunk по нормативной единице: пункт/подпункт/абзац под пунктом, а не по произвольным `500` символам.
- Если пункт длинный, split должен сохранять `point_number`, `part_index`, `total_parts`, `split_reason`, и по возможности резать по подпунктам/абзацам/предложениям.
- Добавить стабильные `doc_id`, `point_id`, `chunk_id`, чтобы индексы и eval не зависели от порядка обработки.
- Не пропускать короткие значимые пункты только из-за лимита `100` символов. Вместо этого маркировать `short_but_structural`.

## Метрики Качества

Документный уровень:

- `documents_total`;
- `documents_processed_ok`;
- `documents_failed`;
- `metadata_confidence_distribution`;
- доля документов с пустыми `doc_name`, `doc_date`, `doc_number`, `authority`;
- число документов с одинаковым `doc_id` или очень похожим `doc_name`.

Структурный уровень:

- `headings_per_document`;
- доля документов без заголовков;
- доля heading nodes без текста после них;
- глубина heading tree;
- число подозрительных заголовков длиннее порога.

Пунктовый уровень:

- `points_detected`;
- доля чанков с `point_number`;
- число дублирующихся `point_id`;
- число пунктов, разорванных на части;
- доля chunks с `is_complete_point=false`.

Chunk-level:

- `chunks_total`;
- `avg_chars`, `p50_chars`, `p95_chars`;
- `avg_token_estimate`, `p50_token_estimate`, `p95_token_estimate`;
- доля chunks меньше 80 символов и больше целевого лимита;
- доля chunks без `heading_path_text`;
- доля chunks без `doc_name`;
- доля chunks с обрывом на двоеточии, открытой скобке, незавершенном перечислении.

Retrieval-readiness:

- наличие обязательных payload keys у 100% chunks;
- стабильность `chunk_id` при повторном запуске;
- отсутствие пустого `text`;
- отсутствие служебных маркеров `//`, `***`, одиночного `\` в итоговом `text`;
- достаточный контекст заголовка в payload без загрязнения текста чанка.

Payload compatibility:

- 100% chunks имеют обязательные `rag_norm` payload keys;
- типы ключей совместимы с `rag_norm`: `headings` как список строк, `point_number` как строка, `metadata_confidence` как `high|medium|low`;
- `heading_path_text` согласован с `headings`;
- `point_identity_key` имеет формат `point_number::point_scope::point_anchor`;
- split chunks сохраняют исходный `point_number`, `point_scope`, `point_identity_key` и получают `part_index`, `total_parts`, `is_split=true`.

## Метрики Времени

По каждому запуску:

- общее время;
- время чтения RTF;
- время очистки/структурирования;
- время chunking;
- время записи JSON;
- документов в минуту;
- chunks в секунду.

## Сравнение с `rag_norm`

Для честного сравнения на первом этапе достаточно взять одинаковый набор RTF и построить таблицу:

- сколько документов обработано;
- сколько chunks получено;
- сколько chunks без `doc_name`;
- сколько chunks без heading;
- сколько chunks без point number;
- сколько дублей `doc_name + point_number + text_hash`;
- распределение длины chunks;
- примеры 20 худших chunks по score качества.

Baseline источник:

- `rag_norm` не редактируется;
- текущий baseline JSON читается из рабочего артефакта `rag_norm/qdrant_chunks.json`, если он существует;
- если нужен свежий baseline на том же subset RTF, он запускается отдельно вручную в `rag_norm`, а `mr_norm` только читает результат сравнения.
- Запрещено улучшать comparison искусственными fallback-подстановками из filename или догадками, если основной extraction не смог получить данные из корректного источника. Такие случаи должны попадать в quality report как ошибки и исправляться в extraction pipeline.

Предлагаемый `chunk_quality_score`:

```text
100
- 25 если нет doc_name
- 20 если нет heading_path_text
- 20 если chunk пустой или почти пустой
- 15 если есть служебные маркеры
- 15 если chunk выглядит обрезанным
- 10 если нет point_number в документе с пунктовой структурой
- 10 если chunk слишком длинный
```

Итоговый acceptance-тест качества должен падать, если:

- новый `qdrant_chunks.json` не создан или не является валидным JSON-массивом chunks;
- меньше 100% chunks имеют обязательные payload keys `rag_norm`;
- количество документов с chunks меньше baseline на том же subset без объясненных ошибок RTF;
- доля chunks без `doc_name` не ниже baseline;
- доля chunks без `heading_path_text` не ниже baseline;
- доля chunks без `point_number` в пунктовых документах не ниже baseline;
- есть служебные markers `//`, `***`, одиночный `\` в итоговом `text`;
- повторный запуск на том же input меняет `chunk_id` для неизменных chunks;
- число дублей `doc_name + point_number + text_hash` не ниже baseline.

Предлагаемые тесты:

```text
tests/test_marking_payload_quality.py
tests/test_chunk_quality_report.py
tests/test_baseline_comparison_acceptance.py
```

`test_marking_payload_quality.py` работает на синтетическом structured-документе без Word COM и проверяет заголовки, пункты и `rag_norm` payload.  
`test_chunk_quality_report.py` проверяет расчет quality metrics и worst chunks.  
`test_baseline_comparison_acceptance.py` является итоговым тестом: он сравнивает `mr_norm/output/qdrant_chunks.json` с baseline `rag_norm/qdrant_chunks.json` или подготовленным baseline fixture на одинаковом subset.

## План Реализации Первого Этапа

1. Создать минимальный Python-пакет `src/mr_norm`.
2. Добавить config paths для `input`, `output`, `marked_docs`, `reports`.
3. Реализовать `RtfProcessor`:
   - чтение RTF через Word COM;
   - извлечение абзацев, outline level, style name;
   - сохранение marked txt и structured json.
4. Реализовать `ChunkBuilder`:
   - metadata extraction;
   - heading tree;
   - point detection;
   - chunk splitting по пунктам/абзацам;
   - stable ids.
5. Реализовать `ChunkQualityReporter`.
6. Добавить тесты качества маркирования, heading extraction и `rag_norm` payload compatibility.
7. Добавить итоговый baseline comparison acceptance-тест.
8. Добавить `main` с командами `build-chunks`, `quality-report`, `compare-baseline`.
9. Прогнать на малом наборе 3-5 RTF и посмотреть worst chunks.
10. Только после ручной проверки расширять на всю папку `input/All_raw_docks`.

## Ожидаемый Результат

К концу этапа должен быть создан `output/qdrant_chunks.json`, который лучше baseline не на ощущениях, а по отчету:

- меньше пустых/мусорных metadata;
- лучше привязка к heading path;
- стабильные ids;
- меньше обрезанных chunks;
- понятное распределение длины;
- воспроизводимый report качества.

Финальное условие завершения: итоговый тест сравнения показывает, что новый `qdrant_chunks.json` лучше `rag_norm` baseline по согласованным quality metrics, при сохранении `rag_norm`-совместимого payload.
