# Архив: этап 1, итерация 2 — semantic draft и сборка плана кодом

Версия 2.0, 2026-10-07. Статус: спецификация следующей реализации, не отчёт
о выполнении. Исполнитель кода — GPT-6 Luna medium. Предыдущая спецификация
сохранена в SPEC_QUESTION_PARSER_STAGE1_V1_ARCHIVE.md. Общий PLAN_QUESTION_PARSER.md
сохраняет силу; этот документ заменяет модельный контракт и протокол измерения.

## 1. Решение и измеримая гипотеза

H1: повторяющиеся технические отказы разных моделей в основном вызваны тем,
что модель одновременно разбирает смысл и вручную сериализует готовый план.
Проверяем одно изменение: LLM возвращает минимальный semantic draft, код
строит окончательный контракт. Не заменять это сменой модели, ослаблением
смысловой приёмки или неограниченными repair-вызовами.

Основание — три сохранённых Polza tuning-отчёта 2026-10-07 по 20 случаев:

- Luna/Sol: классификация 15/20, полный план 0/20, технические ошибки 18/20.
- Sol/Astra: 18/20, 1/20, 11/20 соответственно.
- Gemini Flash Lite/Pro: 17/20, 0/20, 20/20.

Повторяются evidence, массивы, issues, ID, scope/coverage. Это поддерживает H1,
но у технически валидных ответов также есть смысловые потери. Диагноз и
ограничения сравнения — DECISION_QUESTION_PARSER_STAGE1_V2_20261007.md.

Следующий прогон по выбору пользователя: GPT-6 Luna medium для classifier и
simple_parser, GPT-6 Sol medium для complex_parser, через подписку Codex.
Polza не использовать. Это экспериментальная пара, не принятая runtime-модель.

## 2. Результат, файлы и границы

Существующая plan_skill_query принимает question, dialog snapshot, pending,
catalog/validation_catalog, knowledge, adapter и config; возвращает dry-run
план. Сохранить совместимость Route/route_query и baseline-тестов. Не вызывать
скиллы, поиск, Qdrant, бота или генерацию нормативного ответа. Production и
параллельную definition-задачу не менять.

Доработать router_contracts.py, router_prompts.py, router_planning.py,
run_router_plan_eval.py, тесты и артефакты песочницы. Допустимы небольшие
router_draft.py и router_subscription_eval.py для сборщика и файлового адаптера.
Не создавать второй планировщик, словарь, memory framework или сетевой SDK.

Обычный путь: два логических этапа LLM. Технические поля собирает код,
provenance проверяется точно, полнота смысла измеряется отдельно.

## 3. Разделение ответственности

Модель определяет намерения, принадлежность условий задачам, подходящий смысл
термина/документа, продолжение/поправку, зависимость и предмет неоднозначности.
Код не подменяет эти решения набором ключевых слов.

Код формирует IDs, schema_version, original_query, skill, coverage, status,
issues, trace, пустые коллекции/null, exact evidence, проверенные словарные
варианты и вопрос уточнения. Модель не должна дублировать skill/scope/coverage,
копировать цитаты посимвольно или придумывать технические issue-коды.

Не спасать невалидный draft догадкой. Unknown fields/enums/refs и неверные типы
отклонять. Только пропуск необязательного поля означает заранее определённый
default; null не равен пропуску. Не исправлять 86 на 861 и не угадывать doc_id.

## 4. Immutable input manifest и SourceRef

Использовать существующие match_query_terms, каталог, knowledge, snapshot и
pending adapters. Вход содержит original_query, пользовательские turns,
pending_original, подтверждённые условия с ID, candidate documents и группы
словарных значений. Никаких expected/gold, ожидаемого класса или списка
правильных задач в model input. Не передавать длинные нормативные тексты.

Для каждого source turn код строит таблицу токенов: ordinal от 1, start/end
в исходной строке, text. Токенизация детерминирована и версионирована; номера
6.25 атомарны, исходная строка сохраняется. Начальная tokenizer_version=source_tokens.v1:
regex `\d+(?:[._]\d+)*|[^\W\d_]+(?:[-’'][^\W\d_]+)*|[^\s]` с Unicode,
finditer даёт offsets; номера/слова/пунктуация отдельные токены. Пробелы остаются
между offsets исходной строки. Модель видит пронумерованные токены.
SourceRef — строка `turn_id@first:last`, границы включительны, например
`current@3:6`. Код извлекает quote как source[start_first:end_last], включая
исходные пробелы/пунктуацию. Модель не вычисляет символьные координаты.

Неизвестный turn, пустой/обратный интервал, выход за границу — contract error.
Один SourceRef не пересекает turns. Одинаковые inputs/version дают одинаковые
IDs и срезы. При использовании reserved pending_original проверять конфликт ID.

Словарь предоставляет meaning IDs и варианты; разные значения аббревиатуры
не объявлять синонимами. Для follow-up готовить варианты используемых
контекстных/pending фрагментов, а не только короткого текущего ответа.
Полный словарь не помещать в prompt. Подтверждённые условия имеют исходный
SourceRef; неподтверждённые выводы модели не повышать до фактов.

validation_catalog — universe проверки ID, frozen candidates — пространство
выбора случая. Документ истории не становится текущим кандидатом нового
самостоятельного вопроса автоматически.

## 5. Первый вызов: classifier

Ответ строго `{"complexity":"simple"}` или `{"complexity":"complex"}`.
Убрать reason_codes: комбинация single_intent с другими причинами сама давала
ошибки. Лишние поля запрещены; classifier не возвращает план.

Simple: одна однозначная задача/область, несколько условий допустимы.
Однозначное «А пункт 2.1?» может быть simple. Несколько целей, сравнение,
коррекция, зависимость, неоднозначность, unsupported intent или неуверенность
означают complex. Ошибка JSON/transport classifier → error без fallback.

Второй вызов обязателен: simple_parser Luna, complex_parser Sol. При неудаче
simple не эскалировать автоматически к Sol: ошибка классификации остаётся
видна. Правила выбора одинаковы в fake, subscription и будущем runtime.

## 6. Semantic draft v2: wire-схема

Версию сообщает envelope кода, модель её не пишет. Объект имеет обязательные
dialog и tasks; необязательное uncertain. dialog: new|continue|correct|clarify.
tasks: 0–6 объектов. Пустой tasks без предметной неопределённости → error.
Пропуск uncertain → []; null/unknown fields запрещены.

Обязательные поля задачи только operation и source:

- operation: text|search|all|define|compare|conclude|summarize|unknown.
- source: непустой массив SourceRef, обосновывающий намерение.

Необязательные поля передавать только при наличии значения:

- topic: отдельный краткий поисковый вопрос со значимыми условиями; для
  search/all обязателен, если нет uncertain по intent. Для других операций
  при пропуске код берёт выбранные исходные фрагменты. Не генерировать ответ.
- document: canonical doc_id, corpus или unresolved. Default corpus для
  search/all; text без документа → unresolved и уточнение. Явная ссылка на
  документ или наследуемая область требует поля, default не должен её скрыть.
- point: SourceRef номера пункта. Код извлекает значение. Для двух пунктов —
  две задачи с собственными document/point. Число с единицей не является пунктом.
- terms: список {source, meaning?}; source — один SourceRef, meaning — ID
  выбранного значения словаря. Surface и variants строит код. Пропуск meaning
  сохраняет исходный термин; конкурирующие значения требуют uncertain.
- conditions: список {source, kind, polarity}; source — один SourceRef;
  kind object|action|condition|quantity|exclusion; polarity include|exclude.
  Код извлекает text/evidence и назначает ID. Число с единицей — один SourceRef;
  знак сравнения и отрицание нельзя терять. Полярность решает модель.
- after: номера предыдущих задач (от 1) в массиве; semantic dependency.
  Forward/self/цикл — ошибка; технические task_id модель не пишет.
- replace: ID явно исправленных подтверждённых условий snapshot. Только
  dialog=correct с основанием в current.

terms/conditions/after/replace при пропуске код превращает в []. Не просить
модель выводить пустые массивы и не делать их required в transport schema.
Необязательное поле, если присутствует, валидируется полностью.

uncertain: список {field, source, task?, candidates?}. field:
document|term|point|scope|intent. source — SourceRef предмета неясности; task —
номер связанной задачи, если определён; candidates — только ID manifest.
Здесь нет готового question, status или issue code.

Пример формы с условными SourceRef, не gold:

```json
{"dialog":"new","tasks":[{"operation":"search","source":["current@1:8"],"topic":"требования к заземлению ВЛ 110 кВ","terms":[{"source":"current@5:5","meaning":"vl_air"}],"conditions":[{"source":"current@6:7","kind":"quantity","polarity":"include"}]}]}
```

Схема additionalProperties=false хранится рядом с контрактом; описание для
prompt генерируется из неё. Проверить тестом равенство required/enums схемы,
prompt и локального validator. Не поддерживать две расходящиеся схемы вручную.

## 7. Сборка публичного плана

Сохранить поля SkillQueryPlan; schema_version=skill_query_plan.v2. Новая версия
явно обозначает provenance через SourceRef и неисполняемые unresolved-задачи.
Исторические v1 отчёты не переписывать.

1. Провал подготовки/JSON/schema/refs/transport → error. Превышение лимита
   также error с issue input_limit_exceeded, не пятый статус.
2. Назначить t1...; after преобразовать в depends_on; проверить replace.
3. text → exact_point/exact; search → corpus_point_search/ranked;
   all → find_all/comprehensive. define/compare/conclude/summarize/unknown →
   skill=null; unknown требует уточнения intent, остальные unsupported_intent.
4. Canonical document → scope=document; corpus → corpus/doc_id=null;
   unresolved → unresolved/doc_id=null. Без проверки ID нет ready.
5. Query из topic, иначе исходных source; intent_text из шаблона operation и
   исходного предмета. Не требовать слово «найди» в query ради оценщика.
   Дословное сохранение выбранных условий предпочтительнее красивого пересказа.
6. Evidence задачи/условия/понятия — точные проверенные срезы SourceRef;
   дедуплицировать. Сохранять draft и source map в trace. Цитата сама по себе
   не доказывает смысловой полноты task.query и constraints.
7. Variants только из выбранного meaning и морфологии. Не включать все разные
   значения аббревиатуры, чтобы скрыть отсутствие выбора.
8. Код создаёт ambiguity issues из uncertain/незаполненных смысловых полей,
   unsupported/dependency — из operation/after. Technical failure не маскировать
   под пользовательскую неясность.
9. Приоритет status: error > needs_clarification > deferred > ready. Ready
   только полный независимый план из трёх поддержанных скиллов. У unresolved
   exact_point в needs_clarification допустим отсутствующий doc_id/point_ref;
   это сохранённое намерение, не готовый вызов. Не скрывать его skill=null.
10. Clarification — шаблон кода по document → term → point → scope → intent.
    Вопрос называет subject из source, options 0–5 берутся из manifest.
    Например «Какой документ вы имеете в виду под „{subject}“?». Остальные
    неоднозначности остаются в issues. Не писать общее «Уточните вопрос».

Границы: structural grounding не доказывает смысловую полноту. Существующие
checks чисел/отрицаний могут отклонять явную потерю, но не должны догадкой
добавлять условие. Полнота намерений/ограничений проверяется gold/eval.

## 8. Диалог и pending

Сохранить принятые регрессии продолжения, коррекции, нового вопроса и pending.
Production memory persistence не входит. ID стабильны внутри snapshot.

Код строит pending из проверенного плана: original_query, semantic draft и
SourceRef, подтверждённые условия, field, subject, options, target tasks.
Модель не сериализует ещё один полный nested contract. Адаптер старого pending
разрешён только с полной проверкой источников; тему/условия не терять.

Закрытый ответ привязан к canonical option ID. При options=[] проверенный
draft может разрешить уточнение по текущему источнику: один целый/составной
номер пункта; несколько refs через разные разделители остаются неясностью;
числа с единицами, в том числе без пробела, не пункты. Документ должен быть
единственным среди frozen candidates; неоднозначный alias, отрицание,
«не знаю» сохраняют pending. Сохранять исходную тему/условия после разрешения.

## 9. Adapter и schema enforcement

Adapter возвращает raw content и metadata, а не только готовый Python dict.
Capability metadata: transport_name, authentication_mode, supports_json_schema,
capability_evidence, requested_model, actual_model (может быть null), effort,
usage (может быть null), physical_request_count (может быть null).

Настоящую JSON Schema передавать лишь если конкретный подписочный transport
документированно и фактически поддерживает её. Сохранить версию transport,
параметры и capability evidence. Один правильный JSON не подтверждает
enforcement. У exposed collaboration.spawn_agent нет response_format/schema;
для file protocol supports_json_schema=false. Схема в prompt — инструкция,
не constrained decoding. json_object не равно JSON Schema.

Некоторые strict schema transports требуют все поля required. Не добавлять
ради этого в модельный draft десятки null/пустых полей. Допустима доказанно
эквивалентная wire-schema для поддерживаемого subset (с тестом round-trip),
иначе schema capability для этого draft=false и используется fail-closed путь.
Не объявлять unsupported schema принятой только потому, что HTTP вернул 200.

При schema/reference ошибке parser разрешён один repair той же модели с тем
же входом, raw draft и списком ошибок; без gold/желаемого ответа. Максимум
3 logical role jobs на случай. Classifier/transport error без repair;
смысловая ошибка, обнаруженная gold, repair не запускает.

Лимиты: max_tasks=6, query=8000, serialized input=16000 символов; classifier
256, parser 4096 output tokens; timeout 60s, если transport умеет это применять.
В subagent protocol unsupported hard limit=null с причиной, host deadline
учитывается отдельно. Не выдавать его за модельный timeout/max_tokens.

## 10. Воспроизводимый прогон из подписки

Текущий build_chat_client умеет ollama/polza, подтверждённого subscription
endpoint в репозитории нет. Не извлекать OAuth tokens, не обращаться к
недокументированным backend URL, не подменять подписку платным API. Наличие
официального app-server механизма не доказывает локальную готовность и доступ
нужных моделей. Если direct transport не подтверждён, использовать ниже.

experiment_mode=codex_subscription_file_replay. Ответы workers реально получены
через подписку; replay этих ответов выполняется offline. Это не fake LLM test,
не Polza и не production runtime benchmark.

1. Freeze run directory: manifest.json; prompts/classifier.txt, parser.txt;
   schema.json; input JSONL только 20 tuning без expected; sources/candidates.
   Сохранить SHA-256 файлов, версии кода, порядок случаев, locale. Inputs для
   двух ролей различаются только необходимым role envelope, не скрытыми hints.
2. Spawn две worker-роли без fork истории: `gpt-6-luna` medium и `gpt-6-sol`
   medium. Сохранить host spawn receipts. Если ID недоступен, blocked;
   не менять модель/alias. Это новые eval workers, не исполнители/критики кода.
3. Worker получает только frozen role instruction и input envelope. Запрет
   читать gold, репозиторий, тесты, отчёты, Internet; не запускать tools, код
   или другие модели. Final содержит только raw JSON. Host передаёт prompts
   в неизменном виде и логирует весь message wrapper.
4. Luna классифицирует каждый случай отдельным заданием. Host немедленно
   сохраняет raw final, валидирует и строит parser request manifest по actual
   классу. Invalid classifier — ошибка случая. Luna парсит simple, Sol complex;
   parser prompt одинаков. Classifier не передаёт скрытый план. Repair только
   по разделу 9, запрос repair также сохраняется до dispatch.
5. Parent/host сохраняет receipts, а не просит worker переписать собственный
   ответ после проверки. Receipt: run_id/case_id/role/attempt, input/prompt/schema
   hashes, agent_id, requested model/effort, model evidence, timestamps,
   raw_final_text/raw_hash, event/message ID, finish/error. Raw — неизменный
   финальный текст, наблюдаемый через host tool; это не дамп внутреннего server
   response. UTF-8 bytes наблюдаемого текста кодируются один раз для hash.
   Fenced JSON или лишний текст — wire error, не очищать вручную.
6. ReplayAdapter читает по точному key/hash без сети. Missing/duplicate/hash
   mismatch → replay_input_error. Тот же assembler/validator создаёт планы,
   отдельный eval читает gold. Два replay должны дать одинаковые substantive
   plans/metrics; generated_at не сравнивать. Sampling не повторять скрыто.
7. actual_model брать только из host metadata; самоназвание модели не evidence.
   Если серверная metadata недоступна: actual_model=null и
   verification=requested_by_host_only. Не утверждать server verification.
8. Logical role jobs считать точно; physical requests/usage/price при отсутствии
   данных=null. Не приравнивать requests к числу tool calls. Queue/agent overhead
   учитывать отдельно, не сравнивать latency с HTTP Polza как равные величины.
9. Два persistent workers имеют историю внутри run. Фиксировать порядок и
   transcript hashes; не называть это изолированными single-request API calls.
   Воспроизводим replay, а не случайный повторный sampling. Изоляция fresh
   workers на случай, если потребуется, — отдельная версия протокола.

Перед стартом/переходами проверять 5h usage. При usedPercent>=95 сохранить
receipts/checkpoint и остановить workers; новый batch не начинать уже при >=90.
После сброса продолжать только с теми же hashes. Незавершённый ответ не
засчитывать и не угадывать. Никаких Polza-вызовов в subscription protocol.

## 11. Миграция gold/eval без открытия holdout

v1 gold и три исходных отчёта immutable. Создать tuning_v2 из tuning inputs и
semantic expectations, уже имеющихся в этих отчётах. Сохранить 20 case IDs,
вопросы, context, candidates, операции, пары doc/point, условия и зависимости.
Не менять expected под ответ модели. Содержательная правка эталона требует
отдельного diff/основания и нового hash до модельного прогона.

Source manifests создаются без gold. Заменить обязательное дословное совпадение
evidence quote на gold source coverage: нужный атом покрыт допустимым точным
срезом нужного turn. Более широкая цитата допустима, но не заменяет сохранения
условия в query/constraints. Сравнивать draft и assembled plan отдельно:
operation/topic/conditions/terms → skill/query/scope/point/constraints.

Intent проверять enum, не требовать action word в query. Для constraints
сравнивать kind/polarity/число+единицу и смысловой объект с заранее разрешёнными
словоформами. Независимые задачи сопоставлять one-to-one по смыслу; dependencies
после сопоставления, а не по случайному номеру. Clarification проверять по
field/предмету/options, не дословному шаблону. Unknown synonyms сохранять как
mismatch для слепой проверки; не «дожимать» LLM judge до нужного балла.

V03/V06 оставить в tuning; paired actual metric по skill/scope/concept meaning
сохраняется. Старые Polza scores обозначать legacy_v1_comparison; v2 semantic
scores публиковать отдельно, не смешивать разные evaluator/transport.

Holdout здесь не открывать. Подготовить migration script на tuning/synthetic,
без чтения/печати закрытых случаев. Будущий запуск миграции/holdout — после
tuning gate и новой команды; freeze нового hash, проверка сохранения смысла
ожиданий/split, один контрольный прогон. Проход tuning не означает holdout pass.

## 12. Проверки и приёмка

Offline gate до worker sampling:

- Совместимые старые регрессии и новые tests проходят. Source slicing, invalid
  refs/IDs, ambiguous meanings, количество/отрицание, defaults/null/unknown fields,
  DAG, pending и отсутствие мутации имеют positive/negative проверки.
- На representative архивных raw ответах документированы v1 сбои; synthetic
  draft tests доказывают сборку IDs/evidence/issues/coverage/defaults кодом.
  Это не live-измерение интеллекта модели.
- 20 tuning expectations/manifests согласованы; gold отсутствует в model inputs.
- Replay tests покрывают missing/duplicate/hash mismatch, classifier failure,
  2 задания в норме, <=3 с repair, отсутствие network/search side effects.

Tuning gate (все 20 случаев в знаменателе):

- >=19/20 первых parser drafts валидны по wire/schema; после одного разрешённого
  repair 20/20 технически валидных итогов, включая уточнения/deferred.
  Transport/classifier failure остаётся неуспехом, не исключается из знаменателя.
- Классификация >=18/20; все размеченные compound/dependency/ambiguity/unsupported
  идут в complex. Полный semantic plan/status >=18/20.
- Ноль ready с потерянным намерением, обязательным отрицанием, числом/единицей,
  неверным doc_id, неподдержанной операцией или зависимой цепочкой.
- Все ambiguous случаи дают предметное уточнение; <=1 ненужного уточнения
  на однозначных. Paired metamorphic metric проходит.
- Ноль invalid quotes и несовместимых operation/skill/coverage в финальном плане;
  это гарантия сборщика, не достижение модели.

Раздельные метрики: classification, first-draft validity, repair success,
assembler errors, semantic correctness, critical omissions, receipt coverage,
unknown metadata. При failed gate сохранить диагноз, holdout не запускать.
Сравнение Polza/v1 и subscription/v2 не доказывает H1 причинно: изменился
transport. Оно проверяет пригодность новой пары/контракта в названном протоколе.
Same-transport A/B для причинного вывода — отдельный эксперимент, не эта задача.

## 13. Порядок для исполнителя

1. Freeze tuning_v2, schema draft и source addressing.
2. Validator/assembler; перенести принятые safety/dialog regressions.
3. Упростить prompts; capability metadata; оставить legacy eval для истории.
4. Добавить manifests/receipt importer/ReplayAdapter без network fallback.
5. Независимые tests/critique, исправления и offline acceptance.
6. После acceptance — подписочный tuning по frozen protocol.
7. Сохранить отчёт tuning_passed|tuning_failed|blocked|paused_limit с hashes,
   фактическими metadata и точным следующим шагом в checkpoint.

Сейчас меняются документы. Спецификация не подтверждает новый модельный прогон,
доступность subscription endpoint или schema enforcement в подписке.
