"""OpenAI TTS client — serial queue, PCM output."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import numpy as np
from hki import config
from hki.live.audio import peak_db
from hki.live.openai_client import get_async_openai

logger = logging.getLogger(__name__)

OnAudio = Callable[[str, str, bytes], Awaitable[None]]  # item_id, text, pcm
OnLevel = Callable[[dict], Awaitable[None]]


class TTSClient:
    def __init__(self, on_audio: OnAudio, on_level: OnLevel | None = None):
        self.on_audio = on_audio
        self.on_level = on_level
        self._client = get_async_openai()
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._running = False
        self._in_flight = 0

    def pending_count(self) -> int:
        return self._queue.qsize() + self._in_flight

    async def drain(self, timeout: float = 120.0) -> bool:
        """Wait until queued and in-flight TTS jobs finish."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.pending_count() == 0:
                return True
            await asyncio.sleep(0.05)
        logger.warning(
            "TTS drain timeout (%d still pending)", self.pending_count()
        )
        return False

    async def speak(self, item_id: str, text: str) -> None:
        text = text.strip()
        if text:
            await self._queue.put((item_id, text))

    async def _emit_level(self, level: dict) -> None:
        if self.on_level:
            await self.on_level(level)

    async def _synthesize(self, item_id: str, text: str) -> None:
        phrase = text[:80] + ("…" if len(text) > 80 else "")
        try:
            await self._emit_level(
                {
                    "peak_db": -18.0,
                    "active": True,
                    "phrase": phrase,
                    "synth": True,
                }
            )
            response = await self._client.audio.speech.create(
                model=config.TTS_MODEL,
                voice=config.TTS_VOICE,
                input=text,
                response_format="pcm",
                instructions=config.TTS_INSTRUCTIONS,
            )
            pcm = response.content
            if not pcm:
                return

            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
            peak = peak_db(samples) if len(samples) else -60.0
            await self.on_audio(item_id, text, pcm)
            await self._emit_level(
                {
                    "peak_db": peak,
                    "active": True,
                    "phrase": phrase,
                    "synth": False,
                }
            )
        except Exception as e:
            logger.error("TTS synthesis error: %s", e)
            await self._emit_level(
                {
                    "peak_db": -60.0,
                    "active": False,
                    "phrase": "",
                    "synth": False,
                }
            )

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                item_id, text = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self._in_flight += 1
            try:
                await self._synthesize(item_id, text)
            finally:
                self._in_flight -= 1

    def stop(self) -> None:
        self._running = False
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break
