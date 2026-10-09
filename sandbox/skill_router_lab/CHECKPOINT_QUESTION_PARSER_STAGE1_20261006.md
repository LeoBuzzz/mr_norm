# Контрольная точка парсера вопросов — 2026-10-06

## Почему работа остановлена

Пользователь задал порог: не опускать остаток пятичасового лимита ниже 5%.
После реализации, тестирования и критики использовано 92% окна, осталось 8%.
Ожидаемый сброс: 2026-10-07 03:03 МСК. Astra acceptance и live LLM не
запускались, чтобы сохранить обязательный запас.

## Распределение и выполненная работа

- Код: GPT-6 Luna, medium. Реализованы контракты, подготовка контекста и
  словаря, classifier → simple/complex parser, строгая проверка, один repair,
  dry-run API, eval CLI и 40 случаев tuning/holdout.
- Тесты: GPT-5.5 Sol, medium. Добавлено 20 независимых контрактных тестов;
  найден и исправлен дефект самозависимости шага.
- Критика: GPT-5.6 Sol, medium. Усилены skill/scope/coverage, grounding,
  неоднозначности, trace, prompt boundaries, eval и gold-набор.
- Приёмка: GPT-6 Astra, medium — ещё не запускалась.

Live LLM, скиллы поиска и Qdrant не вызывались. Production не изменялся.

## Текущее состояние проверки

- 66 тестов проходят.
- 40 gold cases проходят усиленную проверку структуры и provenance.
- Gold SHA-256:
  `205ba97aa0adfb5e7ce80dd9a103b3a3dbf898c8fc28536e3f3649a562acd7fd`.
- Предупреждение pytest о невозможности записать `.pytest_cache` не влияет на
  результат; запускать с `-p no:cacheprovider`.

## Открытые замечания критика

1. `compare_case` ещё не проверяет содержание `task.query`, `intent_text`,
   полный набор `concepts/variants` и конкретность уточнения. Это позволяет
   теоретически пропустить потерю темы при совпавших skill/scope/evidence.
2. Production `DialogContext` не содержит стабильных turn_id и подтверждённых
   условий; полноценная коррекция сейчас доказана только на snapshot-контексте.
3. `pending_clarification` в публичном API не имеет отдельной runtime-схемы.
4. Из 40 случаев 34 синтетические; в набор уже добавлены 6 реальных вопросов
   и metamorphic-пара, но содержательную достаточность должна оценить Astra.
5. Отдельного adversarial-набора prompt injection пока нет.
6. Runtime provider и конкретные light/heavy модели не выбраны; live gate имеет
   статус `live_not_run`.

## Следующий запуск после сброса

1. Проверить пятичасовой лимит. Не начинать работу, если остаток не больше 5%.
2. Прочитать `PLAN_QUESTION_PARSER.md`, `SPEC_QUESTION_PARSER_STAGE1.md`, этот
   checkpoint и итог критика в текущем чате.
3. Запустить GPT-6 Astra medium для независимой оценки приёмки. Попросить её:
   - оценить соответствие плану и спецификации, достаточность offline gate и
     40-case набора;
   - проверить шесть замечаний критика и определить обязательные исправления
     до live tuning;
   - дать verdict `accept_offline`, `changes_required` или `blocked`, не
     запускать live LLM/Qdrant и не менять код самостоятельно.
4. Если Astra требует узкие исправления, вернуть их GPT-6 Luna medium, затем
   повторить GPT-5.5 Sol tests и GPT-5.6 Sol critique только для изменённой
   области. Не открывать holdout результат ради настройки.
5. После `accept_offline` согласовать/выбрать runtime provider и точные модели.
   Затем выполнить tuning и ровно один holdout live run по спецификации.
6. На каждом переходе снова проверять лимит. При остатке <=5% обновить этот
   checkpoint, остановить агентов и перенести следующий недостающий шаг.

## Команды воспроизведения offline gate

```powershell
python -m pytest -p no:cacheprovider sandbox/skill_router_lab/test_router_planning.py sandbox/skill_router_lab/test_router.py sandbox/skill_router_lab/test_skill_extensions.py sandbox/skill_router_lab/test_skills.py tests/test_abbreviations.py -q
python sandbox/skill_router_lab/run_router_plan_eval.py
```

Не изменять definition-задачу и её контрольные точки: она ведётся параллельно
в том же каталоге. Существующее грязное дерево содержит пользовательскую и
чужую работу; не делать reset/checkout и не коммитить всё дерево целиком.

## Обновление 2026-10-07 после первого сброса

Выполнена первая Astra offline acceptance: `changes_required`. Четыре исходных
блока исправлены несколькими узкими циклами Luna → Sol 5.5 tests → Sol 5.6
critique. Последняя полная независимая проверка до финального узкого исправления:
84 tests, 40 fixtures, critic `ready_for_astra`, SHA gold:
`698a355494ea3f8c784c75f915553be91439cff3a73fad68e4c6bd8c96ded9a5`.

Повторная Astra приняла все прежние блоки, но нашла один дефект открытого
pending clarification (`options=[]`). Luna исправила его: свободный пункт и
документ закрывают уточнение только по валидному parser result, grounded в
текущем ответе; документ обязан быть canonical doc_id из frozen candidates;
«не знаю» и отрицание оставляют уточнение; closed options не изменены.

Независимый Sol 5.5 адресный прогон последнего fix: 8 passed, 45 deselected.
Полный набор непосредственно перед этим fix проходил; после fix Luna получила
53 tests и fixture gate 40/40. Live LLM/Qdrant/production не запускались.

Работа остановлена при остатке около 10%, чтобы гарантированно не пересечь
пользовательский порог 5%. После следующего сброса нужны только:

1. GPT-5.6 Sol medium — короткая критика последнего `options=[]` fix без
   расширения scope; адресные тесты или read-only inspection.
2. Если `pass`, GPT-6 Astra medium — финальная повторная offline acceptance
   только этого исправления и итоговый verdict `accept_offline` или blocker.
3. При `accept_offline` обновить `REPORT_QUESTION_PARSER_STAGE1.md` и этот
   checkpoint. Остановиться перед live gate: runtime provider и конкретные
   light/heavy модели пользователь ещё не выбрал.
4. Не запускать live LLM, Qdrant или production и не открывать holdout для
   настройки. При остатке <=5% снова остановиться.

## Финальный статус offline-этапа — 2026-10-07

После второго сброса Sol 5.6 нашёл неоднозначность свободных номеров и
документов. Исправления прошли повторные циклы Luna → Sol 5.5 → Sol 5.6.
Открытый point parser теперь требует ровно один уникальный номер, поддерживает
целые и составные номера, не зависит от разделителя и исключает числа с
известными единицами, включая дробные и слитные формы. Свободный документ
требует один canonical doc_id из frozen candidates. Closed options сохранены.

Финальный verdict GPT-6 Astra medium: **accept_offline**.

Финальная проверка root:

- `117 passed in 1.88s`;
- `fixtures_valid`, 40 cases;
- SHA `698a355494ea3f8c784c75f915553be91439cff3a73fad68e4c6bd8c96ded9a5`;
- `live_not_run=true`.

Подробности: `REPORT_QUESTION_PARSER_STAGE1.md`.

Offline-этап завершён. Автоматическое возобновление больше не требуется.
Следующий шаг заблокирован только выбором runtime provider и точных моделей
для ролей `light_model` и `heavy_model`. До такого выбора не запускать tuning
или holdout live gate. Production, Qdrant и бот не изменялись.
