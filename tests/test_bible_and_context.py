"""Unit tests for Bible API error classification and context helpers."""

from __future__ import annotations

import httpx

from hki.live.bible_api import (
    BibleFetchErrorKind,
    ParsedReference,
    classify_fetch_error,
)
from hki.live.context import format_context_display, format_context_for_system
from hki.live.session import LiveSession


def _ref() -> ParsedReference:
    return ParsedReference(
        book_ko="마태복음",
        book_slug="mateo",
        book_es="Mateo",
        chapter=1,
        verse_start=1,
        verse_end=1,
    )


def test_ref_label_and_verse_param():
    single = _ref()
    assert single.ref_label == "Mateo 1:1"
    assert single.verse_param == "1"

    rng = ParsedReference(
        book_ko="마태복음",
        book_slug="mateo",
        book_es="Mateo",
        chapter=1,
        verse_start=1,
        verse_end=3,
    )
    assert rng.ref_label == "Mateo 1:1-3"
    assert rng.verse_param == "1-3"


def test_classify_http_404_is_reference():
    req = httpx.Request("GET", "https://example.test/nvies/mateo/1/1")
    resp = httpx.Response(404, request=req)
    failure = classify_fetch_error(_ref(), str(req.url), httpx.HTTPStatusError(
        "404", request=req, response=resp
    ))
    assert failure.kind == BibleFetchErrorKind.REFERENCE


def test_classify_http_503_is_transient():
    req = httpx.Request("GET", "https://example.test/nvies/mateo/1/1")
    resp = httpx.Response(503, request=req)
    failure = classify_fetch_error(_ref(), str(req.url), httpx.HTTPStatusError(
        "503", request=req, response=resp
    ))
    assert failure.kind == BibleFetchErrorKind.TRANSIENT


def test_classify_http_401_is_fatal():
    req = httpx.Request("GET", "https://example.test/nvies/mateo/1/1")
    resp = httpx.Response(401, request=req)
    failure = classify_fetch_error(_ref(), str(req.url), httpx.HTTPStatusError(
        "401", request=req, response=resp
    ))
    assert failure.kind == BibleFetchErrorKind.FATAL


def test_classify_timeout_is_transient():
    failure = classify_fetch_error(
        _ref(), "https://example.test", httpx.TimeoutException("timeout")
    )
    assert failure.kind == BibleFetchErrorKind.TRANSIENT


def test_classify_value_error_is_reference():
    failure = classify_fetch_error(_ref(), "https://example.test", ValueError("bad"))
    assert failure.kind == BibleFetchErrorKind.REFERENCE


def test_format_context_for_system_includes_nvi():
    block = format_context_for_system(
        {
            "sermon_summary": "Resumen breve",
            "outline": ["Intro"],
            "terminology": [{"ko": "은혜", "es": "gracia"}],
            "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
            "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía…"}],
            "style_notes": "voseo",
        }
    )
    assert "Resumen breve" in block
    assert "Mateo 1:1" in block
    assert "gracia" in block
    assert "voseo" in block


def test_format_context_display_omits_verse_bodies():
    display = format_context_display(
        {
            "sermon_summary": "S",
            "outline": [],
            "terminology": [],
            "bible_books": [],
            "style_notes": "",
            "bible_references": ["Mateo 1:1"],
            "bible_es_source": "bible_api",
            "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "secreto"}],
        }
    )
    assert display is not None
    assert display["sermon_summary"] == "S"
    assert "bible_es_nvi" not in display
    assert "secreto" not in str(display)


def test_session_clear_translation_context():
    s = LiveSession()
    s.set_translation_context(
        "마태복음 1:1",
        "원고",
        {"generated_at": "t", "bible_es_nvi": []},
        {"ko": "…", "nvi": "…"},
    )
    assert s.context_ready is True
    s.clear_translation_context()
    assert s.context_ready is False
    assert s.translation_context is None
    assert s.passage_display is None
    assert s.bible_text == ""
    assert s.manuscript == ""
