"""Deterministic, isolated router prototype for narrow sandbox skills."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from router_contracts import RouterPlanConfig, SkillQueryPlan
from router_planning import plan_skill_query

TASK_TYPES = ("exact_point", "all_mentions", "applicability", "corpus_point_search", "definition_search")
CORPUS_SEARCH_ROUTE = "corpus_point_search"

_POINT = re.compile(r"\b(?:пункт(?:а|е|у|ом)?\s*)?(\d+(?:[._]\d+)+(?:\([а-яa-z0-9]+\))?)\b", re.I)
_POINT_SIMPLE = re.compile(r"\bпункт(?:а|е|у|ом)?\s+(\d+)\b", re.I)
_POINT_BEFORE_WORD = re.compile(r"\b(\d+(?:[._]\d+)*)(?:\s*\([а-яa-z0-9]+\))?\s+пункт(?:а|е|у|ом)?\b", re.I)
_DOC_HINTS = (
    ("правил по охране труда", "Правила по охране труда"),
    ("правила по охране труда", "Правила по охране труда"),
    ("правил переключений", "Правила переключений"),
    ("правила переключений", "Правила переключений"),
    ("инструкции по предотвращению", "Инструкция по предотвращению и ликвидации аварий"),
    ("инструкции по ликвидации аварий", "Инструкция по ликвидации аварий"),
    ("инструкции по переключениям", "Инструкция по переключениям"),
    ("правилах технической эксплуатации", "Правила технической эксплуатации"),
    ("правилах ликвидации нормального режима", "Правила предотвращения развития и ликвидации нарушений нормального режима электрической части энергосистем и объектов электроэнергетики"),
    ("35фз", "Федеральный закон № 35-ФЗ"),
    ("ппрф 861", "Постановление Правительства РФ № 861"),
    ("постановлении 861", "Постановление Правительства РФ № 861"),
    ("постановлению 861", "Постановление Правительства РФ № 861"),
    ("постановления 861", "Постановление Правительства РФ № 861"),
    ("постановления 1172", "Постановление Правительства РФ № 1172"),
    ("птэ", "Правила технической эксплуатации"),
    ("пуэ", "ПУЭ"),
    ("п у э", "ПУЭ"),
    ("ппуэ", "ПУЭ"),
)


@dataclass(frozen=True)
class Route:
    primary: str
    routes: tuple[str, ...]
    uncertainty: str
    explicit_doc_hint: str | None
    point_ref: str | None
    negative_constraints: tuple[str, ...]
    followup: bool
    rationale: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["routes"] = list(self.routes)
        result["negative_constraints"] = list(self.negative_constraints)
        result["rationale"] = list(self.rationale)
        return result


def route_query(query: str, *, dialog_context: str | None = None) -> Route:
    text = " ".join(query.lower().replace("ё", "е").split())
    context = (dialog_context or "").lower().replace("ё", "е")
    rationale: list[str] = []
    doc_hint = next((label for needle, label in _DOC_HINTS if needle in text), None)
    if doc_hint is None and context:
        doc_hint = next((label for needle, label in _DOC_HINTS if needle in context), None)
    point_match = _POINT.search(text) or _POINT_SIMPLE.search(text) or _POINT_BEFORE_WORD.search(text)
    point_ref = point_match.group(1) if point_match else None
    if doc_hint is None and re.search(r"\bпостановлени\w*\s+86\b", text):
        doc_hint = "Неоднозначное упоминание постановления № 86; требуется уточнение"
        rationale.append("ambiguous_document_mention")

    negative = []
    for match in re.finditer(r"\bне\s+([\w-]+(?:\s+[\w-]+){0,3})", text):
        phrase = match.group(1).strip(" .,?!;:")
        if phrase and phrase not in {"все", "надо", "нужно", "можно"}:
            negative.append(phrase)
    if "не птэ" in text or "не правила" in text:
        negative.append("исключить ранее упомянутый документ; использовать исправление пользователя")

    has_all = bool(re.search(r"\b(все|всё|всех|всему|вытяни|вытащи|перечень|полный перечень)\b", text))
    context_has_all = bool(re.search(r"\b(все|всё|всех|вытяни|вытащи|перечень|полный перечень)\b", context))
    inherited_exhaustive = bool(context and context_has_all and re.search(r"\b(только текст|текст этих пунктов|этих пунктов)\b", text))
    has_all = has_all or inherited_exhaustive
    asks_docs = bool(re.search(r"\b(в каких документах|во всех документах|перечень документов|документами регулируется|какими документами регулируется|чем регулируется|в законах есть|поищи во всех документах)\b", text) or re.search(r"\bперечень\s+(?:всех\s+)?документ\w+\b", text))
    asks_definition = bool(re.search(r"\b(?:что\s+такое|кто\s+такой|что\s+означает|дай(?:те)?\s+определени\w*|определени\w+\s+(?:термин\w*|поняти\w*)|все\s+определени\w+)\b", text))
    asks_apply = bool(re.search(r"\b(можно ли|должна? ли|должен ли|должны ли|должен|должна|должны|что должно|что должен|кто должен|кто определяет|кто обязан|на основании чего|какие требования|должны его иметь|должна разрабатывать)\b", text) or re.search(r"\bгде\s+должн\w+\b", text))
    asks_requirement_inventory = bool(doc_hint and "какие требования" in text)
    asks_exact = bool(point_ref and (re.search(r"\b(как звучит|покажи|найди|а пункт|дай текст)\b", text) or re.search(r"\bпункт\w*\b", text)))

    routes: list[str] = []
    if asks_definition:
        routes.append("definition_search")
        rationale.append("explicit_definition_lookup")
    elif asks_docs:
        routes.append(CORPUS_SEARCH_ROUTE)
        rationale.append("broad_scope_corpus_search")
    else:
        if asks_exact and not has_all:
            routes.append("exact_point")
            rationale.append("explicit_point_reference")
        if has_all:
            routes.append("all_mentions")
            rationale.append("exhaustive_or_comprehensive_wording")
        if asks_apply:
            routes.append("applicability")
            rationale.append("obligation_or_condition_question")
        if asks_requirement_inventory:
            routes.append("all_mentions")
            rationale.append("requirements_inventory_within_explicit_document")

    if not routes:
        # A known document can use scoped retrieval. Unknown-document topics
        # stay deferred until a separate broad-search design is available.
        routes = ["applicability"] if doc_hint else [CORPUS_SEARCH_ROUTE]
        rationale.append("conservative_fallback")

    # Canonical order retains multi-route requests while selecting a stable primary.
    order = {name: index for index, name in enumerate(("definition_search", "exact_point", "applicability", "all_mentions", CORPUS_SEARCH_ROUTE))}
    routes = sorted(set(routes), key=order.__getitem__)
    followup = bool(context) or bool(re.match(r"^(а\s+)?(пункт|а пункт|а с|то же самое|только текст|не птэ|не контролирующий)\b", text))
    if followup:
        rationale.append("dialog_followup_or_correction")
    if len(routes) > 1:
        uncertainty = "medium"
    elif doc_hint or point_ref or asks_apply or asks_docs or has_all or asks_definition:
        uncertainty = "low"
    else:
        uncertainty = "high"
    if CORPUS_SEARCH_ROUTE in routes:
        uncertainty = "high"
    if "требуется уточнение" in (doc_hint or ""):
        uncertainty = "high"
        rationale.append("ambiguous_document_mention")
    return Route(routes[0], tuple(routes), uncertainty, doc_hint, point_ref, tuple(dict.fromkeys(negative)), followup, tuple(rationale))
