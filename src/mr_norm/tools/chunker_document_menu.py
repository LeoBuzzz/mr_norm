"""Load `chunker_document_menu.json` and resolve human-readable `doc_kind` / `authority` for chunk payloads.

Эвристика по JSON — детерминированно и без сети. Опциональная лёгкая LLM для спорных случаев
вынесена за пределы этого модуля (можно вызывать снаружи и подменять поля до `build_document_chunks`).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_MENU_PATH = Path(__file__).resolve().parent.parent / "data" / "chunker_document_menu.json"


@lru_cache
def load_chunker_document_menu() -> dict[str, Any]:
    return json.loads(_MENU_PATH.read_text(encoding="utf-8"))


def internal_doc_kind_to_payload_label(internal: str) -> str:
    """Соответствие внутреннего кода чанкера (`resolve_doc_kind`) → строка `doc_kind` в payload для LLM.

    Внутренние коды остаются английскими (law, order, …), чтобы не ломать `extract_doc_number` и тесты.
    Значения для payload задаются **только** явными строками в `chunker_document_menu.json` — без подстановок в коде.
    """
    menu = load_chunker_document_menu()
    raw = menu.get("internal_to_payload") or {}
    mapping: dict[str, str] = {k: v for k, v in raw.items() if isinstance(v, str)}
    key = (internal or "").strip()
    if key not in mapping:
        raise ValueError(
            f"internal_doc_kind {internal!r} отсутствует в internal_to_payload "
            f"(chunker_document_menu.json). Допустимые ключи: {sorted(mapping)!r}"
        )
    out = mapping[key].strip()
    if not out:
        raise ValueError(
            f"internal_to_payload[{key!r}] пусто — задайте явную непустую строку в chunker_document_menu.json."
        )
    return out


def _canonicalize_known_short_forms(authority: str) -> str:
    a = (authority or "").strip()
    if not a:
        return ""
    menu = load_chunker_document_menu()
    preferred: dict[str, str] = menu.get("authority_preferred_form") or {}
    if a in preferred:
        return preferred[a]
    low = a.lower()
    for alias, full in (menu.get("authority_aliases") or {}).items():
        if alias in low:
            return full
    for m in sorted(menu.get("ministries") or [], key=len, reverse=True):
        ml, al = m.lower(), a.lower()
        if ml in al or al in ml:
            return m
    return a


def resolve_payload_authority(
    metadata: dict[str, str],
    internal_doc_kind: str,
    extract_authority_fn,
) -> str:
    """Приоритет: явный `metadata.authority` → `extract_authority(doc_reg|approving_act)` → дефолты меню.

    `extract_authority_fn` — обычно `extract_authority` из chunker (инъекция, чтобы не импортировать циклично).
    """
    menu = load_chunker_document_menu()
    base = (metadata.get("authority") or "").strip()
    if base:
        return _canonicalize_known_short_forms(base)

    doc_reg = (metadata.get("doc_reg") or "").strip()
    approving = (metadata.get("approving_act") or "").strip()
    extracted = (extract_authority_fn(doc_reg) or extract_authority_fn(approving) or "").strip()
    if extracted:
        return _canonicalize_known_short_forms(extracted)

    payload_kind = internal_doc_kind_to_payload_label(internal_doc_kind)
    defaults: dict[str, Any] = menu.get("default_authority_by_payload_kind") or {}
    dval = defaults.get(payload_kind)
    if isinstance(dval, str) and dval.strip():
        return dval.strip()

    # Только ПУЭ: без явного органа в тексте известно утверждение Минэнерго (7‑е изд. и фрагменты).
    # Обычные приказы без извлечённого органа не заполняем «Минэнерго» — иначе ложные срабатывания.
    if internal_doc_kind == "pue":
        fb = (menu.get("pue_fallback_authority") or menu.get("order_fallback_authority") or "").strip()
        if fb:
            return fb
    return ""
