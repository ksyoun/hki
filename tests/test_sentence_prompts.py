"""Sentence pipeline prompt assembly."""

from hki.live.context import ANCHOR_PRIORITY_RULES, format_context_for_translate
from hki.live.sentence_prompts import (
    SENTENCE_COMPLETENESS_RULES,
    SENTENCE_FAITHFULNESS_RULES,
    SENTENCE_TRANSLATE_STYLE_RULES,
    SENTENCE_TRANSLATE_TASK_HEADER,
    UNDERSTAND_OUTPUT_SCHEMA,
    UNDERSTAND_REPAIR_RULES,
    build_translate_system_prompt,
    build_understand_system_prompt,
    build_understand_user_message,
    describe_sentence_prompt,
)
from hki.live.translate import ARGENTINE_RULES, GENERAL_SERVICE_RULES, TRANSLATION_TASK_HEADER


def _fake_context():
    return {
        "sermon_summary": {"ko": "시험 요약", "es": "Resumen de prueba"},
        "outline": [{"ko": "도입", "es": "Intro"}],
        "terminology": [{"ko": "은혜", "es": "gracia"}],
        "key_names": [{"ko": "아브라함", "es": "Abraham", "stt_variants": ["아브라함"]}],
        "critical_sentences": [
            {
                "ko": "아브라함이 사라를 보고",
                "es": "Abraham vio a Sara",
                "note": "",
            }
        ],
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía de Jesucristo"}],
        "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
        "recurring_phrases": [{"ko": "여러분", "es": "hermanos"}],
        "style_notes": "usted",
    }


def test_understand_general_has_no_nvi_or_translate_register():
    prompt = build_understand_system_prompt(False, None)
    assert UNDERSTAND_REPAIR_RULES in prompt
    assert SENTENCE_COMPLETENESS_RULES in prompt
    assert UNDERSTAND_OUTPUT_SCHEMA in prompt
    assert "ko_corrected" in prompt
    assert ARGENTINE_RULES not in prompt
    assert "Libro de la genealogía" not in prompt
    assert TRANSLATION_TASK_HEADER not in prompt
    assert SENTENCE_TRANSLATE_TASK_HEADER not in prompt
    assert "NO es la fuente" not in prompt


def test_understand_sermon_omits_nvi_body():
    prompt = build_understand_system_prompt(True, _fake_context())
    assert "아브라함" in prompt
    assert "아브라함이 사라를 보고" in prompt
    assert "시험 요약" in prompt
    assert "도입" in prompt
    assert "Resumen de prueba" not in prompt
    assert "Libro de la genealogía de Jesucristo" not in prompt
    assert "Abraham vio a Sara" not in prompt
    assert "gracia" not in prompt
    assert UNDERSTAND_REPAIR_RULES in prompt
    assert "문장 생성" in prompt or "채워" in prompt


def test_translate_sermon_keeps_nvi_without_classic_rules():
    prompt = build_translate_system_prompt(True, _fake_context())
    assert SENTENCE_TRANSLATE_TASK_HEADER in prompt
    assert SENTENCE_TRANSLATE_STYLE_RULES in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert TRANSLATION_TASK_HEADER not in prompt
    assert ARGENTINE_RULES not in prompt
    assert ANCHOR_PRIORITY_RULES not in prompt
    assert "ANTES de traducir" not in prompt
    assert "priorizá el sentido de esa frase" not in prompt
    assert "Resumen de prueba" in prompt
    assert "Abraham vio a Sara" in prompt
    assert "Mateo 1:1" in prompt
    assert "Libro de la genealogía de Jesucristo" in prompt
    assert "YA TRADUCIDOS" not in prompt
    assert "through_index" not in prompt
    assert "NO es la fuente" in prompt
    assert "NVI" in prompt
    assert "reexpresión gramatical" in prompt
    assert "no infieras" in prompt


def test_translate_general_has_service_rules():
    prompt = build_translate_system_prompt(False, None)
    assert SENTENCE_TRANSLATE_TASK_HEADER in prompt
    assert GENERAL_SERVICE_RULES in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert ARGENTINE_RULES not in prompt
    assert TRANSLATION_TASK_HEADER not in prompt
    assert "Libro de la genealogía" not in prompt


def test_understand_fallback_without_context():
    prompt = build_understand_system_prompt(True, None)
    assert SENTENCE_COMPLETENESS_RULES in prompt
    assert "Contextualizar" in prompt
    assert "Libro de la genealogía" not in prompt


def test_user_message_relative_index():
    msg = build_understand_user_message(
        [("a", "은혜가")],
        [{"ko": "은혜가", "es": "la gracia"}],
    )
    assert "1. 은혜가" in msg
    assert "through_index=k" in msg
    assert "KO: 은혜가" in msg


def test_describe_sentence_prompt_nvi_is_translate_only():
    info = describe_sentence_prompt(True, _fake_context())
    assert info["translation_prompt_mode"] == "sermon_context"
    assert info["translator_live"] is True
    assert info["translation_prompt_includes_nvi"] is True
    assert info["understand_prompt_includes_nvi"] is False
    assert info["translation_prompt_includes_context_summary"] is True


def test_nvi_is_reference_not_source():
    prompt = build_translate_system_prompt(True, _fake_context())
    assert "NO es la fuente" in prompt
    assert "no fuente alternativa" in prompt or "REFERENCIA" in prompt
    assert "마태복음 1장 1절을 보십시오" in prompt
    assert "no recites" in prompt
    assert "El KO —no el bloque NVI— decide si hay lectura" in prompt
    understand = build_understand_system_prompt(True, _fake_context())
    assert "NO es la fuente" not in understand
    assert "Libro de la genealogía de Jesucristo" not in understand


def test_translate_critical_sentence_does_not_replace_ko_source():
    ctx = {
        "sermon_summary": "Resumen",
        "critical_sentences": [
            {
                "ko": "우리는 은혜로 살아갑니다.",
                "es": "Vivimos por gracia.",
                "note": "",
            }
        ],
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía de Jesucristo"}],
    }
    prompt = build_translate_system_prompt(True, ctx)
    ctx_block = format_context_for_translate(ctx)
    assert "Vivimos por gracia" in prompt
    assert "NO sustituyen el KO" in ctx_block
    assert "traducí el KO" in ctx_block
    assert "믿음/fe" in prompt
    assert "은혜/gracia" in prompt
    assert ANCHOR_PRIORITY_RULES not in prompt
    assert "priorizá el sentido de esa frase" not in prompt
    assert "si STT de la misma idea es incoherente" not in ctx_block
