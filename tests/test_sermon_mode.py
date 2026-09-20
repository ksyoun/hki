"""Sermon ON/OFF switches translation system prompts."""

from hki.live.context import format_context_for_system
from hki.live.session import LiveSession
from hki.live.translate import (
    ARGENTINE_RULES,
    FALLBACK_SYSTEM,
    FRAGMENT_ENDING_RULES,
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
    assert FRAGMENT_ENDING_RULES in GENERAL_SYSTEM
    assert "alfabetos no latinos" in GENERAL_SYSTEM
    assert "omítelo" in GENERAL_SYSTEM


def test_sermon_prompt_omits_unintelligible_tokens():
    assert "Nunca copies hangul" in ARGENTINE_RULES
    assert "OMÍTELA" in ARGENTINE_RULES
    assert "al FINAL del fragmento" in ARGENTINE_RULES
    assert "inteligible" in ARGENTINE_RULES


def test_fragment_ending_rules_in_sermon_prompts():
    assert FRAGMENT_ENDING_RULES in FALLBACK_SYSTEM
    t = Translator(lambda *a: None, context={"sermon_summary": "x"}, sermon_mode=True)
    assert FRAGMENT_ENDING_RULES in t._system_prompt()


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


def test_emit_translation_strips_gujarati_and_marks_incierto():
    t = Translator(lambda *a: None)
    out = t._emit_translation(
        "Sin poder llegar a Betel, se quedó en Siquén,િકેટ",
        "세겜에 머물러",
    )
    assert out is not None
    assert "િકેટ" not in out
    assert "Siquén" in out
    assert "[INCIERTO]" in out


def test_emit_translation_strips_quoted_hangul():
    t = Translator(lambda *a: None)
    out = t._emit_translation("Por encima de Dios «디게»", "하나님 위에 디게")
    assert out is not None
    assert "디게" not in out
    assert "Por encima de Dios" in out
    assert "[INCIERTO]" in out
    assert out.count("[INCIERTO]") == 1


def test_emit_translation_drops_script_only_output():
    t = Translator(lambda *a: None)
    assert t._emit_translation("한글만", "한글만") is None


def test_emit_translation_does_not_duplicate_incierto_after_strip():
    t = Translator(lambda *a: None)
    out = t._emit_translation("Hola «안녕» [INCIERTO]", "안녕")
    assert out is not None
    assert "안녕" not in out
    assert out.count("[INCIERTO]") == 1
