# Решение: следующая tuning-итерация — 2026-10-08

Выбрана H2: сделать смысл операций и ownership полей явными в instructions,
сохранив schema/assembler/models/input data. V2 решил основную проблему формы
JSON (20/20 wire), но короткий prompt преимущественно перечисляет schema и
«preserve conditions», не объясняя search/all, двойную роль term/object condition
и «только по X» как include. Это минимальное изменение поведения, не новый parser.

Проанализированы report router_subscription_eval_tuning_v2_20261008.json,
44 tuning receipts, frozen tuning inputs и tuning gold. Ни holdout, ни новые
модельные вызовы не использовались. Старые данные не редактировались.

## Разбор всех 13 несовпадений

- S01: после repair правильный doc/point. Два evidence spans покрывают намерение
  и документ, но evaluator требует один цельный gold span, включая «из».
  Основной финальный miss — evaluator; первая ошибка document label вместо ID
  также свидетельствует о недостаточно понятной инструкции wire references.
- S02: model выбирает all вместо search. Repair исправляет meaning reference,
  но не исправляет semantic operation. Это реальная ошибка модели; prompt
  перечисляет enums без достаточного объяснения границы search/all.
- C02: classifier ошибочно simple; parser объединяет два пункта в один
  SourceRef. Validator правильно отклоняет. В receipts нет attempt2. Это
  model/cardinality failure плюс недостающая repair-попытка протокола, а не
  доказанный неуспешный ответ repair-модели.
- V01: term ЛЭП сохранён в concepts, но отсутствует object restriction и
  объект убран из topic. Реальное нарушение текущего контракта; не называть
  полной потерей слова, которое осталось в concepts. Prompt не объясняет,
  почему объект нужен как constraint независимо от словарного term.
- V03/V06: число и отрицание сохранены. Объект сохранён в concepts/query, но
  object constraint отсутствует. Representation omission по действующему
  контракту; требуется ясная instruction, не auto-copy в assembler/ослабление gold.
- D01: правильное продолжение и точный пункт. Evaluator требует конечный «?»,
  которого нет в actual evidence quote. Это scoring artifact, не потеря условия.
- D02: корректные correction/replace и объект подстанции; query использует
  «подстанциям», которого нет в коротком списке gold wordforms. Это ложный
  lexical mismatch, а не доказанная потеря темы. Наличие exclude старого объекта
  отдельно от include нового не означает одновременное включение обоих.
- D03: document выбран правильно, но «только по ПУЭ» записано exclude ПУЭ.
  Настоящая смысловая ошибка polarity/kind. Отдельная область document не
  оправдывает противоречивый constraint.
- A01: документ не угадан, уточнение и point intent сохранены. Несовпадение
  tasks=[] с разрешённой unresolved task и падеж «постановления» в subject —
  дефекты оценки/legacy migration. Нельзя учить parser удалять намерение ради
  эталона. Canonical option 861 остаётся предложением, не выбранным документом;
  задачу нельзя выпускать ready с 861 вместо 86.
- A02: field/options/subject верны; miss главным образом из-за expected.tasks=[]
  при сохранённой ожидающей уточнения задаче. Область corpus у этой задачи
  не подтверждена, пока документ не уточнён; не считать её исполняемой.
- A03: classifier simple, parser не сообщает неоднозначность АВР, хотя frozen
  terms содержит конкурирующие расшифровки. Model/prompt miss. Manifest даёт
  плоские term strings, не явные relation groups: это presentation limitation,
  но изменение входной информации в H2 не включено. Не считать их синонимами.
- U03: parser верно возвращает define/deferred; ошибка только classifier simple.
  Legacy expected.operation=unknown объединяет unsupported intents — опасность
  оценщика, который считает любую skill=null эквивалентной. Не менять правильный
  define в unknown ради совпадения.

Итого: пять кандидатов на scorer-only исправление S01/D01/D02/A01/A02;
восемь содержательных/маршрутных failures S02/C02/V01/V03/V06/D03/A03/U03.
Это аналитическая атрибуция, не новый измеренный score. Исправленный baseline
должен быть отдельно вычислен frozen evaluator без изменения gold.

## Ограничения измерений и подготовки

Source coverage 8/20 устанавливается false при неуспешном task matching, поэтому
не доказывает 12 source failures. Только S01/D01 имеют task match, но отдельный
evidence mismatch. Dependency metric тоже зависит от task matching и включает
пустые edges; её 10/20 нельзя читать как долю восстановленных реальных цепочек.

Report отмечает first-draft threshold=true по wire=20, хотя спецификация
требует technical>=19 и измерено technical=16. Это ошибка gate wiring.
Critical omissions используют поиск единственного task по skill и могут
ложно обвинять корректные повторные skills; нужны общие assignment правила.

Assembler исключений не дал. Неправильная polarity D03 пришла от модели и была
честно перенесена; явного доказанного runtime assembler crash в отчёте нет.
Schema validation должна оставаться строгой. Нет основания исправлять её
принятием двух point refs или label вместо canonical ID.

Полные repair envelopes и server metadata старого run недоступны. Нельзя
приписать старым receipts проверенный prompt или серверный actual_model.
Новый host должен сохранять наблюдаемые request wrappers до dispatch и raw
ответы до validation; неизвестные поля остаются null, не придуманными данными.

## Измеримый исход следующей итерации

Сначала общий scorer preflight и replay старых receipts дают baseline B;
этот evaluator замораживается до новых ответов. H2 полезна при new>=B+4/20,
без новых critical regressions и final technical>=19/20. Полный tuning pass
по-прежнему требует >=18/20 semantic, все critical safeguards, final technical
20/20 и правильный complex routing. Прирост только от scorer не засчитывается H2.

Роли неизменны: подписочные GPT-6 Luna medium classifier/simple, GPT-6 Sol
medium complex; два новых eval workers, no fork, frozen prompts/inputs,
raw receipts и deterministic replay. Polza/holdout/production запрещены.

Подробная реализация и gates: SPEC_QUESTION_PARSER_STAGE1.md v3.0.
V2 сохранена в SPEC_QUESTION_PARSER_STAGE1_V2_ARCHIVE.md. В этой работе изменены
только документы; product code, gold и receipts не менялись.
