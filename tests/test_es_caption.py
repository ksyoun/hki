"""Latin-script caption filter."""

from hki.live.es_caption import (
    es_char_allowed,
    has_latin_letter,
    latin_caption_words,
    strip_non_latin_scripts,
)


def test_allows_spanish_punctuation_and_accents():
    sample = "Ñandú, ¿qué más? «usted»… — ¡amén!"
    cleaned, stripped = strip_non_latin_scripts(sample)
    assert cleaned == sample
    assert not stripped
    assert has_latin_letter(sample)


def test_strips_gujarati_tail():
    raw = "se quedó en Siquén,િકેટ"
    cleaned, stripped = strip_non_latin_scripts(raw)
    assert stripped
    assert "િકેટ" not in cleaned
    assert "Siquén" in cleaned


def test_strips_quoted_hangul_and_empty_guillemets():
    raw = "Por encima de Dios «디게»"
    cleaned, stripped = strip_non_latin_scripts(raw)
    assert stripped
    assert "디게" not in cleaned
    assert "«»" not in cleaned
    assert cleaned.startswith("Por encima de Dios")


def test_latin_caption_words_ignore_non_latin():
    words = latin_caption_words("quedó en Siquén િકેટ extra")
    assert "quedó" in words or "quedo" in words or any("qued" in w for w in words)
    assert "siquén" in words or "siquen" in words
    assert not any(es_char_allowed(w[0]) is False for w in words)
