"""Sermon ON/OFF switches translation system prompts."""

from hki.live.context import format_context_for_system
from hki.live.session import LiveSession
from hki.live.translate import (
    FALLBACK_SYSTEM,
    GENERAL_SYSTEM,
    GENERAL_TASK_HEADER,
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


def test_general_prompt_always_translate_substantive_korean():
    assert "SIEMPRE traducí" in GENERAL_SYSTEM
    assert GENERAL_TASK_HEADER in GENERAL_SYSTEM
    assert "el operador pausa la transmisión en alabanza" in GENERAL_SYSTEM
    assert "traducí solo si hay frase clara" not in GENERAL_SYSTEM


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
    assert t._emit_translation("[Alabanza del coro]", "") is None
    assert t._emit_translation("  ", "") is None
    assert t._emit_translation("Oramos juntos.", "") == "Oramos juntos."
    assert t._emit_translation("Algo dudoso [INCIERTO]", "ko") == "Algo dudoso [INCIERTO]"


def test_emit_translation_heuristic_incierto_for_broken_es():
    t = Translator(lambda *a: None, sermon_mode=True)
    assert t._emit_translation("vio a X y no tiene confianza.", "ko largo") == (
        "vio a X y no tiene confianza. [INCIERTO]"
    )
    general = Translator(lambda *a: None, sermon_mode=False)
    assert general._emit_translation("vio a X y no tiene.", "ko largo") == (
        "vio a X y no tiene."
    )


def test_emit_translation_skips_model_refusal():
    t = Translator(lambda *a: None)
    assert t._emit_translation("Lo siento, no puedo ayudar con eso.", "ko") is None
    assert t._emit_translation("I'm sorry, I can't help with that.", "ko") is None
    general = Translator(lambda *a: None, sermon_mode=False)
    sermon = Translator(lambda *a: None, sermon_mode=True)
    dash = "\u2014"
    assert general._emit_translation(dash, "corto") is None
    assert sermon._emit_translation(dash, "texto coreano bastante largo") is None
    assert sermon._emit_translation(dash, "corto") == dash


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
    t._history.append({"ko": "c", "es": "d"})
    t.set_sermon_mode(False)
    assert t._history == []
