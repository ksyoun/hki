"""Shared KO suffix tuples drive regex and prompt strings."""

from hki.live.ko_endings import (
    KO_CLEAR_FINAL_SUFFIXES,
    KO_OPEN_END_SUFFIXES,
    format_suffix_prompt,
    fragment_looks_open_ko,
    has_clear_final_ending,
)
from hki.live.sentence_prompts import build_recombine_system_prompt
from hki.live.translate import FRAGMENT_ENDING_RULES


def test_shared_final_suffixes_include_kka():
    assert "까" in KO_CLEAR_FINAL_SUFFIXES
    assert has_clear_final_ending("있습니까")
    assert fragment_looks_open_ko("있습니까") is False


def test_clear_final_beats_short_token():
    assert fragment_looks_open_ko("그렇습니다") is False
    assert fragment_looks_open_ko("아멘") is False


def test_ellipsis_beats_clear_final():
    assert fragment_looks_open_ko("그렇습니다...") is True


def test_prompts_use_same_suffix_tuples():
    joined_final = format_suffix_prompt(KO_CLEAR_FINAL_SUFFIXES)
    joined_open = format_suffix_prompt(KO_OPEN_END_SUFFIXES)
    recombine = build_recombine_system_prompt(False, None)
    assert joined_final in recombine
    assert joined_open in recombine
    assert joined_final in FRAGMENT_ENDING_RULES
    assert joined_open in FRAGMENT_ENDING_RULES
