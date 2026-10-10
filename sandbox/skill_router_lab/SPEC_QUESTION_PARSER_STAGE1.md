# Этап 1, итерация 3.1 / H3: однозначный контракт представления полей

2026-10-08. Статус: спецификация для реализации GPT-6 Luna medium.
H2 завершена и отклонена: B=11/20, H2=5/20. Эта версия заменяет указания H2
о следующем эксперименте. Исходная v3.0 побайтно сохранена в
SPEC_QUESTION_PARSER_STAGE1_V3_H2_ARCHIVE.md. Общий PLAN и неизменённые
требования SPEC_QUESTION_PARSER_STAGE1_V2_ARCHIVE.md сохраняются.
Основания: DECISION_QUESTION_PARSER_STAGE1_H3_20261008.md.

## 1. Одна гипотеза и границы

H3: согласование literal topic / SourceRef / canonical ID между prompt и
validator устраняет системное попадание locator в query. H2 prompt прямо
предлагал topic=current@5:5, тогда как runtime воспринимает topic как текст.
12 tuning cases содержат эту ошибку. Пять first technical failures имели
обратную ошибку: point literal вместо SourceRef. Это одна проблема
представления полей, а не две гипотезы про смысл запроса.

Разрешены только: точечная правка parser/repair инструкции о типах полей,
её синтетических примеров, fail-closed проверка topic, тесты и отдельная
H3 freeze/replay обвязка. Не переписывать router и не создавать новый парсер.
Использовать существующие router_draft.py, router_prompts_v3.py и lifecycle.
Версия эксперимента H3 не меняет semantic_draft.v2 / skill_query_plan.v2.

Запрещены: изменение classifier prompt, operation meanings, dialog/ambiguity
policy, term grouping, словаря, candidate IDs, source tokenization, числа
вызовов, моделей/effort, case input/order, gold, scorer и его audit maps.
Не исправлять C02 evidence attribution в этом эксперименте. Не заменять
SourceRef строку её цитатой автоматически; не добавлять topic_refs schema.
Не генерировать новые semantic fields кодом, не копировать terms в conditions.
Не запускать поиск, Polza, API вместо подписки, Qdrant, production, bot,
definition-задачу, holdout. Не добавлять semantic judge/critic LLM call.

## 2. Точные изменения реализации

### 2.1 Parser prompt

Сохранить весь H2 semantic текст, кроме ошибочного указания формата topic.
Добавить компактный нормативный перечень; он применяется и к simple, и к
complex parser:

- topic: обычный непустой текст темы на языке вопроса, например «вентиляции»;
  НЕ SourceRef, НЕ canonical ID, НЕ JSON-строка с locator. Переносить предмет
  и объект, без которых запрос становится шире. Не писать пояснения/ rationale.
- source: массив SourceRef, например ["current@2:5"]. Это grounding операции,
  объекта и условий; literal quotes сюда не помещать.
- point: одна SourceRef на ровно один номер, например "current@3:3";
  literal "4.7" не является допустимым значением point. Сам номер извлекает код.
- terms[].source, conditions[].source, uncertain[].source: SourceRef.
- document и terms[].meaning: supplied IDs, не SourceRef и не произвольный label.
- after: номера предшествующих tasks; replace: supplied condition IDs.

Переписать ТОЛЬКО topic части имеющихся synthetic examples: вместо topic
uses current@5:5 написать topic="вентиляции", source содержит проверенную
ссылку; для освещения аналогично. Вариант quantity/object не должен внушать,
что достаточно удалить объект из topic. Не добавлять tuning формулировки,
case IDs, реальные doc IDs или электрические примеры. Point example должен
явно различать token quote (номер) и wire value (SourceRef). Все ссылки
проверяются против фактического synthetic tokenizer, включая кавычки.

Не менять classifier ради S01/S02/S03/V01/D03. Новые classifier outputs
должны быть получены в sampling, а не скопированы или заменены gold.

### 2.2 Fail-closed validator

Расширить существующую validate_semantic_draft проверку topic до сборки.
Если topic присутствует, оставить текущие требования string/nonempty и
отклонять наличие locator token формата turn_id@digits:digits где-либо в
topic. Pattern нормативно: (?<![\w@])[^\s@]+@\d+:\d+(?!\d).
Он должен ловить current@1:4, turn_1@5:6, неизвестный turn, в том числе
в скобках/кавычках или внутри «тема current@1:4»; границы не требуют, чтобы
turn существовал. Это запрещённое представление, а не попытка его разрешить.
Существующая SourceRef проверка остальных полей остаётся неизменной.

Ошибка DraftValidationError с устойчивым кодом/текстовым маркером
TOPIC_MUST_BE_LITERAL_TEXT и номером task. Не менять ошибочный input и не
пропускать draft к assembler. Не пробовать resolver, не fallback на весь
source и не подставлять guessed topic. Это техническая ошибка и использует
существующий максимум один repair; повторная ошибка завершает case failure.
Обычные числа, двоеточие без @, email без :digits не должны ложно блокироваться.
Схема JSON остаётся побайтно прежней: дополнительная проверка относится к
technical validity, а не заявленному серверному schema enforcement.

Assembler, normalizer, candidate selection, clarification и scoring не менять.
Для topic без locator публичный plan должен совпадать побайтно со старым.
Если неизбежно нужно другое поведение runtime, остановиться с blocker;
не включать его скрыто в H3.

### 2.3 Repair

Сохранить H2 repair policy и одну попытку. Дописать только объяснение
TOPIC_MUST_BE_LITERAL_TEXT: вернуть topic обычным текстом из исходного
запроса; source/point остаются SourceRef. Напомнить границу literal topic /
SourceRef point, не требовать менять выбранные intents/document/meaning.
Repair request содержит исходный frozen input, observed raw draft и реальные
ошибки validator. Семантические ошибки scorer не отправлять модели.

## 3. Историческая воспроизводимость и evaluator

H2 directory, 45 receipts, envelopes, bindings, reports и manifest immutable.
H2 manifest SHA-256:
e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862.
H2 receipts SHA-256:
184b7516277df17e9c9098ba86239c6f9fddb32c0e921625a6a65a45967d7160.
Gold SHA-256:
386e7d682cf5153dd9e5bba266e348eb621fc6cfd6378b2af51c62e4609c14fe.

До изменения validator сохранить вне H2 immutable versioned source snapshot
всех его транзитивных runtime dependencies для исторического replay и hash
inventory. Можно использовать уже существующий проверенный snapshot, если
он полный. Нельзя обходить hash guard или заставлять старый run принимать
новый код. Historical H2 replay остаётся first15/final20/semantic5; baseline B
остаётся11. Новая H3 validation policy не пересчитывает старые technical scores.
При невозможности исторического replay — blocker до sampling.

Новый H3 manifest явно указывает parent H2 manifest hash, новый runtime hash,
prompt hashes и validation_policy=topic_literal_guard.v1. Сохранить текущую
семантику evaluator и audit maps; разрешён только выбор версии validator при
replay по manifest. Отчёт различает historical и new-policy результаты.

Не менять expected/source_refs C02 и не зачитывать его post-hoc. В отчёте
пометить известную task-local evidence measurement limitation. Снижение
порогов или новая калибровка требуют отдельного дизайна до другого sampling.
Опциональный counterfactual topic dereference H2 — отдельная диагностика,
не model improvement, не изменение baseline, не основание объявить pass.

## 4. Обязательные offline проверки

1. Вся релевантная ранее проходившая suite; без запусков внешних скиллов.
2. Literal topic проходит неизменённым для all/search/unsupported; numbers,
   units, negation, object и обычная пунктуация сохраняются. SourceRef-like topic
   отклоняется: valid, unknown turn, out-of-range, embedded, quoted, multiple.
3. После guard assembler не вызывается; один repair допускается, повторный
   locator/missing response/model/transport failure не превращается в success.
4. Prompt example имеет literal topic и валидные source/point, не содержит
   запретных tuning literals/IDs. classifier hash и schema bytes равны H2.
5. Synthetic point literal отклоняется, SourceRef номера проходит; multiple
   points остаются отдельными tasks. Не ослаблять measured quantity rejection.
6. Старые valid literal drafts дают идентичные планы; новая проверка не меняет
   conditions/concepts/status/clarification/DAG и не добавляет semantic repair.
7. Исторические H2 и baseline воспроизводимы в закреплённой runtime версии.
   Все исходные hashes неизменны. H3 empty replay: coverage0/20, complete=false,
   overall gate=false, missing cases перечислены, denominators всегда20.
8. Canonical request reconstruction, coherent hash tamper rejection, role
   selection по observed classifier, repair input binding и lineage tests
   переносятся из r2 без ослабления. runtime dependencies frozen целиком.

Роли: Luna medium реализация; GPT-5.5 medium независимые tests; GPT-5.6 Sol
medium critique; GPT-6 Astra medium offline acceptance. Только после всех
PASS/accept_offline можно отправить frozen H3 subscription requests.

## 5. Frozen subscription sampling

Актуальная implementation freeze revision: `router-v3-h3-tuning-20261009-r10`,
directory `router_subscription_run_tuning_v3_h3_r10_20261009`. r1–r9 являются
unreceipted immutable predecessors/superseded; использовать только r10. Если r10
уже существует, не перезаписывать: выбрать следующий явный revision ID.
Никакой правки H2.

Те же 20 input rows/order, candidates, contexts, token manifests и schema bytes
побайтно, что H2. Workers видят только необходимые inputs/prompts/schema;
никаких gold, reports, decision, case failure hints или репозитория.
Два свежих workers без fork: gpt-6-luna medium classifier/simple и gpt-6-sol
medium complex. Tools/Internet/repository browsing запрещены. Сохранить r2
порядок: classifier batch в frozen порядке, затем predicted parser в том же
порядке, repairs после соответствующего invalid parser согласно lifecycle.
Не менять модель после ошибочной классификации. Не reuse H2 responses.

Транспорт — подписка Codex через host/subagent file protocol, не Polza/API.
Supports_json_schema=false. До dispatch fsync canonical full envelope;
после ответа raw final text до validation, receipt hash и request binding.
Сохранять реальные host_clock и доступные metadata; actual_model/server event/
usage при отсутствии null. Requested model не представлять как проверенный.
Не заполнять отсутствующий ответ сочинённым JSON. Только один technical repair.

На каждом переходе проверить 5h usage: не начинать batch при >=90%; при >=95%
checkpoint и остановка workers. Продолжение с теми же hashes после reset.
Если interrupted workers нельзя безопасно восстановить с тем же протоколом,
фиксировать partial run, не выдавать его за полный и не делать silent retry.

## 6. Точные gates и abort

До sampling: все offline tests, critic PASS, Astra accept_offline, freeze hash
validation, сохранённая историческая воспроизводимость. Иначе blocked.
При hash/input/model/role/schema drift, открытом gold worker, нарушении repair
budget или невозможной provenance — abort invalid_run; не смешивать receipts.
Нельзя редактировать prompts после первого dispatch. Model error оставлять
в знаменателе, не abort ради исключения неудобного случая.

H3 useful требует ВСЕ:
- full semantic plan >=15/20 (B+4; исходный порог не снижен после H2=5);
- first technical >=19/20, final technical20/20;
- ноль новых critical omissions относительно B и H2, отдельные per-case diffs;
- ноль locator-in-query в технически принятых plans; complete coverage20/20;
- два byte-identical substantive deterministic replay.

Это критерий useful, а не окончательная приёмка. Если хотя бы одно условие
не выполнено, H3 failed, сохранить все результаты и остановить tuning; новый
prompt retry требует новой гипотезы/run, а не перезаписи.

Окончательный strict tuning gate НЕ ИЗМЕНЁН:
first technical>=19/20, final20/20; classifier>=18/20 и все11 expected complex;
full semantic plan>=18/20 и status>=18/20; ноль ready с потерянным намерением,
отрицанием, числом/единицей, ошибочным doc_id, unsupported/dependent цепочкой;
все3 ambiguity field/subject/options верны, <=1 лишнее уточнение; V03/V06
metamorphic pass; полная честная provenance. Только при всех условиях
вердикт tuning_pass; holdout всё равно требует отдельного разрешённого этапа.

Отчёт показывает B=11, H2=5, H3 same-evaluator score; classifier отдельно от
parser, wire отдельно от technical, причины repair, literal-topic coverage,
per-case recovered/regressed/unchanged. Task assignment fail не переименовывать
в доказанную потерю каждого dependency/source. Не выдавать fail-closed guards
или counterfactual score за улучшение модели.

## 7. Конкретная работа Luna

Сначала snapshot исторического runtime и доказательство replay; затем только
parser/repair field-format instructions + topic guard; добавить focused tests;
создать H3 freeze/replay version selection с прежним scorer. Не менять gold
и старые frozen artifacts. Передать tests/critic/Astra с diff и hash inventory.
Sampling выполнять только после отдельной offline acceptance, по §5–6.

---

# Этап 1, итерация 3.2 / H4: различать варианты названия и разные значения

2026-10-09. **spec_ready; sampling не разрешён.** Этот раздел задаёт следующий
ограниченный эксперимент после завершённого H3 r10. Разделы H3 выше сохраняются
как история; их запрет менять classifier/alias policy снимается ТОЛЬКО в точно
описанной ниже области. Прочие контракты, пороги и запреты сохраняются.

## H4.1 Основание и одна проверяемая гипотеза

H3 r10: first/final technical 20/20, semantic plan 13/20, classifier 17/20,
status 16/20, ambiguity 1/3; четыре лишних уточнения, ноль новых critical
regressions. Immutable run: router-v3-h3-tuning-20261009-r10, manifest SHA
ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc.

H4: ошибочная инструкция «не считать отдельные candidate strings синонимами»
превращает название и его расшифровку в конкурирующие значения. Единое правило
интерпретации candidates в classifier и parser должно убрать эту искусственную
неоднозначность, сохранив настоящую. Это гипотеза о prompt semantics, а не
разрешение автоматически объединять любые два кандидата или менять словарь.

Ожидаемая область восстановления: V01/V03/V06/U03. Их task semantics в r10
уже верны; сбой — лишнее уточнение/status, у V01/V06 также classification.
Если восстановятся все четыре без регрессий, ожидаемый plan score 17/20.
Это прогноз, НЕ результат и НЕ основание засчитать post-hoc pass.

## H4.2 Точная область реализации для Luna medium

1. Создать отдельную H4 версию prompts на основе router_prompts_v3_h3.py.
   Не редактировать H3 prompts, snapshots, receipts и manifests. Изменить
   только alias/ambiguity policy parser и соответствующее правило classifier.
2. Legacy flat candidates.terms остаются допустимыми точными wire IDs.
   Разные строки сами по себе НЕ доказывают разные смыслы. Когда supplied
   surface и supplied expansion обозначают один известный смысл в контексте,
   не создавать uncertain только из-за двух написаний. Не выводить число
   смыслов из числа строк. Не считать порядок строк или совпадение инициалов
   достаточным доказательством эквивалентности. Не придумывать отсутствующие
   expansions/IDs и не добавлять сведения в candidates.
3. Для однозначного смысла terms[].source остаётся исходным упоминанием;
   terms[].meaning — один точный supplied ID. Если supplied expansion
   однозначно соответствует surface, предпочесть ID expansion; если несколько
   эквивалентных labels и нет единственного предпочтительного expansion,
   допустим точный supplied surface ID. Никаких новых canonical IDs. Исходный
   объект, числа/единицы и исключения остаются отдельными conditions.
4. При реально конкурирующих значениях или недостаточных основаниях для
   разрешения сохранить uncertain. Для legacy clarification сохранить полный
   текущий допустимый набор exact candidate IDs (включая surface, когда он
   является частью этого набора), без omissions/duplicates/extras. Не удалять
   surface из вариантов «ради нормализации». Document IDs разных документов
   не объединяются даже при одинаковой аббревиатуре/названии.
5. Classifier: несколько написаний одного смысла не являются причиной complex.
   Один результат с несколькими условиями, числом/единицей и исключением
   сохраняет simple при отсутствии остальных существующих complex triggers.
   Настоящая смысловая/document ambiguity сохраняет complex. Неподдерживаемая
   операция по-прежнему complex независимо от однозначности её объекта.
6. define/compare/conclude/summarize остаются неподдерживаемыми операциями:
   однозначность термина не превращает define в search или ready. Без другой
   неопределённости прежний assembler выдаёт deferred; при настоящей ambiguity
   сохраняется прежний приоритет clarification. Не менять status кодом.
7. Допускается один небольшой synthetic contrast block в H4 prompt: вымышленная
   аббревиатура с одной ясной расшифровкой; та же аббревиатура с двумя разными
   расшифровками; два документа с одним alias. Не использовать tuning case IDs,
   реальные tuning literals/аббревиатуры/doc IDs. Если примеры содержат SourceRefs,
   проверить их существующим tokenizer. Новый пример не должен заменять прочие
   H3 semantic правила и topic/point type instructions.

Не менять _candidate_sets, _prepare_vocab, словарь, assembler, validator,
schemas, tokenization, scorer, gold, audit maps, runtime status precedence,
методы matching или repair policy. Существующие meaning_groups в runtime уже
есть; их перенос в frozen file protocol — отдельная гипотеза, не часть H4.
Разрешены лишь отдельная H4 prompt версия, минимальное подключение этой версии
к существующему freeze/lifecycle и focused offline tests. Не создавать ещё один
общий parser или новый framework. Если подключение требует semantic runtime
изменений, остановиться с blocker и описать конкретную зависимость.

## H4.3 Известный остаток, который не исправляется скрыто

D03: parser правильно продолжил поиск и сузил область документа; ошибка только
в classifier simple вместо complex. Общая политика scope-changing continuation
в classifier недостаточно явная. Не чинить её в этом эксперименте: это отдельный
триггер, не следствие alias policy. D01 с обычным продолжением на другой номер
не должен автоматически стать complex только из-за наличия истории.

A02/A03: clarification field/subject/options правильны, но оставленный search
task имеет scope=corpus вместо unresolved, поэтому scorer не принимает его как
неисполняемую unresolved task. Это отдельный дефект representation, а не повод
сливать настоящие значения или документы. В H4 эти два plan failures допустимы
как известный остаток useful-эксперимента; они НЕ допустимы для full acceptance.
Сохранить clarification и отсутствие выбора guessed doc/meaning. Не исправлять
evaluator так, чтобы он засчитал текущие corpus-scoped задачи.

## H4.4 Offline gate и точные границы регрессий

Luna передаёт компактный diff, hash inventory и команды проверок. GPT-5.5 Sol
medium независимо запускает focused и relevant suite, GPT-5.6 Sol medium
критикует только H4 diff, GPT-6 Astra medium выдаёт accept_offline или blocker.
Обязательные проверки до нового dispatch:

- prompt не содержит старого запрета на synonyms и содержит обе стороны
  alias/competing-meaning contrast; synthetic примеры не содержат tuning literals;
- схемы, source inputs/order/candidates/contexts и H3 non-prompt runtime bytes
  неизменны; H4 отличается только разрешёнными prompt/обвязкой/test файлами;
- deterministic synthetic drafts через реальный lifecycle: однозначный alias
  сохраняет concept + object/quantity/exclusion; unsupported define остаётся
  deferred; реальные конкурирующие term/document candidates остаются unresolved
  clarification; неизвестный ID отклоняется; один technical repair максимум;
- guards topic literal/point SourceRef, exact one-to-one clarification options,
  canonical critical omission multiset, empty/partial coverage fail-closed,
  resource substitution/tamper и role selection по observed classifier проходят;
- исторические H2 15/20→20/20, B=11, H2 semantic=5 и H3 r10 20/20→20/20,
  semantic=13 воспроизводятся закреплёнными snapshots без ослабления hashes.

Synthetic drafts проверяют совместимость plumbing, НЕ доказывают, что LLM
следует новой инструкции. Ни offline PASS, ни анализ старых ответов не являются
измерением H4 model quality. Этот документ сам sampling НЕ разрешает.

## H4.5 Будущий frozen tuning и критерии решения

Только после всех offline PASS и отдельного Astra accept_offline создать/проверить
новый immutable H4 run с parent r10 SHA и точными prompt/runtime/resource hashes.
Те же 20 tuning cases/order, untouched gold/scorer и исходные inputs. Каждый
case/role/attempt — свежий изолированный подписочный worker с exact envelope:
GPT-6 Luna medium classifier/simple, GPT-6 Sol medium complex. До dispatch
immutable envelope, после ответа raw receipt/binding; не переиспользовать ответы
r10. Requested model не выдавать за verified actual model. Прежний single repair.
Никаких Polza, holdout, Qdrant, production, bot или definition-задачи.

H4 useful требует одновременно: semantic >=15/20; first technical >=19/20;
final technical 20/20; complete coverage 20/20; locator-free accepted plans;
<=1 лишнее уточнение; ноль новых critical regressions отдельно против B, H2
и r10; ноль новых ready critical losses; два идентичных deterministic replay.
Дополнительная проверка границы H4: все 13 r10 plan passes сохраняются, A02/A03
сохраняют верные clarification field/subject/options и не получают guessed
resolution. Из четырёх целевых лишних уточнений должно исчезнуть минимум три
(не более одного остаётся); фиксировать причины каждого plan восстановления.
Сравнение с r10 делать отдельным неизменяющим scorer диагностическим diff.

Strict full tuning gate остаётся прежним: classifier >=18/20 и все 11 expected
complex; semantic >=18/20; status >=18/20; все 3 ambiguity корректны; <=1 лишнее
уточнение; V03/V06 metamorphic pass; прежние technical/provenance/critical gates.
H4 useful при провале full gate — лишь подтверждённая промежуточная гипотеза,
не завершение stage1 и не разрешение holdout. Holdout остаётся закрыт до отдельного
разрешённого этапа. Не снижать пороги, не скрывать D03/A02/A03 и не смешивать
ошибку evaluator task assignment с доказанной потерей всех dependency/evidence.

После первого dispatch никаких prompt edits/retries под тем же manifest. Если
H4 failed, сохранить результаты и прекратить этот эксперимент. Следующая
гипотеза требует нового решения; не расходовать остаток на бесконечные retries.
Проверять лимиты перед каждым batch. По последнему указанию пользователя
2026-10-09 недельный резерв теперь 2%: при weekly used>=98% checkpoint и stop;
не запускать batch, который по оценке может съесть этот резерв. Пятичасовой
порог начала batch остаётся used<90%.

---

# Этап 1, итерация 3.3 / H5: проверка согласованности решения с его основаниями

2026-10-09. Verdict: **spec_ready**. Разрешена только ограниченная offline
реализация ниже; sampling запрещён до отдельного **accept_offline** Astra.
H4 завершён как failed и остаётся immutable. Настоящий раздел дополняет историю,
не меняет scorer, gold, strict gate или разрешения на holdout.

## H5.1 Диагноз и одна гипотеза

H4 r1: technical 20/20→20/20, classifier 19/20 (11/11 expected complex),
status 19/20, semantic tasks 15/20, semantic plan 14/20, одно лишнее уточнение.
V06/U03 восстановлены; D04 потерян: 13 r10 passes превратились в 12 сохранённых.
V01 уже имеет верные tasks/status, но complex вместо simple; V03 одновременно
выбирает значение и уточняет варианты написания этого же значения. Правильный
status/classifier не компенсирует task/evidence/clarification ошибки в full plan.

Одна H5 гипотеза: небольшой заключительный prompt self-check согласованности
решения с уже доступными основаниями уменьшит противоречивые решения и потери
источников. Это один проход перед выдачей JSON, без нового вызова модели,
reasoning-поля, runtime inference, postprocessing или semantic repair. Он
проверяет только (a) основание ambiguity/complexity и (b) полноту source и
conditions для уже выбранных полей. Не менять ontology или алгоритм routing.

D04 raw H4 содержит dialog=correct и только current source, хотя выбранный
doc_id взят из истории; r10 содержит continue и отдельный historical doc span.
Это наблюдаемая вариативность prompt/model output при свежем ответе, совместимая
с недостаточно явным evidence правилом; один run не доказывает стохастическую
причину или причинность alias patch. H5 может предупредить её общим правилом
обоснования наследуемых полей. Нельзя объявить её шумом и исключить из gate.

D03 больше НЕ является только classifier ошибкой: H4 исправил complexity, но
потерял явное scope condition, которое r10 сохранял. Отдельный redesign
scope-changing continuation остаётся вне H5; однако D03 НЕ исключается из
regression guard. Общая проверка сохранения явно заданных ограничений обязана
защитить его, без case-specific инструкции. Если это требует отдельного runtime
или dialog redesign, вернуть changes_required, а не ослаблять critical gate.

## H5.2 Нормативное содержание bounded prompt patch

1. Classifier считает независимые запрошенные результаты/операции, а не число
   найденных пунктов, слов, условий или candidate labels. Один полный перечень
   по одной теме с action/object/quantity/exclusion — один результат и simple,
   если нет существующего complex trigger. Multiple document-point pairs,
   dependencies, реальные ambiguity, corrections и unsupported intents сохраняют
   complex. Не превращать любое наличие истории в complex.
2. Parser проверяет, есть ли реально конкурирующие смыслы перед uncertain.
   Явно названная полная форма, включая обычное склонение, и её установленный
   supplied alias не создают ambiguity только из-за разных строк. Правило
   симметрично для surface→expansion и expansion→surface, независимо от порядка
   candidates. Не создавать программный stemmer/alias table. Выбирать только
   exact supplied meaning ID по H4.2. Нельзя автоматически снимать uncertainty
   лишь потому, что draft уже заполнил meaning: выбранное значение само не
   доказательство. При конкурирующих expansions или недостатке оснований
   сохранять uncertainty и не выдавать предположение за resolution.
3. Для каждого уже выбранного поля убедиться, что task.source включает
   подтверждающие его supplied spans: текущую операцию/target и историческое
   упоминание документа/темы, если поле наследовано. Наличие resolved_doc_id
   не заменяет доступный исторический source span. Не цитировать всю историю
   автоматически; включать релевантный источник, не старый point как новый
   target. Если подтверждения нет, не выдумывать span или документ.
4. Явные restrictions сохранять в conditions независимо от заполненных
   document/topic/terms: scope selection не поглощает «только по ...», concept
   не поглощает object, quantity не теряет unit, exclusion не меняет polarity.
   Новый номер в том же подтверждённом документе без сигнала исправления —
   continue; correct требует реального исправления прежнего условия. Не
   переносить чужие/устаревшие условия в новый запрос; сохранить прежние правила
   correction/replace/dependency, без новых триггеров и inference.
5. Реальные разные doc IDs никогда не объединять по labels. Для настоящего
   term/document clarification сохранять field, subject и полный exact набор
   options без duplicates/extras/omissions; не выбирать guessed meaning/doc.
   A02/A03 corpus вместо unresolved — отдельный representation defect,
   остающийся вне H5. Не убирать retained task ради score и не менять assembler.
   Его сохранение не разрешает full acceptance. U03 остаётся unsupported define
   и deferred при отсутствии настоящей ambiguity.

Допускается один короткий заключительный self-check block в parser и один
согласованный classifier paragraph. Можно уточнить противоречащую формулировку
H4 внутри этих границ и добавить максимум один компактный fictional contrast
block: alias в обе стороны/конкурирующие meanings и inherited-document source.
Все literals/IDs вымышленные, не из tuning. SourceRefs проверяются tokenizer.
Repair prompt, schema и остальные правила H4 сохраняются побайтно, кроме
необходимого размещения этих blocks; не переписывать весь prompt.

## H5.3 Точный allowlist и immutable revisions

Все пути ниже относительно sandbox/skill_router_lab. Реализация может создать
только router_prompts_v3_h5.py, freeze_router_v3_h5.py,
router_subscription_lifecycle_h5.py, prepare_router_subscription_h5.py,
test_router_h5_offline.py, test_router_h5_lifecycle_boundaries.py,
test_router_subscription_h5_coordinator.py. Обвязка — минимальная адаптация H4
для новой идентичности/хешей/версии; evaluator adapter может только привязать
существующий pinned evaluator к H5 identity/lifecycle, без изменения логики.
Не менять существующие implementation/tests. Документы H5 review/acceptance
и append-only checkpoint допустимы отдельно, не как evidence model quality.
Если нужен иной файл или semantic runtime patch — changes_required до правки.

Запрещены изменения assembler, validator, _candidate_sets, _prepare_vocab,
meaning_groups/file protocol, dictionary, tokenizer, schemas, gold, audit maps,
scorer/matching, status precedence, repair budget и исторических reports/runs.
Не вводить case-ID branching, tuning literals, тестовые ответы в runtime.

Первый H5 freeze: router-v3-h5-tuning-20261009-r1, отдельный каталог
router_subscription_run_tuning_v3_h5_r1_20261009. Прямой parent H4 r1 manifest
SHA-256 4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa;
сохранить lineage до r10 ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc.
Новые manifests/resources/envelopes разрешены только в новом H5 каталоге.
Закрепить exact prompt/runtime/resource hashes и отдельный test/hash inventory.
Те же 20 tuning inputs/order/candidates/context/source manifests и schema bytes.
Freeze можно подготовить offline без receipts. Любое изменение manifest-pinned
bytes после freeze требует следующего явного revision и нового offline review;
не перезаписывать r1. После dispatch не менять prompt и не делать silent retry.

## H5.4 Обязательный offline gate и роли

Luna medium пишет код; Sol 5.5 medium независимо проверяет tests; Sol 5.6 medium
делает critique; Astra medium принимает exact frozen revision. Недоступность
указанной модели фиксировать как blocker роли, не заменять молча. До sampling:

- Focused и релевантная ранее проходившая suite PASS; compact diff и hash
  inventory подтверждают allowlist, отсутствие case-specific prompt content и
  неизменность всех неразрешённых ресурсов, включая repair prompt и schema.
- Synthetic drafts через реальный H5 lifecycle/assembler: обе формы одного
  alias дают один concept без лишнего clarification, один all с несколькими
  условиями сохраняет object/action/quantity/unit/exclusion; define deferred.
  Перестановка labels не меняет смысл; несколько разных meanings/doc IDs
  сохраняют точные clarification options, отсутствие guessed resolution.
- Продолжение point lookup цитирует current point и historical document,
  не возвращает старый point и не становится correction лишь из-за нового
  номера. Scope-only continuation сохраняет condition и topic; отдельные
  synthetic correction/new-request contrasts защищают перенос условий.
- Проверять task-local source coverage, не только наличие поля: положительные
  и отрицательные drafts (пропущенный historical source/condition) должны
  различаться существующим scorer; не добавлять validator semantic rejection.
  Отрицательные примеры могут быть technically valid и semantic fail.
- Все старые guards: literal topic/point SourceRef, unknown IDs, measured
  quantity, один technical repair, terminal failure, role selection по observed
  classifier, canonical request/repair input binding, tamper/resource substitution,
  critical multiset identity и empty/partial coverage fail-closed.
- Исторический replay без правки артефактов воспроизводит B=11, H2=5
  (technical15→20), r10 semantic13/technical20 и H4 semantic14/classifier19/
  status19/technical20, включая реальные failures. Два substantive deterministic
  replay совпадают. Empty H5: coverage0/20, complete=false, gate=false.
- Независимые tests PASS, critique PASS и отдельный Astra accept_offline с
  exact manifest SHA обязательны. Offline injected outputs проверяют plumbing,
  а не следование модели prompt; offline quality score не заявлять.

## H5.5 Измеримый target, regression guards и запрет sampling

Прогноз H5 — semantic18/20, classifier20/20, status20/20, ноль лишних уточнений:
V01/V03 и D04 плюс восстановленная task semantics D03; A02/A03 остаются
representation failures. Это проверяемый target, НЕ результат и НЕ новый gate.
H5 useful сохраняет порог >=15/20 и ВСЕ H4.5 technical/provenance/critical
условия; повышение score само по себе недостаточно. Обязательно сохранить все
14 H4 plan passes и восстановить все 13 r10 passes; ноль новых critical records
отдельно против B, H2, r10 и H4, ноль ready critical losses. В частности D03
не освобождается от critical guard. Все11 expected complex сохраняются,
V03/V06 metamorphic pass; A01/A02/A03 field/subject/options сохранены;
V06/U03 не регрессируют. Два byte-identical replay, coverage20/20, first>=19/20,
final20/20, locator-free accepted plans, <=1 лишнее уточнение обязательны.

Strict tuning gate НЕ МЕНЯЕТСЯ: semantic>=18/20, classifier>=18/20 и все11
expected complex, status>=18/20, все3 ambiguity корректны, <=1 лишнее уточнение,
metamorphic/technical/critical/provenance gates. Известные A02/A03 failures
нельзя засчитать по одним field/options или объявить full pass при score18.

До отдельного accept_offline — никаких новых model samples/Polza/Qdrant/
production/bot/holdout. После acceptance допустим только новый frozen tuning
по прежнему подписочному протоколу: свежий isolated worker на case/role/attempt,
Luna medium classifier/simple и Sol medium complex, exact immutable envelopes,
raw receipts/bindings, один technical repair, без gold/reports/repository у
worker. Requested model не выдавать за verified actual. Не reuse H4 responses.
Лимиты: старт batch при 5h used<90%, stop при >=95% либо weekly>=98%, резерв2%.
При failure сохранить весь run и остановить гипотезу; дальнейший retry требует
новой версии/решения. Holdout не открывается даже при tuning_pass.

## H5.6 Нормативная коррекция r4 после independent critique r3

2026-10-10. Verdict: **spec_ready**, только для подготовки и проверки offline
successor. R3 остаётся **changes_required**; его test report с 12 failures не
является PASS по H5.4. Этот append-only раздел уточняет trust boundary и строго
расширяет allowlist ниже; H5.1–H5.5, semantic/critical thresholds и запрет sampling
сохраняются. Ни это решение, ни созданный release lock не являются accept_offline.

### H5.6.1 Trust root без самоподтверждения кода

Хеш в изменяемом Python module, manifest или соседнем inventory — не независимый
корень доверия. Добавление validator в manifest при embedded exact manifest SHA
создаёт цикл и НЕ является решением. Для r4 применяется внешний release anchor:

1. Сначала фиксируются окончательные code/tests/resources и создаётся manifest,
   содержащий их SHA-256, включая freeze validator, coordinator, lifecycle и
   весь локальный import/dependency closure, влияющий на validation/assembly/
   scoring/replay. Затем создаётся `release_lock.json`: exact manifest SHA,
   exact `hash_inventory.json` SHA, полный path→SHA-256 inventory executable
   closure/tests/replay adapter, predecessor tree inventory и identity/lineage.
   Lock НЕ включает свой SHA и НЕ меняется ради последующей review. Ни code, ни
   manifest не содержат SHA самого lock или собственного будущего manifest.
2. Независимый reviewer проверяет байты и записывает SHA-256 lock, SHA-256
   stdlib-only launcher `release_router_h5_r4.py`, exact manifest SHA и run ID
   в свой результат в доверенном канале coordinator/reviewer. Astra при
   accept_offline повторяет эту точную тройку hashes в acceptance message и
   acceptance artifact. Авторитетен сохранённый внешний message/record, не
   изменяемая копия markdown/JSON в checkout. Локальный acceptance документ —
   только зеркало. До acceptance независимый test reviewer может задать такой
   anchor исключительно для synthetic offline tests; это не dispatch approval.
3. Доверенный host сначала сравнивает actual launcher bytes с внешним SHA
   стандартным SHA-256 инструментом, не импортируя candidate Python modules.
   Проверенный launcher получает expected lock SHA из внешнего record, проверяет
   lock и все bytes до первого candidate import, затем вызывает выбранный
   validator/coordinator/lifecycle из проверенного closure. Нельзя получать
   expected SHA из run directory, его manifest, Python constant или unverified
   local acceptance mirror. Нет fallback при отсутствующем/неверном anchor.
4. Все три supported release entry points проходят один этот preflight; прямой
   запуск/import candidate API без проверенного host context не является
   release verification и должен fail closed в supported entry points. Python
   object/token не объявляется unforgeable security boundary. Host удерживает
   проверенный checkout неизменяемым до завершения процесса, фиксирует import
   paths и исключает стороннее local module shadowing; либо повторно проверяет
   bytes непосредственно перед использованием. Не запускать проверку в уже
   monkeypatched процессе. Freeze preparation, synthetic test и release-use
   режимы должны быть различимы; preparation не выдаёт release authorization.

Threat model: противник/ошибка может согласованно заменить run manifest,
inventory, lock, candidate validator/coordinator/lifecycle и launcher в checkout
до старта. Он не может заменить внешний reviewer/acceptance message, host hash
tool/interpreter/dependencies или менять process/memory после host verification.
Эта доверенная база перечисляется с версиями/путями в acceptance. Подмена самой
доверенной базы либо arbitrary monkeypatch после verification вне обещания; при
невозможности обеспечить эти предпосылки acceptance blocked, а не self-auth claim.

Обязательные subprocess negatives на disposable copies: (a) manifest transport
и inventory согласованно изменены; (b) тот же manifest плюс изменённые expected
constants/validator в candidate code; (c) обновлены также candidate lock/hash
maps; (d) изменён launcher; (e) missing/wrong external anchor; (f) resource или
local imported dependency изменены. Сохранённый внешний test anchor одинаков
для originals и substitutions. Для validator/coordinator/lifecycle отклонение
до candidate import/dispatch/write подтверждается sentinel и нулём side effects.
Оригинал проходит. Подмена самого trusted anchor не считается negative PASS:
это другая разрешённая release identity, а не доказательство tamper resistance.
Отдельные lineage unit tests могут подавать изменённые данные за hash boundary,
но не заменяют внешний preflight negative. Нужен exact inventory membership,
не только проверка оставленных в attacker-controlled map записей.

### H5.6.2 Минимальная коррекция H4 fixture debt

Разрешено изменить только fixture setup и ошибочное empty assertion в трёх
существующих H4 test files, перечисленных ниже. Все 13 test instances, их
behavioral assertions и parametrization сохраняются; 12 failures должны стать
PASS в общей suite. Удаление, skip, xfail, deselection, suppression и исключение
из PASS запрещены. Старая empty проверка переносится на disposable copy.

Для lifecycle/coordinator synthetic dispatch тест создаёт отдельную копию H4
под pytest tmp_path, проверяет resolved destination вне original run, затем
убирает ТОЛЬКО receipts.jsonl, request_envelopes.jsonl и
receipt_envelope_bindings.jsonl из этой копии. До/после подтверждаются одинаковые
manifest/resource bytes и пустые три evidence streams копии. Original H4 не
чистить и не перефризить. Дополнительно проверяется actual populated run:
receipts_present=true, ровно 40 receipts/40 envelopes/40 bindings, exact SHA
каждого файла из independently reviewed release lock, valid key/hash bindings,
и прежний отказ повторного start/role/attempt на populated disposable copy.
Counts без exact bytes недостаточны. Старые test bytes сохраняются в r4 evidence
до изменения; report даёт mapping прежних test node IDs к тем же assertions.

### H5.6.3 Исполняемая граница D03 и source/condition

В новых r4 tests обязательны injected drafts через настоящий
FileSubscriptionRunnerH5R4 → существующие validator/assembler → неизменённый
`router_subscription_eval_v3.compare_case`. D03 input и expected берутся из
замороженных tuning/evaluation bytes, только в тестах. Positive draft сохраняет
continue, all/comprehensive, inherited literal topic, выбранный exact doc ID,
current scope condition и task-local current+historical evidence. Проверяются
assembled values и full semantic plan PASS. Negative меняет ровно omission
scope condition, оставляя topic/doc/evidence; technical validation остаётся
успешной без repair, conditions и full semantic plan scorer дают FAIL. Отдельный
negative удаляет historical source того же task: scorer source coverage FAIL,
а соседний task с этим source не может компенсировать потерю. Нельзя добавлять
semantic rejection в validator или считать presence prompt substring покрытием.

D04 current-only/complete inherited-document drafts также проходят lifecycle,
assembler и scorer, сохраняя новый point и continue без ложного correction.
Synthetic explicit correction и independent new-request contrasts исполняются
через lifecycle/assembler, проверяют dialog/conditions и отсутствие переноса
устаревших условий; expected может быть test-local, scorer остаётся прежним.
Не менять gold ради PASS. Если положительный D03 невозможно провести через
неизменённый runtime, вернуть changes_required для отдельного решения.

### H5.6.4 Immutable lineage и воспроизводимый historical replay

Новая identity: `router-v3-h5-tuning-20261010-r4`, каталог
`router_subscription_run_tuning_v3_h5_r4_20261010`. Direct parent — H5 r3,
SHA-256 `2617500135233dd4d0903e8e6c3bda7e668acc55d993f3d6fe64ff17f428bdf7`.
R3→r2 SHA `0c63752b3711ba44351cc673b3befd0e0accbe60623a8de6d982345c2ecce194`,
r2→r1 SHA `c4819b3e9b661ce0dfcf83788823da4b6b7a7fe54555af81cce385bfa88d77fd`;
actual H4 SHA `4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa`,
r10 SHA `ebca3c43947ab7361369bf9a59eff93789c565ce4f0f32dfb274b0b614ee4cdc`,
H2 SHA `e0fb87c8b6dc2864fec9204c4089e3ae2a265f7f77b2f88e8216627093c7c862`.
Legacy erroneous lineage fields внутри r1/r2 не исправлять: отдельно записать
их original contents и corrected actual ancestry. Не объявлять старые fields
верными ради chain validation. R4 inputs/order/schema/prompts/evaluation и H2
snapshot resources побайтно равны r3; новая prompt гипотеза здесь не разрешена.

Release lock закрепляет full existing file inventory r1/r2/r3 и H4 (включая
manifest, inventories, frozen resources, historical snapshots и H4 raw evidence).
При каждом supported preflight проверяются full tree membership/hashes и все
доступные historical resource maps, не только parent manifest. Evidence trees
не получают новых файлов. R1/r2/r3 остаются без receipts/envelopes/bindings;
на r4 filesystem тоже проверяется отсутствие всех трёх, а empty replay обязан
инспектировать их и fail closed на unexpected evidence, не возвращать константу.

Старые inventories ссылаются на разные поколения live H5 code/tests, которые
не хранятся внутри r1/r2. Нельзя сравнивать их с r4 code, переписывать inventories
или утверждать full historical code validation по одним resources. Для каждого
такого entry report указывает verified archived bytes либо unavailable, с exact
old hash. Найденные originals можно архивировать в новом r4 evidence только
после exact hash match; восстановленный похожий код не оригинал. Неполные старые
rejected H5 revisions используются только как immutable historical data, не как
доверенный executable runtime или proof прежнего PASS. Это не waiver r4 closure:
все выполняемые r4/H4/H2/r10 code/tests/dependencies должны реально существовать
и быть закреплены. Текущие r3 code/tests и original H4 tests архивируются до
правок. Любое изменение predecessor data после anchor блокирует release.

Новый `replay_router_h4_for_h5_r4.py` — только воспроизводимый offline adapter
существующих pinned r10 replay/scorer к pinned H4 identity/validator/lifecycle,
без правки evaluator logic и artifacts. Он запускается отдельным процессом,
проверяет/документирует все переназначенные globals/import bindings (technical
и semantic RUN_DIR/RUN_ID/validate_frozen, validate_raw_h3, assemble_plan_h3,
semantic replay_h3 и frozen gold/baseline paths); H2 остаётся pinned. Нельзя
оставить неизвестный скрытый in-process monkeypatch как единственное evidence.
Report сохраняет точную CLI, interpreter/dependency versions, input/closure
hashes, canonical serialization и SHA-256 полных substantive outputs двух запусков
в свежих процессах. Нормализация для сокрытия различий запрещена. Для H4 оба
выхода одинаковы: technical20, semantic14, classifier19, status19 со всеми
actual failures; H2 technical15→20/semantic5, B11 и r10 technical20/semantic13
также воспроизводятся дважды. Digest не подставлять прогнозом заранее.
Replay использует read-only original evidence, отключает запись bytecode в
historical trees; outputs идут в disposable/evidence destination нового r4.
Сначала получить canonical replay outputs, затем финализировать manifest и lock:
outputs не включают будущий lock SHA или своё собственное hash поле. Независимый
reviewer повторяет replay и сравнивает с уже закреплёнными outputs без перезаписи.

### H5.6.5 Точный расширенный allowlist, gates и роли

Все пути относительно sandbox/skill_router_lab. В рамках r4 разрешены ТОЛЬКО:

- Новые `freeze_router_v3_h5_r4.py`, `router_subscription_lifecycle_h5_r4.py`,
  `prepare_router_subscription_h5_r4.py`, `release_router_h5_r4.py`,
  `replay_router_h4_for_h5_r4.py`: identity/trust/preflight/replay binding changes
  выше; никакой semantic runtime logic. Имя lifecycle class —
  `FileSubscriptionRunnerH5R4`.
- Новые `test_router_h5_r4_offline.py`, `test_router_h5_r4_lifecycle_boundaries.py`,
  `test_router_subscription_h5_r4_coordinator.py`, `test_router_h5_r4_trust.py`.
- Только H5.6.2 fixture/assertion refactor в `test_router_h4_offline.py`,
  `test_router_h4_lifecycle_boundaries.py`, `test_router_subscription_h4_coordinator.py`.
- Новый r4 run directory с frozen resources, manifest, hash_inventory,
  `release_lock.json`, `evidence/predecessor_files_sha256.json`,
  `evidence/original_tests/`, `evidence/predecessor_runtime/` и
  `evidence/replay/`. В evidence разрешены только exact archived originals,
  hash/provenance inventories, replay command metadata и canonical outputs.
  Никаких model receipts/envelopes/bindings в этом offline run.
- Этот append-only spec, `DECISION_QUESTION_PARSER_STAGE1_H5_R4_20261010.md`,
  новые `REPORT_QUESTION_PARSER_H5_R4_INDEPENDENT_TEST_20261010.md`,
  `CRITIQUE_QUESTION_PARSER_H5_R4_20261010.md`,
  `ACCEPTANCE_QUESTION_PARSER_H5_R4_ASTRA_20261010.md`; checkpoint append
  coordinator по прежнему разрешению, вне данной specification role.

Существующие H5 r1/r2/r3 modules/tests/prompts, все старые frozen runs/reports,
H4 implementation, common runtime/scorer/gold/schema/tokenizer не меняются.
Tests/preflight temporary artifacts допускаются только в disposable tmp dirs.
Новые manifests/tests не ретроактивно переписывают pins предыдущих revisions.
Любая правка frozen r4 bytes после выпуска lock требует successor revision,
новый anchor и заново независимые reviews. Review reports и external acceptance
создаются после lock и не включаются в него: их provenance идёт из внешнего
reviewer record, что устраняет hash cycle.

Общий regression command включает все suites из r3 independent report, новые
четыре r4 suites и исправленные три H4 suites без deselection; PASS обязателен,
без исключения fixture debt. Отдельно обязательны external-anchor negatives,
D03/D04 behavioral contrasts, precursor preservation и reproducible replay.
Все H5.4 guards и H5.5 quality/critical gates остаются conjunctive.
Роли прежние: Luna medium implementation; Sol 5.5 medium independent tests;
Sol 5.6 medium critique; Astra medium exact release acceptance. Недоступность
роли — blocker, не тихая замена. Нужны independent tests PASS, critique PASS и
отдельный Astra accept_offline с manifest/lock/launcher SHA из trusted record.
До этого sampling запрещён. Никаких Polza/holdout/Qdrant/production/bot.
