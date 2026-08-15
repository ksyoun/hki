"""Sentence pipeline prompt assembly."""

from hki.live.context import ANCHOR_PRIORITY_RULES
from hki.live.sentence_prompts import (
    SENTENCE_COMPLETENESS_RULES,
    SENTENCE_FAITHFULNESS_RULES,
    SENTENCE_OUTPUT_SCHEMA,
    SENTENCE_RELEASE_GENERAL,
    SENTENCE_RELEASE_SERMON,
    build_sentence_system_prompt,
    build_sentence_user_message,
    describe_sentence_prompt,
)
from hki.live.translate import ARGENTINE_RULES, GENERAL_SERVICE_RULES, TRANSLATION_TASK_HEADER


def _fake_context():
    return {
        "sermon_summary": "Resumen de prueba",
        "outline": ["Intro"],
        "terminology": [{"ko": "은혜", "es": "gracia"}],
        "key_names": [{"ko": "아브라함", "es": "Abraham"}],
        "critical_sentences": [
            {
                "ko": "아브라함이 사라를 보고",
                "es": "Abraham vio a Sara",
                "note": "",
            }
        ],
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "NVI texto"}],
        "bible_books": [],
        "recurring_phrases": [],
        "style_notes": "",
    }


def test_general_system_includes_release_and_schema():
    prompt = build_sentence_system_prompt(False, None)
    assert SENTENCE_RELEASE_GENERAL in prompt
    assert SENTENCE_OUTPUT_SCHEMA in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert SENTENCE_COMPLETENESS_RULES in prompt
    assert GENERAL_SERVICE_RULES in prompt
    assert ANCHOR_PRIORITY_RULES in prompt
    assert SENTENCE_RELEASE_SERMON not in prompt
    assert ARGENTINE_RULES not in prompt
    assert "YA TRADUCIDOS" not in prompt
    assert "Resumen de prueba" not in prompt


def test_sermon_context_combines_translation_and_faithfulness():
    prompt = build_sentence_system_prompt(True, _fake_context())
    assert TRANSLATION_TASK_HEADER in prompt
    assert ARGENTINE_RULES in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert SENTENCE_COMPLETENESS_RULES in prompt
    assert SENTENCE_RELEASE_SERMON in prompt
    assert SENTENCE_OUTPUT_SCHEMA in prompt
    assert ANCHOR_PRIORITY_RULES in prompt
    assert "Resumen de prueba" in prompt
    assert "Abraham vio a Sara" in prompt
    assert "Mateo 1:1" in prompt
    assert "YA TRADUCIDOS" not in prompt
    assert prompt.index(ARGENTINE_RULES) < prompt.index(SENTENCE_FAITHFULNESS_RULES)


def test_sermon_fallback_without_context():
    prompt = build_sentence_system_prompt(True, None)
    assert SENTENCE_RELEASE_SERMON in prompt
    assert SENTENCE_FAITHFULNESS_RULES in prompt
    assert SENTENCE_COMPLETENESS_RULES in prompt
    assert "Contextualizar" in prompt


def test_completeness_keeps_hold_release_schema():
    prompt = build_sentence_system_prompt(True, _fake_context())
    assert '"action":"hold"|"release"' in prompt
    assert "through_index=k" in prompt
    assert "습니다" in prompt
    assert "no inventes otros status" in SENTENCE_COMPLETENESS_RULES
    assert "No uses campos extra" in SENTENCE_OUTPUT_SCHEMA


def test_user_message_does_not_fill_from_history():
    msg = build_sentence_user_message(
        [("a", "은혜가")],
        [{"ko": "은혜가", "es": "la gracia"}],
    )
    assert "1. 은혜가" in msg
    assert "No uses el resumen ni el historial" in msg
    assert "continuidad de términos" in msg
    assert "through_index=k" in msg


def test_describe_sentence_prompt_matches_legacy_fields():
    info = describe_sentence_prompt(True, _fake_context())
    assert info["translation_prompt_mode"] == "sermon_context"
    assert info["translator_live"] is True
    assert info["translation_prompt_includes_nvi"] is True
    assert info["translation_prompt_includes_context_summary"] is True
