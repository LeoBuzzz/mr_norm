"""Pictures that occur inside RTF cells, recognized by the SHA-1 of their bytes.

Kodeks writes a superscript, a Greek letter or a sign as a tiny GIF/PNG instead of a
character. The hash is the first 12 hex digits of SHA-1. Unlisted pictures stay images:
a wrong guess would replace the original, so only shapes checked against nearby text
are listed here.
"""

from __future__ import annotations

# sha1/12 -> (characters, role). role "sup" is a superscript, "base" is on the line.
GLYPHS: dict[str, tuple[str, str]] = {
    # Superscripts after units: «мм» + this picture is мм², мм³, …
    "b47c8ab6cc68": ("2", "sup"),
    "d90f32d18e71": ("2", "sup"),
    "92b2528b75ba": ("3", "sup"),
    "a84cefc5a267": ("3", "sup"),
    "4d75535fe77e": ("1", "sup"),
    "87823f9577ef": ("1", "sup"),
    "b1f82aa1b3a5": ("4", "sup"),
    "1c80882d2645": ("4", "sup"),
    "90c3f4ece0d7": ("4", "sup"),
    "06bfb4f743fb": ("4", "sup"),
    "7fad96caf54d": ("5", "sup"),
    "f9c5500a507f": ("5", "sup"),
    "f098d189c059": ("6", "sup"),
    "8763046138bb": ("6", "sup"),
    "d6c27c04d61e": ("7", "sup"),
    "c78cc5617e71": ("9", "sup"),
    # «tg» + this picture is tgδ. «α = 0,75, β = 0,25». «9,1 ± 0,1».
    # ПУЭ 1.9.1: удельная эффективная длина пути утечки, λэ.
    "d8a242a79460": ("λэ", "base"),
    "73f443613262": ("δ", "base"),
    "a55873e3c50b": ("α", "base"),
    "12b3d3a0202c": ("β", "base"),
    "b70f121bf73e": ("±", "base"),
    # Методика готовности к ОЗП: «Ф / П ≥ 0,5», «Ф/П ≤ 0,01», сумма по позициям.
    "1e60b7e70976": ("≥", "base"),
    "57846562daad": ("≤", "base"),
    "2689b053c569": ("Σ", "base"),
    "1a438f9416fc": ("1", "sup"),
}


def glyph_text(sha: str) -> str | None:
    item = GLYPHS.get(sha)
    return item[0] if item else None


def glyph_role(sha: str) -> str | None:
    item = GLYPHS.get(sha)
    return item[1] if item else None

