# H3: согласовать текст topic и ссылки на источник

2026-10-08. Решение Astra: changes_required; H2 отклонена, разрешена подготовка
узкой H3 по SPEC v3.1. Это не offline acceptance реализации и не разрешение
немедленного sampling. В этой работе меняются только документы.

## Доказательства

Изучены semantic_evaluation_report.json, offline_replay_report.json, фактические
45 receipts и repair envelopes frozen router-v3-h2-tuning-20261008-r2,
reconciled baseline, текущие prompt/validator/assembler и tuning C02 expected.
Holdout не открывался. B=11/20; H2=5/20, classifier=15/20 (complex 10/11),
first technical=15/20, final technical=20/20. Прогресс H2 не подтверждён.

Причина основной деградации — доказанное противоречие инструкции контракту.
router_prompts_v3.py учит «a search topic uses current@5:5» и аналогичным
примерам. router_draft.py требует topic как непустой текст, не разыменовывает
его и непосредственно переносит в query. Поэтому обе модели исполняют
ошибочную инструкцию, validator пропускает locator как текст, а поисковая
тема становится строкой current@4:4. Это не потеря понятия моделью и не
недостаток тяжёлой модели. Синтаксическая валидность скрыла контрактный дефект.

## Причины по классам и случаям

- S01: после repair задача семантически верна; полный plan провален только
  из-за classifier complex вместо simple. S02/S03 также лишний complex;
  V01 — тот же over-routing. D03 ошибочно simple: изменение области документа
  в продолжении не распознано как сложная коррекция ограничения. Raw classifier
  не содержит rationale, поэтому точную внутреннюю причину утверждать нельзя.
  H3 не меняет classifier; эти пять ошибок остаются самостоятельным backlog.
- S02/S03/C03/V01/V03/V06/D02/D03/A02/A03/U01/U02: topic содержит SourceRef.
  Всего 12 случаев, включая пять содержательных регрессий S03/C03/D02/U01/U02.
  Неверный query подтверждён actual plan, а не только score. В U01/U02 сами
  unsupported operations и after сохранены: нельзя называть их потерянными
  только потому, что assignment/dependency metric false после query mismatch.
- S01/C01/C02/C03/A01: все пять first technical errors — literal point
  ('6.25', '1.1', '2.1', '33') вместо SourceRef. Это обратная путаница типов;
  каждый repair исправил её. Ни transport failure, ни missing repair здесь нет.
- V01/V03: в отличие от прежнего run object conditions теперь представлены.
  Сохранять их как улучшение отдельных fields, хотя query locator ломает plan.
  V06 дополнительно теряет concept и ненужно уточняет ВЛ; U03 также требует
  лишнее уточнение вместо deferred. H2 прямо запрещает объединять flat strings
  ВЛ/воздушная линия, хотя gold трактует их как варианты одного понятия.
  Это manifest/prompt limitation, а не доказательство реальной полисемии.
  Исправление relation groups потребует отдельного эксперимента, не H3.
- D02 корректно задаёт replace и новую object condition; провал query — locator.
  D03 кроме locator не содержит требуемого include constraint для «только по».
  Его doc_id сам по себе не заменяет constraint по действующему контракту.
- A01 проходит. A02/A03 правильно задают field/subject/options, но unresolved
  task с query locator не принимается scorer как grounded semantic task.
  Поэтому headline ambiguity=1/3 не означает два неверных набора options.
- C02: два point tasks после repair правильно разделены и имеют правильные
  doc/point. Каждый source намеренно покрывает свой номер. Gold source_refs
  обоих tasks = current@2:9, то есть требует также номер соседнего task.
  Текущий scorer поэтому не засчитывает evidence. Это отдельный дефект
  task-local attribution в измерении, не доказанная потеря второго пункта.
  Не исправлять scorer в H3; сохранить строгий исторический fail и явную
  оговорку. Если он станет единственным препятствием окончательной приёмке,
  оформить отдельную калибровку и пересчитать ВСЕ baseline до нового sampling.
- S04/C01/D01/D04/A01 проходят; это контроль отсутствия регрессий.

## Одна гипотеза

H3: явное разделение literal topic / source references / canonical IDs в
инструкции и fail-closed проверка locator-in-topic устранят системную порчу
query без изменения модели, schema, кандидатов, scorer или семантических правил.
Point/source instructions уточняются только как та же граница представлений.
Не расширяем H3 до исправления classification, term ambiguity или C02 scorer.

Это меньшее изменение, чем новый topic_refs wire-контракт, автоматическое
разыменование строки либо смена модели. Последние варианты скрыли бы
несоответствие или изменили одновременно несколько причин. Сборщик продолжает
получать обычный topic; ни regex-rewrite смысла, ни LLM judge не добавляется.

## Измерение

Same evaluator, B=11, H2=5 сохраняются. H3 useful только при >=15/20 full plan,
first technical >=19/20, final=20/20, отсутствии новых critical regressions
к B и H2. Минимальная цель не снижена до восстановления H2=5. Полный gate
остаётся прежним и может не пройти из-за известных независимых ошибок.
Не обещаем, что одна H3 устранит все blockers. Если useful gate не достигнут,
гипотеза failed; повторный подбор prompts на тех же ответах — новый run/design.

Counterfactual разыменование topic старых H2 ответов допускается только как
отдельный диагностический отчёт, не runtime, не baseline и не новый sampling.
Он не исправляет topic omissions, classifier или неопределённость. H2 report
и receipts никогда не переписываются, новый technical validator к ним не
применяется задним числом.

## R6 replay and gates decision — 2026-10-09

Keep historical H2 replay executable through its immutable runtime snapshot.
The launcher verifies the original H2 manifest/receipt hashes and each pinned
runtime module, then imports only that snapshot while explicitly binding H2 r2,
H2 r1, and the frozen v2 source directory. Coherent envelope tampering remains
rejected by the historical replay validator. Do not adapt the old H2 manifest
to the changed working-tree lifecycle.

Define H3 useful and strict gates as named booleans and require every prescribed
criterion. Useful requires complete case coverage, technical thresholds,
locator-free accepted plans, the semantic threshold, and no new critical
regressions. Strict tuning additionally requires zero ready-plan losses in
intent, negation, number/unit, document identity, and unsupported/dependent-chain
categories. Gate calculation is separate from the semantic scorer and does not
alter gold or comparison rules. R6 is an offline implementation freeze only;
it has no receipts.

R7 is the current unreceipted freeze. Its critical-ready audit preserves the
integer-key representation returned by the unchanged one-to-one semantic task
scorer; JSON display coercion is not used for in-memory gate decisions. Keep R5
and R6 immutable as historical, superseded implementation revisions.

Реализация, тестирование, критика, offline acceptance и новый frozen run
описаны в SPEC v3.1. Автор текущего решения не запускал модели и не менял код.

## R8 provenance decision — 2026-10-09

R8 supersedes the unreceipted R7 implementation freeze without changing R7.
The H2 snapshot launcher is bound only to R8's run identity, manifest, and
`historical_h2_runtime` inventory. It does not fall back to R5. Gold,
reconciled baseline, H2/v2 manifests, and the H2 source inputs/prompts/schema
are loaded from R8 frozen copies or are checked byte-for-byte against the exact
SHA-256 inventory pinned by R8 before replay. The full v2 manifest bytes and
all listed v2 source resources are validated, not just its run id. Replay
evidence records R8 manifest/snapshot identities and hashes. Drift in frozen
evaluation resources, snapshot code, or snapshot resource bindings fails
closed. R8 is an unreceipted offline freeze; no gates or scorer semantics were
changed and no sampling was performed.

## R9 semantic-evaluation decision — 2026-10-09

Preserve R8 and add immutable R9 for the remaining offline-acceptance findings.
The H3 semantic evaluator reads gold and reconciled baseline only from R9's
validated `evaluation/` copies, records their consumed SHA-256 values, and
rejects an arbitrary `gold_path`. Its input validation checks those resource
bytes against R9 and the exact frozen R8 parent. Mutable working-tree path
globals are not used to score cases. The evaluator also obtains the H2 reference
through the R9-bound snapshot launcher, whose manifest pins the original H2
runtime and v2 inputs.

The gate reports separate named no-new-critical-regression predicates against
the frozen reconciled baseline B and replayed H2 results; both are required for
useful and strict success. It computes unnecessary clarifications by comparing
actual `needs_clarification` status with expected status and requires a count
of at most one for the strict tuning gate. Scoring semantics and existing gate
thresholds are unchanged. R9 is an unreceipted offline freeze; no sampling was
performed.

## R10 critical-regression identity decision — 2026-10-09

Preserve R9 and create R10 for the critical-omission identity defect. Compare
stable canonical records, not only omission codes. A record includes `code`,
task/dependency/ready identity fields, and normalized `failed_fields`; transient
nearest-actual-task hints are excluded. Normalize object keys deterministically,
sort/deduplicate failed-field labels, and compare records as a multiset so an
additional omission remains a regression even when another omission has the
same code. Publish the added records with their case ids. Do not alter the
underlying scorer, baseline, H2 replay, or gates. R10 is a new unreceipted
implementation freeze; no sampling is performed.
