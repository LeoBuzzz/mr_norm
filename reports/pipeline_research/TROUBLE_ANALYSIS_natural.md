# Natural corpus — trouble analysis (nat10, excl. removed)

- Corpus: `corpus_50_natural.json` (42 items)
- Eval: `eval_results_natural10.json` — 7 cases after excluding removed
- Removed from corpus: case_002, case_003, case_004, case_022, case_029, case_031, case_041, case_044

## Summary

| Metric | Value |
|--------|-------|
| Mean score | 5.86 |
| Chunk hit | 4/7 |
| Doc hit | 4/7 |
| Refusals (score=1) | 3 |

### Failure taxonomy

- **ok**: 4
- **refusal_no_gold**: 3

### Routing modes on failures (score<7)

- `regulation_scope_hybrid`: 2
- `document_lookup_hybrid`: 1

## Case-by-case (failures first)

### case_001 (eval `case_001`) — score **1**

- **Type:** requirement / easy
- **Class:** `refusal_no_gold`
- **Chunk hit:** False | **Doc hit:** False
- **Routing:** `document_lookup_hybrid` | **Retry:** False
- **Resolved doc:** (none)
- **Early resolver:** False

**Q:** Когда нужно рассчитать показатели технико-экономического состояния объектов электроэнергетики за прошедший год?

**Gold:** О комплексном определении показателей технико-экономического состояния объектов электроэнергетики, в p.4

**Answer (preview):** В предоставленных фрагментах отсутствует информация о сроках расчёта показателей технико-экономического состояния объектов электроэнергетики за прошедший год.…

**Judge:** Фактический ответ утверждает, что информации нет, в то время как эталон содержит конкретные сроки (не позднее 31 марта) и орган предоставления данных (Минэнерго). Это прямое противоречие и отсутствие ключевых фактов.

**Top retrieved docs:**
- Об энергосбережении и о повышении энергетической эффективности и о внесении изменений в от
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- О функционировании розничных рынков электрической энергии, полном и (или) частичном ограни
- Об утверждении профессионального стандарта "Работник по осуществлению функций диспетчера в

**Key warnings:**
- `Confidence score (0.52) is below threshold (0.55). The top candidate 'doc_d4825ce2546b78bc' appears relevant but may not fully specify timing requirements. Consider verifying with additional sources.`
- `llm returned no verified document; doc_name filter not applied`
- `conditional fallback skipped: no strong deterministic signal`
- `intent_routing:document_lookup_hybrid:tools=payload,vector`

---

### case_002 (eval `case_005`) — score **1**

- **Type:** requirement / easy
- **Class:** `refusal_no_gold`
- **Chunk hit:** False | **Doc hit:** False
- **Routing:** `regulation_scope_hybrid` | **Retry:** False
- **Resolved doc:** (none)
- **Early resolver:** False

**Q:** Разрешен ли доступ из интернета в технологические сети связи, используемые для дистанционного управления объектами электроэнергетики?

**Gold:** Об утверждении дополнительных требований по обеспечению безопасности значимых объектов критической и p.9

**Answer (preview):** В предоставленных фрагментах отсутствует информация о разрешении или запрете доступа из интернета в технологические сети связи, используемые для дистанционного управления объектами электроэнергетики. …

**Judge:** Фактический ответ утверждает, что информация отсутствует, в то время как эталонный ответ содержит четкий запрет. Это прямое противоречие и полное отсутствие ключевых фактов.

**Top retrieved docs:**
- Об энергосбережении и о повышении энергетической эффективности и о внесении изменений в от
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- О функционировании розничных рынков электрической энергии, полном и (или) частичном ограни
- Об утверждении профессионального стандарта "Работник по осуществлению функций диспетчера в

**Key warnings:**
- `llm returned no verified document; doc_name filter not applied`
- `conditional fallback skipped: no strong deterministic signal`
- `intent_routing:regulation_scope_hybrid:tools=payload,vector`

---

### case_006 (eval `case_009`) — score **1**

- **Type:** procedure / hard
- **Class:** `refusal_no_gold`
- **Chunk hit:** False | **Doc hit:** False
- **Routing:** `regulation_scope_hybrid` | **Retry:** False
- **Resolved doc:** (none)
- **Early resolver:** False

**Q:** Кто и как должен предоставлять доступ к фрагментам цифровых информационных моделей объектов электроэнергетики их владельцам?

**Gold:** Об утверждении Порядка раскрытия (предоставления) цифровых информационных моделей электроэнергетичес p.11

**Answer (preview):** В предоставленных фрагментах отсутствует информация о том, кто и как должен предоставлять доступ к фрагментам цифровых информационных моделей объектов электроэнергетики их владельцам. Фрагменты касают…

**Judge:** Фактический ответ утверждает, что информация отсутствует, в то время как эталонный ответ содержит конкретную норму о роли Системного оператора и ссылку на порядок предоставления доступа. Это прямое противоречие.

**Top retrieved docs:**
- Об энергосбережении и о повышении энергетической эффективности и о внесении изменений в от
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- Об утверждении профессионального стандарта «Работник по осуществлению функций диспетчера в
- О функционировании розничных рынков электрической энергии, полном и (или) частичном ограни
- Об утверждении профессионального стандарта "Работник по осуществлению функций диспетчера в

**Key warnings:**
- `Confidence score of the primary candidate (doc_415fc25295ca6182) is 0.52, below the threshold of 0.55. The question is specific, but the candidate's relevance is borderline. No clear point number is mentioned. Selected_catalog_ids left empty to avoid incorrect narrowing.`
- `llm returned no verified document; doc_name filter not applied`
- `conditional fallback skipped: no strong deterministic signal`
- `intent_routing:regulation_scope_hybrid:tools=payload,vector`

---

## Root causes (synthesis)

1. **Doc resolution gap** — без названия акта в вопросе planner часто не ставит `doc_id`; retrieval уходит в generic «электроэнергетика».
2. **Mis-routing `document_lookup_hybrid`** — вопросы про сроки/требования классифицируются как document_lookup → payload-first без doc filter → шум.
3. **Refusal при пустом final evidence** — final_answer v4 отказывает, даже когда gold doc мог быть в wide pool за rank>25.
4. **Retry не срабатывает** — retry требует resolved doc; при natural query retry_rate=0%.
5. **Chunk hit ≠ good score** — case с chunk в pool но score 2: final_answer игнорирует релевантный chunk среди шума.
