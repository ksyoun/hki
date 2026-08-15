"""Tests for OpenAI client helpers (GPT-4o vs GPT-5.6 param switching)."""

from hki.live.openai_client import chat_completion_extra, is_gpt5_family, usage_from_response


def test_is_gpt5_family():
    assert is_gpt5_family("gpt-5.6-luna")
    assert is_gpt5_family("gpt-5.6-terra")
    assert not is_gpt5_family("gpt-4o")
    assert not is_gpt5_family("gpt-4o-mini")


def test_chat_completion_extra_gpt4o():
    kw = chat_completion_extra("gpt-4o-mini", 512, reasoning="none", temperature=0.1)
    assert kw == {"max_tokens": 512, "temperature": 0.1}


def test_chat_completion_extra_gpt5_luna():
    kw = chat_completion_extra("gpt-5.6-luna", 512, reasoning="none")
    assert kw == {"max_completion_tokens": 512, "reasoning_effort": "none"}
    assert "temperature" not in kw
    assert "max_tokens" not in kw


def test_chat_completion_extra_gpt5_without_reasoning():
    kw = chat_completion_extra("gpt-5.6-luna", 800)
    assert kw == {"max_completion_tokens": 800}
    assert "reasoning_effort" not in kw


def test_usage_from_response_object():
    class Usage:
        prompt_tokens = 100
        completion_tokens = 20

    class Resp:
        usage = Usage()

    assert usage_from_response(Resp()) == (100, 20)
    assert usage_from_response(object()) == (0, 0)


def test_usage_from_response_dict():
    class Resp:
        usage = {"prompt_tokens": 50, "completion_tokens": 7}

    assert usage_from_response(Resp()) == (50, 7)
