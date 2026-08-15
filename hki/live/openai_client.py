"""Shared AsyncOpenAI client for Contextualizar, translation, and TTS."""

from __future__ import annotations

from openai import AsyncOpenAI

from hki import config

_client: AsyncOpenAI | None = None


def is_gpt5_family(model: str) -> bool:
    """GPT-5+ chat models use max_completion_tokens and reject temperature."""
    return model.startswith("gpt-5")


def chat_completion_extra(
    model: str,
    max_out: int,
    *,
    reasoning: str | None = None,
    temperature: float = 0.1,
) -> dict:
    """
    Extra kwargs for chat.completions.create — switches params by model family.

    GPT-4o / 4o-mini: max_tokens + temperature
    GPT-5.6 Luna etc.: max_completion_tokens + reasoning_effort (no temperature)
    """
    if is_gpt5_family(model):
        kw: dict = {"max_completion_tokens": max_out}
        if reasoning is not None:
            kw["reasoning_effort"] = reasoning
        return kw
    return {"max_tokens": max_out, "temperature": temperature}


def usage_from_response(response) -> tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) from a Chat Completions response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        return prompt, completion
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    return prompt, completion


def get_async_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


async def close_async_openai() -> None:
    global _client
    if _client is None:
        return
    close = getattr(_client, "close", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result
    _client = None
