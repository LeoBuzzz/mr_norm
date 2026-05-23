"""Стабильные ключи реестра нормативных документов (тип_номер_дата)."""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def norm_type(s: object | None) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).casefold()


def parse_date_cell(val: object | None) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.match(
        r"^(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        day_s, mon_s, y_s = m.group(1), m.group(2).lower(), m.group(3)
        mo = MONTHS_RU.get(mon_s)
        if mo:
            try:
                return date(int(y_s), mo, int(day_s))
            except ValueError:
                return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def format_adoption_date_iso(d: date | None) -> str:
    if d is None:
        return ""
    return d.isoformat()


def format_adoption_date_display(d: date | None, fallback: str = "") -> str:
    if d is not None:
        return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"
    return fallback.strip()


def date_key(d: date | None) -> str:
    if d is None:
        return "nodate"
    return f"{d.day:02d}{d.month:02d}{d.year:04d}"


def translit_slug_token(s: str) -> str:
    s = str(s).strip().lower()
    repl = {"н": "n", "Н": "n", "с": "s", "С": "s", "т": "t", "Т": "t", "р": "r", "Р": "r"}
    out: list[str] = []
    for ch in s:
        if ch in repl:
            out.append(repl[ch])
        elif ch.isascii() and (ch.isalnum() or ch in ".-"):
            out.append(ch.lower())
        elif ch.isdigit():
            out.append(ch)
    return "".join(out)


def compress_alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", translit_slug_token(s), flags=re.IGNORECASE)


def gost_number_slug(num_raw: str, is_gostr: bool) -> str:
    s = str(num_raw or "").strip()
    if is_gostr:
        s = re.sub(r"(?i)^р\s*", "", s).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace(".", "p")
    return translit_slug_token(s) or "x"


def federal_law_slug(num_raw: str) -> str:
    s = str(num_raw or "").strip()
    m = re.match(r"(?i)^(\d+)\s*-\s*фз\s*$", s)
    if m:
        return m.group(1)
    m = re.match(r"(?i)^(\d+)\s*-\s*фз", s)
    if m:
        return m.group(1)
    m = re.match(r"(?i)^(\d+)", s)
    return m.group(1) if m else compress_alnum(s) or "x"


def ras_slug(num_raw: str) -> str:
    s = str(num_raw or "").strip()
    m = re.match(r"(?i)^(\d+)\s*-\s*р\s*$", s)
    if m:
        return m.group(1)
    m = re.match(r"(?i)^(\d+)\s*-\s*р", s)
    if m:
        return m.group(1)
    m = re.match(r"(?i)^(\d+)", s)
    return m.group(1) if m else compress_alnum(s) or "x"


def pr_slug(num_raw: str) -> str:
    s = str(num_raw or "").strip()
    s = re.sub(r"(?i)-ст\s*$", "", s).strip()
    s = re.sub(r"(?i)-с\s*$", "", s).strip()
    token = translit_slug_token(s)
    token = re.sub(r"[^0-9a-z]+", "", token)
    return token or "x"


def doc_type_prefix(doc_type: str, num_raw: str) -> str:
    t = norm_type(doc_type)
    num_s = str(num_raw or "").strip()
    if re.match(r"(?i)^рд\b", num_s):
        return "rd"
    if t == "приказ":
        return "pr"
    if t == "постановление":
        return "pprf"
    if t == "гост":
        if re.match(r"(?i)^р\s", num_s):
            return "gostr"
        return "gost"
    if t == "федеральный закон" or ("федеральный" in t and "закон" in t):
        return "z"
    if t == "распоряжение":
        return "ras"
    return "doc"


def build_doc_key(doc_type: str, num_raw: object | None, date_val: object | None) -> str:
    num_s = "" if num_raw is None else str(num_raw).strip()
    prefix = doc_type_prefix(doc_type, num_s)
    d = parse_date_cell(date_val)
    dpart = date_key(d)

    if re.match(r"(?i)^рд\b", num_s):
        rest = re.sub(r"(?i)^рд\s*", "", num_s).strip()
        nslug = compress_alnum(rest) or "x"
        return f"{prefix}_{nslug}_{dpart}"

    if prefix == "z":
        nslug = federal_law_slug(num_s)
        return f"{prefix}_{nslug}_{dpart}"
    if prefix == "ras":
        nslug = ras_slug(num_s)
        return f"{prefix}_{nslug}_{dpart}"
    if prefix == "gostr":
        nslug = gost_number_slug(num_s, True)
        return f"{prefix}_{nslug}_{dpart}"
    if prefix == "gost":
        nslug = gost_number_slug(num_s, False)
        return f"{prefix}_{nslug}_{dpart}"
    nslug = pr_slug(num_s)
    return f"{prefix}_{nslug}_{dpart}"
