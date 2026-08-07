"""Sermon ON/OFF switches translation system prompts."""

from hki.live.context import format_context_for_system
from hki.live.session import LiveSession
from hki.live.translate import (
    FALLBACK_SYSTEM,
    GENERAL_SYSTEM,
    Translator,
)


def test_general_prompt_ignores_context_when_sermon_off():
    ctx = {
        "sermon_summary": "Resumen secreto",
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "NVI text"}],
    }
    t = Translator(lambda *a: None, context=ctx, sermon_mode=False)
    prompt = t._system_prompt()
    assert prompt == GENERAL_SYSTEM
    assert "Resumen secreto" not in prompt
    assert "NVI text" not in prompt


def test_sermon_prompt_uses_context_when_sermon_on():
    ctx = {
        "sermon_summary": "Resumen del sermón",
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Texto NVI"}],
    }
    t = Translator(lambda *a: None, context=ctx, sermon_mode=True)
    prompt = t._system_prompt()
    assert "Resumen del sermón" in prompt
    assert "Texto NVI" in prompt
    assert format_context_for_system(ctx).split("\n")[0] in prompt


def test_sermon_on_without_context_uses_fallback():
    t = Translator(lambda *a: None, context=None, sermon_mode=True)
    assert t._system_prompt() == FALLBACK_SYSTEM


def test_emit_translation_skips_bracket_placeholders():
    t = Translator(lambda *a: None)
    assert t._emit_translation("[Alabanza del coro]") is None
    assert t._emit_translation("  ") is None
    assert t._emit_translation("Oramos juntos.") == "Oramos juntos."


def test_session_sermon_on_resets_on_stream_start():
    s = LiveSession()
    s.sermon_on = True
    s.start_streaming()
    assert s.sermon_on is False


def test_set_sermon_mode_clears_history():
    t = Translator(lambda *a: None, sermon_mode=False)
    t._history.append({"ko": "a", "es": "b"})
    t.set_sermon_mode(True)
    assert t._history == []
