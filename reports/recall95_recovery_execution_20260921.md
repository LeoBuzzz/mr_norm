# Recall95 recovery execution report

Дата: 21 сентября 2026. Выполнены пункты 1–5, 7 и 8 из плана восстановления. Пункт 6 (отдельная semantic/gold evaluation после исполнения) намеренно пропущен по указанию пользователя.

## Сделано

- Создана отдельная копия `sandbox/recall95_recovery`; исходная `sandbox/recall95_lab` и общий production `src` не изменялись.
- Сохранён архив исходной песочницы `sandbox/recall95_recovery/recovery_source_20260921.zip` и хеш-манифест исходных и восстановленных файлов.
- В recovery-копию перенесены исходные корпуса, каталог, openings и `qdrant_chunks.json` (31 083 chunks; SHA-256 `3525bda4…204504`). Код recovery импортирует копию `sandbox/recall95_recovery/src`, а не общий `src`.
- Добавлен единый контракт лимитов: wide максимум 80, reranker 20, final evidence максимум 40, final LLM input максимум 25. Shortfall классифицируется отдельно и не заполняется дублями.
- Добавлен crash-safe one-question worker: started/terminal events, provider/tool counters, caps, traceback, partial и независимый terminal artifact.
- Добавлены append-only JSONL events с sequence ID и flush; отдельно различаются tool failure, serializer failure и candidate classification failure.
- Добавлен subprocess coordinator. При первом ненулевом результате он фиксирует STOP; следующий worker не запускается. Есть тесты tool/serializer/invalid-count отказов и повторного case ID.
- Добавлен offline replay validator для сохранённых R0/H1-L3 traces.
- Добавлен content-addressed manifest локального snapshot. Он честно помечает embedding как `descriptor_only_weights_not_copied`; это ограничение блокирует live GO.

## Результаты

- Recovery tests: **111 passed** (`python -m pytest sandbox/recall95_recovery/v2/tests -q -p no:cacheprovider`).
- Mixed structural readiness: **passed, 30/30, 15 new + 15 old, H1-L3=true, external LLM/Polza/Ollama=0**. Это проверка состава и формата manifest, не quality evaluation.
- Локальный snapshot smoke: **passed**, 31 083 chunks, 0 network calls, 0 external LLM calls.
- R0 repeatability: три свежих процесса, compare **pass** по stage hashes и symbol restoration.
- H1-L3 repeatability: три свежих процесса, compare **pass** по stage hashes и symbol restoration.
- Offline replay validation: **passed**, 3 traces, 0 network calls, 0 external LLM calls.
- CLI replay validation: **passed**, 3 traces, 0 network calls, 0 external LLM calls.
- CLI coordinator fault matrix: exit code 2 after `cli_a` tool failure; `cli_b` не запускался.
- Astra medium review: прежний P0 fault injection выявил ошибки сериализации, invalid count, перезапись terminal и неполную проверку compare. Все четыре класса исправлены; добавлены coordinator subprocess tests. Повторная локальная suite — 111/111.

## Что не считается доказанным

1. R0/H1-L3 repeatability использует существующий `localhost:6333` путь в старом `repeatability_audit`; это подтверждает повторяемость при доступном сервисе, но не автономность без Qdrant. Автономно подтверждён только lexical read-only snapshot adapter.
2. Embedding weights не скопированы и не content-addressed; имеется descriptor-only запись. Полный live retrieval без зависимости от окружения не готов.
3. Пункт 6 не выполнялся: semantic/gold evaluator не добавлялся, поэтому ни один trace не получил статуса `quality_ok` или `valid_quality_miss` на основании отдельной проверки.
4. Платные LLM-вызовы не выполнялись. Текущий результат — техническая готовность recovery harness и локальная воспроизводимость, а не разрешение серии 15+15.

## Решение по следующему шагу

Recovery стенд теперь пригоден для безопасного продолжения, но **live/payed GO отсутствует** до получения настоящего embedding snapshot либо полностью детерминированного offline replay полного retrieval path. После этого можно отдельно решить вопрос о пункте 6 и только затем запускать `sample_016`; массовый прогон 15+15 пока не разрешён.

Первоначальная причина сбоя `sample_016` остаётся неизвестной: исходная трасса не сохранила exception и cost telemetry. Новый harness эту потерю воспроизводимо предотвращает.
