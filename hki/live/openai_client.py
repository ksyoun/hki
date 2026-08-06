"""Shared AsyncOpenAI client for Contextualizar, translation, and TTS."""

from __future__ import annotations

from openai import AsyncOpenAI

from hki import config

_client: AsyncOpenAI | None = None


def get_async_openai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client
