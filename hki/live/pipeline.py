"""Orchestrates audio capture, transcription, and translation."""

from __future__ import annotations

import asyncio
import logging

from hki.live.audio import AudioCapture
from hki.live.broadcaster import Broadcaster
from hki.live.session import LiveSession, SessionState
from hki.live.transcribe import TranscriptionClient
from hki.live.translate import Translator

logger = logging.getLogger(__name__)


class LivePipeline:
    def __init__(self, session: LiveSession, broadcaster: Broadcaster):
        self.session = session
        self.broadcaster = broadcaster
        self._audio: AudioCapture | None = None
        self._transcriber: TranscriptionClient | None = None
        self._translator: Translator | None = None
        self._tasks: list[asyncio.Task] = []
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._level_task: asyncio.Task | None = None
        self._latest_level: dict = {}

    def _on_pcm(self, pcm: bytes) -> None:
        if self.session.state == SessionState.STREAMING:
            try:
                self._audio_queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    def _on_level(self, level: dict) -> None:
        self._latest_level = level

    async def _level_broadcaster(self) -> None:
        from hki import config

        interval = config.LEVEL_METER_INTERVAL_MS / 1000
        while self.session.state in (SessionState.STREAMING, SessionState.MONITORING):
            if self._latest_level:
                await self.broadcaster.broadcast(
                    {"type": "level", **self._latest_level}
                )
            await asyncio.sleep(interval)

    async def _audio_forwarder(self) -> None:
        while self.session.state in (SessionState.STREAMING, SessionState.MONITORING):
            try:
                pcm = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
                if self.session.state == SessionState.STREAMING and self._transcriber:
                    await self._transcriber.send_audio(pcm)
            except asyncio.TimeoutError:
                continue

    async def _on_transcript_delta(self, item_id: str, text: str) -> None:
        await self.broadcaster.broadcast(
            {
                "type": "transcript",
                "lang": "ko",
                "text": text,
                "item_id": item_id,
                "final": False,
            }
        )
        if self._translator and self.session.state == SessionState.STREAMING:
            await self._translator.on_transcript_delta(item_id, text)

    async def _on_transcript_completed(self, item_id: str, text: str) -> None:
        await self.broadcaster.broadcast(
            {
                "type": "transcript",
                "lang": "ko",
                "text": text,
                "item_id": item_id,
                "final": True,
            }
        )
        if self._translator and self.session.state == SessionState.STREAMING:
            await self._translator.on_transcript_completed(item_id, text)

    async def _on_translation(
        self, item_id: str, ko: str, es: str, tier: str
    ) -> None:
        self.session.add_translation(ko, es, tier, item_id)
        await self.broadcaster.broadcast(
            {
                "type": "translation",
                "tier": tier,
                "item_id": item_id,
                "ko": ko,
                "es": es,
                "final": tier == "final",
            }
        )

    async def _status_broadcaster(self) -> None:
        while self.session.state in (
            SessionState.STREAMING,
            SessionState.PAUSED,
            SessionState.MONITORING,
        ):
            self.session.listener_count = self.broadcaster.listener_count
            await self.broadcaster.broadcast(
                {"type": "status", **self.session.to_status()}
            )
            await asyncio.sleep(1)

    def start_monitor(self) -> None:
        self._stop_audio()
        self.session.start_monitoring()
        self._audio = AudioCapture(
            device_index=self.session.device_index,
            gain=self.session.gain,
            on_pcm=self._on_pcm,
            on_level=self._on_level,
        )
        self._audio.start()
        self._level_task = asyncio.create_task(self._level_broadcaster())

    def stop_monitor(self) -> None:
        self._stop_audio()
        if self.session.state == SessionState.MONITORING:
            self.session.stop()

    async def start_streaming(self) -> None:
        self._stop_all()

        self._translator = Translator(
            on_translation=self._on_translation,
            bible_text=self.session.bible_text,
            manuscript=self.session.manuscript,
        )

        self._transcriber = TranscriptionClient(
            on_delta=self._on_transcript_delta,
            on_completed=self._on_transcript_completed,
        )

        self._audio = AudioCapture(
            device_index=self.session.device_index,
            gain=self.session.gain,
            on_pcm=self._on_pcm,
            on_level=self._on_level,
        )

        self.session.start_streaming()
        self._audio.start()

        self._tasks = [
            asyncio.create_task(self._transcriber.run()),
            asyncio.create_task(self._translator.run()),
            asyncio.create_task(self._audio_forwarder()),
            asyncio.create_task(self._level_broadcaster()),
            asyncio.create_task(self._status_broadcaster()),
        ]

        await self.broadcaster.broadcast({"type": "resumed"})

    async def pause(self) -> None:
        self.session.pause()
        await self.broadcaster.broadcast({"type": "paused"})

    async def resume(self) -> None:
        self.session.resume()
        await self.broadcaster.broadcast({"type": "resumed"})

    async def stop(self) -> None:
        self._stop_all()
        self.session.stop()
        await self.broadcaster.broadcast(
            {"type": "status", **self.session.to_status()}
        )

    def set_gain(self, gain: float) -> None:
        self.session.gain = gain
        if self._audio:
            self._audio.set_gain(gain)

    def _stop_audio(self) -> None:
        if self._audio:
            self._audio.stop()
            self._audio = None
        if self._level_task:
            self._level_task.cancel()
            self._level_task = None

    def _stop_all(self) -> None:
        self._stop_audio()
        if self._translator:
            self._translator.stop()
            self._translator = None
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._transcriber = None
        # Drain audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
