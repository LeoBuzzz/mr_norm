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
