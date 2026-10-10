# H5 — независимая гипотеза после H4 r1

2026-10-09. Verdict: **spec_ready**. Sampling запрещён до отдельного
**accept_offline** Astra для exact H5 revision. Изменены только этот decision
и append-only H5 section в SPEC_QUESTION_PARSER_STAGE1.md.

Основания: последние H4 sections checkpoint, H4/H4.5 spec и decision,
semantic reports H4 r1 и H3 r10; raw receipts и pre-dispatch envelopes/bindings
V01/V03/D04. Остальные указанные случаи проверены по actual semantic drafts
и per-case score обоих reports. Никаких live samples или изменения результатов.

H4 достиг classifier19/status19 и убрал три ложных уточнения, но full plan
равен14: восстановлены V06/U03 и потерян D04. V01 имеет верные tasks/status,
но ошибочную complexity. V03 правильно выбрал supplied meaning и сохранил
ограничения, однако одновременно уточнил два написания того же смысла.
A02/A03 сохранили правильные field/subject/options, но их retained search
task остаётся corpus-scoped вместо unresolved; это отдельная representation
проблема, и classifier/status не могут компенсировать её в scorer.

D04 выбрал верный inherited document и новый point, но raw source содержит
только current, а dialog равен correct. В r10 был continue плюс historical
document span. Это prompt/model output variance, а не обнаруженный deterministic
runtime defect; одного сравнения недостаточно для доказательства причины.
Общий prompt guard должен требовать релевантные источники всех inherited полей
и реальный сигнал исправления для correct. Не подавлять критическую регрессию
как «случайность», не заполнять source программно.

Диагноз D03 обновлён: в H3 был classifier-only failure; H4 классифицирует
complex верно, но теряет явное ограничение области. Поэтому прежний текст
«D03 вне scope» нельзя использовать как разрешение принять новую critical
потерю. Отдельное изменение политики continuation вне scope, но восстановление
conditions общим правилом и нулевые critical regressions обязательны.

Выбрана одна ограниченная гипотеза: заключительный evidence-consistency
self-check в classifier/parser перед JSON. Complexity требует самостоятельного
complex trigger, plurality полного перечня им не является; uncertainty требует
действительно конкурирующих смыслов, а явная полная форма и её установленный
alias в обоих направлениях — один смысл; каждое выбранное поле сохраняет
источник и независимые ограничения. Self-check не добавляет вызов, schema поле,
semantic repair, reasoning output, новый словарь или runtime inference.

Защита настоящей ambiguity: разные expansions и разные doc IDs не склеиваются;
недостаточное основание сохраняет clarification со всеми exact options.
Заполненный meaning сам по себе не доказывает однозначность. A02/A03 не чинить
удалением tasks или изменением scorer/assembler; их failures остаются видимыми.

Нормативный allowlist и gates — H5.3–H5.5 spec: только новые H5 prompts,
freeze/lifecycle/coordinator adapters и три focused test файла; отдельный
immutable H5 r1 каталог с H4 manifest parent
4a02b0a3ea9c4fd860eed38814e44e5f5d1002bca4a5dd9ea0033c72bb2e7caa.
Исторические bytes, inputs/schema, repair prompt, scorer/gold/runtime semantics
не меняются. Любой pinned-byte change требует successor revision и review.

Target: semantic18, classifier20, status20, лишних уточнений0 из20; это прогноз.
Useful>=15 сохранён вместе со всеми technical/provenance/critical gates,
сохранением14 H4 passes и восстановлением13 r10 passes. Ноль новых critical
records против B/H2/r10/H4; D03 не исключается. Strict gate не ослаблен;
даже semantic18 с прежними A02/A03 не означает tuning_pass. Holdout закрыт.

Роли: Luna medium код; Sol 5.5 medium независимые tests; Sol 5.6 medium critique;
Astra medium acceptance. Offline suite, synthetic lifecycle/evidence contrasts,
historical reproducibility, hash inventory, critique PASS и exact-revision
accept_offline обязательны перед любым dispatch. Нет Polza/Qdrant/production/
bot/holdout, модельных retries или sampling в рамках настоящего решения.
