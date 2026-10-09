# Решение об итерации 2 парсера — 2026-10-07

Выбрана H1: убрать из модельного ответа техническую сборку полного плана.
Модель выдаёт semantic draft, код формирует IDs, exact evidence, issues/status,
coverage/default collections. Structured outputs — capability transport,
а не обязательная предпосылка этой архитектуры.

Исследованы records split=tuning трёх отчётов:
router_plan_live_tuning_polza_gpt6_20261007.json,
router_plan_live_tuning_polza_sol_astra_20261007.json,
router_plan_live_tuning_polza_gemini31_20261007.json.
Holdout в этой работе не открывался и не запускался.

Классификация/полный план/ошибки: Luna/Sol 15/0/18, Sol/Astra 18/1/11,
Gemini 17/0/20; каждый показатель имеет знаменатель 20.

Подсчёт финальных технических диагностик (первая оставшаяся ошибка случая,
не число всех проблем в raw ответе):

- Luna/Sol: 6 evidence, 3 массива, 3 issues, 1 clarification, 1 intent/query;
  дополнительно 4 конфликтующих reason_codes classifier.
- Sol/Astra: 6 issues, по 1 evidence/массив/clarification и 2 сочетания
  skill/scope/coverage.
- Gemini: 8 ID задач, 7 evidence, 2 intent/query, 2 scope/coverage enums,
  1 сочетание skill/scope/coverage.

Пример S02 Luna: repair исправляет task_1 на t1, но оставляет depends_on/replaces
null. S01 Gemini после repair сохраняет query=null и evidence строками.
У Sol/Astra S02 принят, другие случаи теряют условия или не проходят issues.
Подорожание модели не устранило общую нагрузку технической сериализации.
Это поддерживает H1, но не гарантирует исчезновения смысловых ошибок.

Следующий выбор пользователя — GPT-6 Luna medium / GPT-6 Sol medium из подписки.
Он заменяет рекомендацию Sol/Astra через Polza в историческом
REPORT_QUESTION_PARSER_MODEL_SELECTION_20261007.md только для нового прогона;
исторические результаты не редактируются. Новая пара ещё не принята.

Текущий build_chat_client поддерживает ollama/polza; subscription endpoint
здесь не реализован и не подтверждён. Exposed collaboration tool имеет model
и reasoning_effort, но не schema enforcement. Спецификация задаёт fallback:
два новых workers без fork истории, immutable inputs/prompts, raw final receipts
и offline deterministic replay. Parent сохраняет receipts; модель не переписывает
их после проверки. История persistent workers, неизвестные physical requests,
usage и actual model metadata честно отражаются в отчёте.

Официальная документация различает ChatGPT subscription sign-in и API key.
Поддержка JSON Schema в OpenAI API не подтверждает её в exposed subscription
transport. Официальный app-server механизм существует, но локальную готовность
нельзя заключать только из документации. Прочитанные через OpenAI Docs источники:

- [Authentication](https://learn.chatgpt.com/docs/auth).
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
- [Codex app-server и ChatGPT plan](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).

Сравнение нового subscription/v2 со старым Polza/v1 имеет transport confound:
оно проверяет пригодность нового решения, а не изолированный причинный эффект.
Контролируемый same-transport A/B здесь не запускать.

Новый контракт и критерии: SPEC_QUESTION_PARSER_STAGE1.md v2.0. V1 сохранена
в SPEC_QUESTION_PARSER_STAGE1_V1_ARCHIVE.md. Код, gold и Polza-отчёты в этой
работе не менялись; модельные eval-прогоны, Qdrant и production не запускались.
