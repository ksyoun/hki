"""Keep live Spanish captions on Latin script (audience-facing)."""

from __future__ import annotations

import re
import unicodedata

_EMPTY_QUOTES = re.compile(
    r"[«»]\s*[«»]|\"\s*\"|'\s*'|“\s*”|‘\s*’"
)
_WORD = re.compile(r"\w+", re.UNICODE)


def es_char_allowed(ch: str) -> bool:
    """True for ASCII, Latin letters, Spanish punctuation, generic combining marks."""
    if ord(ch) < 128:
        return True
    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z"):
        return True
    if cat in ("Sm", "Sc", "Sk"):
        return True
    name = unicodedata.name(ch, "")
    if "LATIN" in name:
        return True
    if cat == "Mn" and "COMBINING" in name:
        return True
    return False


def has_latin_letter(text: str) -> bool:
    for ch in text:
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            return True
        if unicodedata.category(ch).startswith("L") and "LATIN" in unicodedata.name(
            ch, ""
        ):
            return True
    return False


def strip_non_latin_scripts(text: str) -> tuple[str, bool]:
    """Drop Hangul / Gujarati / other non-Latin letters. Returns (cleaned, stripped)."""
    if not text:
        return "", False
    kept: list[str] = []
    stripped = False
    for ch in text:
        if es_char_allowed(ch):
            kept.append(ch)
        else:
            stripped = True
    cleaned = "".join(kept)
    cleaned = _EMPTY_QUOTES.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?…])", r"\1", cleaned)
    cleaned = re.sub(r"([«¿¡])\s+", r"\1", cleaned)
    cleaned = cleaned.strip()
    return cleaned, stripped


def latin_caption_words(text: str) -> set[str]:
    """Words used for recombine fidelity — ignore non-Latin tokens."""
    return {
        w.lower()
        for w in _WORD.findall(text)
        if len(w) > 3 and all(es_char_allowed(c) for c in w)
    }
