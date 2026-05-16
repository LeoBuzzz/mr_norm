from __future__ import annotations

import re


def normalize_catalog_text(value: str) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()
