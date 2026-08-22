"""Unit tests for Bible API error classification and context helpers."""

from __future__ import annotations

import httpx

from hki.live.bible_api import (
    BibleFetchErrorKind,
    ParsedReference,
    classify_fetch_error,
)
from hki.live.context import (
    format_context_display,
    format_context_for_recombine,
    format_context_for_system,
    format_context_for_translate,
    format_context_for_understand,
    has_sermon_summary,
    normalize_bilingual_list,
    normalize_bilingual_text,
    normalize_critical_sentences,
    normalize_ko_stt,
)
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


def test_format_context_understand_omits_nvi_translate_keeps_it():
    ctx = {
        "sermon_summary": {"ko": "설교 요약", "es": "Resumen breve"},
        "outline": [{"ko": "도입", "es": "Intro"}],
        "terminology": [{"ko": "은혜", "es": "gracia"}],
        "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
        "key_names": [
            {"ko": "사라", "es": "Sara", "stt_variants": ["사래", "살아"]},
        ],
        "recurring_phrases": [
            {"ko": "여러분", "es": "hermanos", "placement": "inicio"},
        ],
        "critical_sentences": [
            {
                "ko": "이것은 핵심 메시지입니다.",
                "es": "Este es el mensaje central.",
                "note": "punto clave",
            }
        ],
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía…"}],
        "style_notes": "tono respetuoso: usted, hermanos",
    }
    understand = format_context_for_understand(ctx)
    translate = format_context_for_translate(ctx)
    assert "설교 요약" in understand
    assert "도입" in understand
    assert "Resumen breve" not in understand
    assert "Intro" not in understand
    assert "이것은 핵심 메시지입니다." in understand
    assert "사래" in understand
    assert "Libro de la genealogía" not in understand
    assert "Este es el mensaje central." not in understand
    assert "gracia" not in understand
    assert "Resumen breve" in translate
    assert "Intro" in translate
    assert "설교 요약" not in translate
    assert "Libro de la genealogía" in translate
    assert "Mateo 1:1" in translate
    assert "gracia" in translate
    assert "NO sustituyen el KO" in translate
    assert "Orden de prioridad" not in translate
    assert "si STT de la misma idea es incoherente" not in translate
    system = format_context_for_system(ctx)
    assert "Orden de prioridad" in system
    assert "Resumen breve" in system
    assert system != translate


def test_format_context_for_system_includes_nvi():
    block = format_context_for_system(
        {
            "sermon_summary": "Resumen breve",
            "outline": ["Intro"],
            "terminology": [{"ko": "은혜", "es": "gracia"}],
            "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
            "key_names": [
                {"ko": "사라", "es": "Sara", "stt_variants": ["사래", "살아"]},
            ],
            "recurring_phrases": [
                {"ko": "여러분", "es": "hermanos", "placement": "inicio"},
            ],
            "critical_sentences": [
                {
                    "ko": "이것은 핵심 메시지입니다.",
                    "es": "Este es el mensaje central.",
                    "note": "punto clave",
                }
            ],
            "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía…"}],
            "style_notes": "tono respetuoso: usted, hermanos",
        }
    )
    assert "Resumen breve" in block
    assert "Mateo 1:1" in block
    assert "gracia" in block
    assert "tono respetuoso" in block
    assert "사라" in block
    assert "사래" in block
    assert "hermanos" in block
    assert "핵심 메시지" in block
    assert "mensaje central" in block
    assert "Orden de prioridad" in block


def test_format_context_display_includes_stt_fields():
    display = format_context_display(
        {
            "sermon_summary": {"ko": "요약", "es": "S"},
            "outline": [{"ko": "도입", "es": "Intro"}],
            "terminology": [],
            "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
            "key_names": [{"ko": "사라", "es": "Sara", "stt_variants": ["사래"]}],
            "recurring_phrases": [{"ko": "여러분", "es": "hermanos"}],
            "critical_sentences": [{"ko": "앵커", "es": "Ancla", "note": ""}],
            "style_notes": "",
            "bible_references": [],
            "bible_es_source": "bible_api",
        }
    )
    assert display["ko"]["sermon_summary"] == "요약"
    assert display["es"]["sermon_summary"] == "S"
    assert display["ko"]["outline"] == ["도입"]
    assert display["es"]["outline"] == ["Intro"]
    assert display["ko"]["bible_books"] == ["마태복음"]
    assert display["es"]["bible_books"][0]["es"] == "Mateo"
    assert "terminology" not in display["ko"]
    assert display["ko"]["key_names"][0]["ko"] == "사라"
    assert display["ko"]["key_names"][0]["stt_variants"] == ["사래"]
    assert display["es"]["key_names"][0]["ko"] == "사라"
    assert display["es"]["recurring_phrases"][0]["ko"] == "여러분"
    assert display["ko"]["critical_sentences"][0]["ko"] == "앵커"
    assert display["es"]["critical_sentences"][0]["es"] == "Ancla"


def test_format_context_display_legacy_summary_goes_to_es_card():
    display = format_context_display(
        {
            "sermon_summary": "Resumen legado",
            "outline": ["Intro"],
            "terminology": [{"ko": "은혜", "es": "gracia"}],
            "bible_books": [],
            "key_names": [],
            "recurring_phrases": [],
            "critical_sentences": [],
            "style_notes": "usted",
            "bible_references": [],
            "bible_es_source": "",
        }
    )
    assert display["ko"]["sermon_summary"] == ""
    assert display["es"]["sermon_summary"] == "Resumen legado"
    assert display["ko"]["outline"] == []
    assert display["es"]["outline"] == ["Intro"]
    assert display["es"]["terminology"][0]["es"] == "gracia"
    assert has_sermon_summary({"sermon_summary": "Resumen legado"}) is True
    assert has_sermon_summary({"sermon_summary": {"ko": "", "es": ""}}) is False
    assert normalize_bilingual_text("Hola") == {"ko": "", "es": "Hola"}
    assert normalize_bilingual_list(["A", {"ko": "가", "es": "B"}]) == [
        {"ko": "", "es": "A"},
        {"ko": "가", "es": "B"},
    ]


def test_normalize_critical_sentences_legacy_strings():
    out = normalize_critical_sentences(["한국어", {"ko": "a", "es": "b", "note": ""}])
    assert out[0] == {"ko": "한국어", "es": "", "note": ""}
    assert out[1]["es"] == "b"


def test_format_context_for_recombine_omits_summary_and_nvi():
    block = format_context_for_recombine(
        {
            "sermon_summary": "No debe aparecer",
            "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "secreto"}],
            "key_names": [{"ko": "사라", "es": "Sara"}],
            "critical_sentences": [
                {"ko": "ko", "es": "Abraham confió en Dios", "note": "test"}
            ],
            "style_notes": "usted",
        }
    )
    assert "No debe aparecer" not in block
    assert "secreto" not in block
    assert "Abraham confió" in block
    assert "Sara" in block


def test_normalize_ko_stt_replaces_variants():
    ctx = {
        "key_names": [
            {
                "ko": "사라",
                "es": "Sara",
                "stt_variants": ["사래", "살아"],
            }
        ]
    }
    assert normalize_ko_stt("사래가 왔다", ctx) == "사라가 왔다"


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
    assert display["es"]["sermon_summary"] == "S"
    assert display["ko"]["sermon_summary"] == ""
    assert "bible_es_nvi" not in display
    assert "bible_es_nvi" not in display["es"]
    assert "secreto" not in str(display)


def test_session_clear_translation_context():
    s = LiveSession()
    s.set_translation_context(
        "원고",
        {"generated_at": "t", "bible_es_nvi": []},
        {"ko": "마태복음 1:1", "nvi": "…"},
    )
    assert s.context_ready is True
    assert s.bible_text == "마태복음 1:1"
    s.clear_translation_context()
    assert s.context_ready is False
    assert s.translation_context is None
    assert s.passage_display is None
    assert s.bible_text == ""
    assert s.manuscript == ""


def test_bible_text_comes_from_passage_display():
    s = LiveSession()
    s.set_translation_context(
        "원고",
        {"generated_at": "t"},
        {"ko": "  요한복음 3:16  ", "nvi": "Juan 3:16 …"},
    )
    assert s.bible_text == "요한복음 3:16"
    status = s.to_status()
    assert status["bible_text"] == "요한복음 3:16"
    assert status["passage_display"]["ko"] == "  요한복음 3:16  "
