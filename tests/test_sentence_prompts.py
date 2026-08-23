"""Sentence pipeline prompt assembly."""

from hki.live.context import ANCHOR_PRIORITY_RULES, format_context_for_translate
from hki.live.sentence_prompts import (
    RECOMBINE_OUTPUT_SCHEMA,
    RECOMBINE_TIDY_RULES,
    SENTENCE_FAITHFULNESS_RULES,
    SENTENCE_TRANSLATE_STYLE_RULES,
    SENTENCE_TRANSLATE_TASK_HEADER,
    build_recombine_system_prompt,
    build_recombine_user_message,
    build_translate_system_prompt,
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


def test_recombine_general_has_no_nvi_or_translate_register():
    prompt = build_recombine_system_prompt(False, None)
    assert RECOMBINE_TIDY_RULES in prompt
    assert RECOMBINE_OUTPUT_SCHEMA in prompt
    assert "through_index" not in prompt
    assert "hold" not in prompt.lower() or "내용을 채우지" in prompt
    assert ARGENTINE_RULES not in prompt
    assert "Libro de la genealogía" not in prompt
    assert TRANSLATION_TASK_HEADER not in prompt
    assert SENTENCE_TRANSLATE_TASK_HEADER not in prompt
    assert "NO es la fuente" not in prompt
    assert "의미를 보충하지" in prompt
    assert "하나의 자연스러운 한국어 발화" not in prompt
    assert "unit 1개" in prompt
    assert "문장 완성" in prompt


def test_recombine_sermon_omits_nvi_summary_critical():
    prompt = build_recombine_system_prompt(True, _fake_context())
    assert "아브라함" in prompt
    assert "마태복음" in prompt
    assert "여러분" in prompt
    assert "설교 용어 참고" in prompt
    assert "아브라함이 사라를 보고" not in prompt
    assert "시험 요약" not in prompt
    assert "도입" not in prompt
    assert "Resumen de prueba" not in prompt
    assert "Libro de la genealogía de Jesucristo" not in prompt
    assert "Abraham vio a Sara" not in prompt
    assert "gracia" not in prompt
    assert "Contexto para anclas" not in prompt
    assert RECOMBINE_TIDY_RULES in prompt
    assert "복원" in prompt
    assert "하나의 자연스러운 한국어 발화" not in prompt
    assert "unit 1개" in prompt
    assert "문장 완성" in prompt


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
    assert "unidad de habla" in prompt
    assert "STT crudo" in prompt
    assert "recombine" in prompt


def test_translate_general_has_service_rules():
    prompt = build_translate_system_prompt(False, None)
    assert SENTENCE_TRANSLATE_TASK_HEADER in prompt
    assert GENERAL_SERVICE_RULES in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert ARGENTINE_RULES not in prompt
    assert TRANSLATION_TASK_HEADER not in prompt
    assert "Libro de la genealogía" not in prompt


def test_recombine_fallback_without_context():
    prompt = build_recombine_system_prompt(True, None)
    assert RECOMBINE_TIDY_RULES in prompt
    assert "Contextualizar" in prompt
    assert "Libro de la genealogía" not in prompt


def test_recombine_user_message_zero_based():
    msg = build_recombine_user_message(
        [("a", "은혜가")],
        [{"ko": "은혜가", "es": "la gracia"}],
    )
    assert "[0] 은혜가" in msg
    assert "through_index" not in msg
    assert "KO: 은혜가" in msg
    assert "하나의 자연스러운 한국어 발화" not in msg
    assert "unit" in msg


def test_describe_sentence_prompt_nvi_is_translate_only():
    info = describe_sentence_prompt(True, _fake_context())
    assert info["translation_prompt_mode"] == "sermon_context"
    assert info["translator_live"] is True
    assert info["translation_prompt_includes_nvi"] is True
    assert info["understand_prompt_includes_nvi"] is False
    assert info["recombine_prompt_includes_nvi"] is False
    assert info["translation_prompt_includes_context_summary"] is True


def test_nvi_is_reference_not_source():
    prompt = build_translate_system_prompt(True, _fake_context())
    assert "NO es la fuente" in prompt
    assert "no fuente alternativa" in prompt or "REFERENCIA" in prompt
    assert "마태복음 1장 1절을 보십시오" in prompt
    assert "no recites" in prompt
    assert "El KO —no el bloque NVI— decide si hay lectura" in prompt
    recombine = build_recombine_system_prompt(True, _fake_context())
    assert "NO es la fuente" not in recombine
    assert "Libro de la genealogía de Jesucristo" not in recombine


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
