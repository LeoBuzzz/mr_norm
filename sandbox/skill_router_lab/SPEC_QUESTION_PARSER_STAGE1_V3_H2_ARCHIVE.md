# Этап 1, итерация 3: явные правила смыслового разбора

Версия 3.0, 2026-10-08. Статус: следующая спецификация, реализация и новый
модельный прогон ещё не выполнены. Общий PLAN_QUESTION_PARSER.md сохраняется.
V2 полностью сохранена в SPEC_QUESTION_PARSER_STAGE1_V2_ARCHIVE.md; все её
требования действуют, кроме явно заменённых ниже. Wire-контракт остаётся
semantic_draft.v2, результат skill_query_plan.v2. Это версия эксперимента,
а не новый формат задач.

## 1. Одна гипотеза и неизменяемые части

H2: schema-oriented prompt v2 объясняет форму JSON, но недостаточно определяет
смысл операций и распределение значимых элементов по полям. Явная общая
инструкция «операция → цели → ограничения → контекст → неопределённость»
улучшит полный план той же пары Luna/Sol без нового вызова модели.

Единственное изменение поведения парсера в эксперименте — новая версия
classifier/parser instructions с небольшими контрастными примерами вне gold.
Не менять модели, effort, schema, сборщик, SourceRef/tokenizer, кандидатов,
лимиты, число вызовов, словарь, вопросы/expected или порядок 20 tuning случаев.
Manifest меняется только hashes prompts/code version и run_id, не содержанием
case input. Не подставлять в prompt case IDs, эталонные планы или буквальные
формулировки tuning-вопросов. Обнаруженное нарушение сборщика, требующее
изменения runtime, останавливает этот эксперимент и оформляется отдельно.

Измерительную ошибку нельзя лечить инструкцией модели копировать ошибочный
gold. Поэтому сначала offline калибруется оценщик по общим правилам §3,
замораживается, пересчитывается baseline из старых raw receipts. Это подготовка
измерения, а не вторая гипотеза улучшения runtime. Новые ответы оцениваются
тем же frozen evaluator. Отдельно сохраняется legacy score для сопоставимости.

## 2. Фактическая исходная точка

router_subscription_eval_tuning_v2_20261008.json: semantic plan 7/20;
классификация 17/20; complex 8/11; first wire validity 20/20, first technical
16/20, final technical 19/20. Assembler exceptions 0 не доказывают правильность
всех сборок. Ambiguity fields 2/3, subjects 1/3, options 2/3. Paired V03/V06 pass.

Три ошибки classifier: C02 (два пункта), A03 (конкурирующие смыслы), U03
(определение — unsupported). C02 содержит один point SourceRef на два номера,
правильно отклонён validator. В receipts для C02 нет attempt=2; нельзя писать
«repair ответил неправильно». Неполная попытка остаётся неуспехом в знаменателе.

13 plan failures не равны 13 смысловым потерям. Подробная разметка причин
находится в DECISION_QUESTION_PARSER_STAGE1_V3_20261008.md. Source coverage 8/20
и dependency match 10/20 зависят от task matching в текущем оценщике; это не
независимые показатели качества цитирования/зависимостей. Не выводить из них
«12 неправильных источников» или «10 потерянных зависимостей».

## 3. Offline подготовка честного измерения

Gold v2 с hash 386e7d682cf5153dd9e5bba266e348eb621fc6cfd6378b2af51c62e4609c14fe
не менять. Старые receipts и отчёт также immutable. Разрешены только общие
правила scoring, следующие из принятого semantic контракта:

1. Evidence: считать объединённое покрытие source spans нужного turn, а не
   наличие одного actual quote, содержащего весь gold quote. Пунктуация
   сама по себе не обязательный semantic atom. Для точного пункта проверены
   одновременно intent, doc_id, point_ref и их grounding; соединительный
   предлог между уже grounded частями не должен быть самостоятельной причиной
   провала. Ни отрицание, ни число/единица, ни маркер сравнения/ограничения
   не являются необязательными токенами. Сохранить отдельно strict span metric.
   Отображение old source_refs в обязательные spans строить по зафиксированным
   правилам до нового sampling, не по новому actual. Нужна read-only audit map
   с причиной исключения каждого несмыслового токена, без правки gold.
2. Морфология: использовать существующую нормализацию проекта для ordinary
   Russian wordforms в query/subject. Не новый LLM judge и не prefix[:3].
   Числа, doc_id, единицы, отрицания/полярность не нормализовать как синонимы.
   «подстанциям» и «подстанции» эквивалентны; «подстанции» и «электростанции» нет.
3. needs_clarification + expected.tasks=[]: legacy означает отсутствие готовых
   вызовов, не обязательную потерю распознанного намерения. Разрешить сохранённые
   unresolved задачи только если они связаны с требуемым уточнением, grounded,
   не исполняются и не выбирают неизвестный doc/смысл. Ready с лишними задачами
   и не связанный с ambiguity новый intent по-прежнему ошибка. В A02 corpus
   у ожидающей уточнения задачи нельзя выдавать за уже подтверждённую область.
4. Не снижать строгость object constraints V01/V03/V06: schema различает terms
   (имена/варианты) и conditions (область/объект применения). Пока публичный
   контракт ожидает оба, один concept не заменяет object condition. Не превращать
   true missing condition в pass. Не auto-copy terms в constraints.
5. Legacy operation=unknown при expected deferred трактуется как неподдержанная
   операция, а не новая команда уточнять intent. У actual draft различать define,
   compare, conclude, summarize; не сравнивать их только по skill=null. Вычислить
   semantic operation expectations для этих legacy строк отдельной audit map
   из исходного вопроса до sampling, независимо от ответов. При невозможности
   однозначного отображения — оценка indeterminate, tuning не объявлять passed.
6. Critical omissions и source/dependency diagnostics используют ту же one-to-one
   assignment, что основной scorer. Для повторных одинаковых skills не выбирать
   только «единственный» task. Отличать not_applicable/unmatched от доказанной
   ошибки конкретного edge/source. Общий pass остаётся fail при unmatched task.
7. Gate first-draft >=19/20 относится к technical validity (schema+refs),
   а не лишь wire validity: отчёт v2 дал wire=20 при technical=16. Исправить
   подключение метрики, сохранив historical report без изменений.

Итог preflight: baseline_reconciled.json и отдельный reasoned diff к исходным
7/20. Все улучшения счёта без нового sampling пометить evaluator-only; они
не подтверждают H2. Если неоднозначные правила scorer нельзя обосновать общим
контрактом, остановиться перед sampling, а не подогнать ожидания.

## 4. Точные изменения instructions

Не добавлять выходной checklist, rationale или chain-of-thought. Требуются
только существующие classifier JSON и semantic draft. Содержимое ниже включить
в normative инструкции; описание JSON schema по-прежнему генерируется кодом.

Classifier перед выбором simple проверяет пять смысловых признаков:

- Один или несколько требуемых результатов/пар документ–пункт? Несколько
  номеров одного документа — multiple targets, даже если один глагол.
- Требуется результат одного действия для другого? Тогда complex.
- Есть коррекция предыдущего условия? Тогда complex.
- Есть несколько релевантных документов/значений без разрешающего контекста?
  Тогда complex. Список кандидатов — возможные смыслы, а не доказательство
  тождества. Одна аббревиатура может быть неоднозначным коротким вопросом.
- Есть define/compare/conclude/summarize? Это unsupported stage1, значит
  complex независимо от длины. Простое в бытовом смысле ≠ simple маршрута.

Parser получает определения operations, а не только enum:

- text: текст конкретного пункта; одна задача на одну пару doc/point.
- search: тематический поиск без явного требования полноты. Множественное
  «пункты/требования» само по себе не означает all.
- all: явно требуется полный перечень в теме. «Все» внутри условия, например
  «для всех смен», само по себе не означает полный перечень результатов.
- define/compare/conclude/summarize: сохранить соответствующее неподдержанное
  намерение. unknown — действительно неясное намерение, а не любое unsupported.

Обязательные смысловые связи fields:

1. Topic сохраняет тему и объект, когда без объекта поисковый вопрос становится
   шире исходного. Terms описывают термины/словарные варианты; conditions
   задают объект/действие/условие применения. Один SourceRef может закономерно
   участвовать в terms и conditions. Не считать наличие term достаточным
   выполнением object restriction. Не дублировать служебные слова без смысла.
2. Quantity содержит число с единицей. Exclusion = исключаемый объект/условие.
   «Только по документу X» ограничивает включённую область: condition/include,
   document=X, а не exclude самого X. «Кроме X» → exclude X. Смысл решается
   в контексте, без механической замены по одному слову.
3. Поправка заменяет прежнее подтверждённое условие через replace; сохраняются
   остальные условия. Новому вопросу старые ограничения не приписывать.
4. Document/meaning — ровно supplied IDs; labels не ID, если явно не заданы
   как ID frozen manifest. Для file protocol terms strings являются разрешёнными
   legacy meaning IDs; не выдумывать другие. Если группировка meanings в manifest
   не подтверждена, не объявлять их синонимами; uncertain вместо угадывания.
5. Point — SourceRef ровно одного номера, не строка номера и не диапазон,
   объединяющий два пункта. Split нескольких пунктов сохраняет исходное намерение.
6. При неясности назвать предмет через uncertain.source и field; не угадывать.
   Документная неоднозначность предпочтительнее термина по прежнему приоритету.
   Допускается сохранить unresolved task; готовый вызов не выдаётся.
7. Source refs должны обосновывать операцию, объект и ограничения в нужных
   turns. Можно несколько точных фрагментов. Полная цитата не заменяет
   заполненные semantic fields и не оправдывает выдуманное условие.

Добавить не более шести коротких контрастных примеров на синтетических вопросах
о вентиляции/лестницах/освещении, с вымышленными fixture documents. Покрыть
search/all, два пункта, term+object condition, only/exclude, correction,
ambiguous meaning/unsupported. Не использовать электрические формулировки
tuning, реальные case IDs, doc IDs или его номера пунктов. Примеры не входят
в run cases и не увеличивают denominator. Все token refs проверяет offline test.

Repair получает реальные ошибки schema/refs и исходный вопрос. Формулировка
«Preserve semantic decisions» заменяется на «Preserve original user intent;
correct invalid representation». Разделение двух пунктов на две задачи при
ошибке cardinality допустимо; добавление незаказанного намерения нет. По gold
семантические ошибки не отправляются на repair. Не более одного repair и
никакой автоматической смены simple на complex после classifier.

## 5. Что не входит

Не добавлять третий semantic critic/self-reflection LLM call, новый extractor,
LLM judge, новое хранилище, автоматическое исправление смысловых полей кодом,
скрытые hints из gold, новую модель, Polza, поиск, Qdrant, бота или production.
Не расширять словарь по ошибкам одного tuning. Не менять document candidates,
term candidates или вопросы ради достижения метрики. Holdout не открывать.

Если в offline audit обнаружено расхождение prepared input и replay interpretation
значений/кандидатов, сначала доказать parity отдельным тестом. Реальное изменение
входной информации требует нового дизайна эксперимента; не совмещать скрыто с H2.

## 6. Offline tests перед прогоном

- Все текущие совместимые tests. Проверить parser prompt содержит определения
  operation и ownership полей, а не только JSON enum; schema hash не изменён.
- Synthetic examples имеют корректные SourceRef, IDs, cardinality/полярность;
  в prompt нет case IDs/буквальных tuning questions/expected.
- Scorer invariance: разделённая и цельная цитата одного grounding; наличие
  либо отсутствие «?»; ordinary wordform; сохранённая unresolved задача при
  корректном clarification. Negative pairs: другой turn/point/doc, потерянное
  «не», 10 вместо 110, неверная единица, чужое условие/лишний ready task.
- Missing object condition при сохранённом concept остаётся fail; only/exclude
  не взаимозаменяемы. Corrected expected не генерируется из actual.
- Повторные skills/independent task reordering не ломают source/edge/critical
  metrics; absent edge не скрывается unmatched-значением.
- Gate отличает wire_valid от technical_valid; случай с 20 wire/16 technical
  не проходит first-draft threshold.
- Receipt lifecycle: при invalid parser автоматически подготовлен один repair
  request и ожидается receipt attempt2 либо явная transport failure. Отсутствие
  ответа = missing_attempt, не model_repair_failed. C02 воспроизводится как
  invalid first attempt + отсутствующий repair; старые receipts не дополняются.
- Два deterministic replay старых receipts дают одинаковый reconciled baseline.
  Hashes gold/old run неизменны, input rows v3 равны v2 побайтно.

## 7. Новый подписочный протокол

Наследовать §10 v2: два новых workers без fork, Luna gpt-6-luna medium для
classifier/simple, Sol gpt-6-sol medium для complex. Новые run_id/directory,
новые prompt hashes, прежние 20 input rows/order и schema. Не читать gold
в worker; не позволять tools/Internet/repository browsing. Реальный schema
enforcement не заявлять: supports_json_schema=false. Программный endpoint
подписки не подменять API и не создавать в этой итерации.

Host сохраняет полный фактически отправленный message envelope до dispatch
каждой роли/попытки; request hash привязан к receipt, включая repair errors и
raw initial draft. Это исправляет отсутствие repair wrappers в прошлом run.
Хранить наблюдаемые raw final text до validation. Event/server model/usage,
если unavailable, null; не заполнять вымышленными timestamps/event IDs.
Host может измерить свои dispatch/receive clock и записать их как host_clock,
не как provider timestamps. Transcript hash вычислить из сохранённых видимых
envelopes/raw replies, не требовать недоступной внутренней истории сервера.

Persistent workers сохраняют известное ограничение контекста между заданиями;
порядок фиксирован. Сохранять classifier error/неверный маршрут; не заменять
предсказанный класс эталоном. Parser error не вызывает незаписанный retry.

Перед запуском/между batches проверять 5h usage. Не начинать новый batch при
>=90%, остановка с checkpoint при >=95%. Незавершённый receipt не угадывать.
Продолжение после сброса только с теми же hashes. В текущей задаче workers
не запускаются: сначала реализация, независимые tests/critique/offline acceptance.

## 8. Измеримый критерий H2 и окончательный gate

Сначала заморозить reconciled baseline B на 20 старых receipts. Записать old
legacy=7/20 и новый B отдельно; evaluator-only прирост не относится к H2.

H2 считается полезной, если новый semantic plan >=B+4 случая из 20, при нуле
новых критических пропусков относительно baseline и технической валидности
не ниже 19/20. Это критерий прогресса эксперимента, не замена итоговой приёмки.
Если B>16 и +4 недостижимо, критерий заранее переопределить как 20/20; изменение
зафиксировать до sampling. Никакой post-hoc корректировки порога.

Итоговый tuning gate остаётся строгим:

- first technical validity >=19/20, final technical validity 20/20;
- classifier >=18/20 и все 11 размеченных complex правильно направлены;
- полный semantic plan/status >=18/20;
- ноль ready с потерянным намерением, отрицанием, числом/единицей, неверным
  doc_id, unsupported operation или dependent chain;
- все 3 ambiguity правильно уточнены по field/subject/options; <=1 лишнего
  уточнения; metamorphic V03/V06 проходит;
- receipt/request coverage полная, воспроизводимый replay, честная metadata.

Публиковать таблицу переходов per-case: unchanged/recovered/regressed отдельно
для classifier, draft, assembler и scorer. Показать legacy score, reconciled
baseline, new same-evaluator score. Нельзя утверждать восстановление потерянного
смысла, если изменился только evaluator. H2 может дать progress при failed gate;
в таком случае holdout остаётся закрытым.

## 9. Передача исполнителю

1. Read-only audit старых receipts; offline scoring preflight и freeze B.
2. Изменить только инструкции поведения, проверяемые synthetic примеры и
   request/receipt instrumentation. Остальной runtime не менять.
3. Независимые tests, critic, acceptance; freeze v3 directory.
4. Подписочный tuning Luna/Sol и replay; итог passed/failed/paused_limit/blocked.
5. Checkpoint с hashes, достигнутыми результатами и точным следующим действием.

Документ не разрешает запуск holdout и не объявляет гипотезу проверенной.
