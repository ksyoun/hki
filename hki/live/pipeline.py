"""Orchestrates audio capture, transcription, and translation."""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from hki import config
from hki.live.audio import AudioCapture, _peak_db, _rms_db, pcm_to_base64
from hki.live.broadcaster import Broadcaster
from hki.live.file_replay import apply_gain
from hki.live.latency import LatencyProfiler
from hki.live.session import LiveSession, SessionState
from hki.live.speech_analytics import (
    SpeechAnalyticsCollector,
    save_session_report,
    update_cumulative_summary,
)
from hki.live.transcribe import TranscriptionClient
from hki.live.translate import Translator
from hki.live.tts import TTSClient

logger = logging.getLogger(__name__)


class LivePipeline:
    def __init__(self, session: LiveSession, broadcaster: Broadcaster):
        self.session = session
        self.broadcaster = broadcaster
        self._audio: AudioCapture | None = None
        self._transcriber: TranscriptionClient | None = None
        self._translator: Translator | None = None
        self._tts: TTSClient | None = None
        self._tasks: list[asyncio.Task] = []
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._level_task: asyncio.Task | None = None
        self._latest_level: dict = {}
        self._latest_output_level: dict = {}
        self._latency: LatencyProfiler | None = None
        self._speech_analytics: SpeechAnalyticsCollector | None = None

    def _has_audience(self) -> bool:
        return self.broadcaster.audience_count >= config.MIN_AUDIENCE_COUNT

    def _should_generate_tts(self) -> bool:
        return config.TTS_ENABLED and self.session.speaker_subscribers > 0

    async def _broadcast_live_status(self) -> None:
        self.session.audience_count = self.broadcaster.audience_count
        await self.broadcaster.broadcast(
            {
                "type": "status",
                **self.session.build_live_status(config.TTS_ENABLED),
            }
        )

    def _on_pcm(self, pcm: bytes) -> None:
        if self.session.state == SessionState.STREAMING:
            try:
                self._audio_queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    def _on_level(self, level: dict) -> None:
        self._latest_level = level

    async def _level_broadcaster(self) -> None:
        interval = config.LEVEL_METER_INTERVAL_MS / 1000
        while self.session.state in (SessionState.STREAMING, SessionState.MONITORING):
            level = self._latest_level or {
                "rms_db": -60.0,
                "peak_db": -60.0,
                "clipping": False,
            }
            await self.broadcaster.broadcast({"type": "level", **level})
            await asyncio.sleep(interval)

    async def _output_level_broadcaster(self) -> None:
        interval = config.LEVEL_METER_INTERVAL_MS / 1000
        while self.session.state == SessionState.STREAMING:
            level = self._latest_output_level or {
                "peak_db": -60.0,
                "active": False,
                "phrase": "",
            }
            await self.broadcaster.broadcast({"type": "output_level", **level})
            await asyncio.sleep(interval)

    async def _on_output_level(self, level: dict) -> None:
        self._latest_output_level = level

    async def _on_tts_audio(self, item_id: str, text: str, pcm: bytes) -> None:
        if self._speech_analytics and not self.session.test_mode:
            self._speech_analytics.on_tts_audio(item_id, pcm, time.monotonic())
        await self.broadcaster.broadcast(
            {
                "type": "tts",
                "item_id": item_id,
                "es": text,
                "audio": pcm_to_base64(pcm),
                "format": "pcm",
                "rate": config.TTS_SAMPLE_RATE,
            }
        )

    async def _audio_forwarder(self) -> None:
        while self.session.state in (SessionState.STREAMING, SessionState.MONITORING):
            try:
                pcm = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
                if (
                    self.session.state == SessionState.STREAMING
                    and self._transcriber
                    and self._has_audience()
                ):
                    await self._transcriber.send_audio(pcm)
            except asyncio.TimeoutError:
                continue

    async def _on_transcript_delta(self, item_id: str, text: str) -> None:
        now = time.monotonic()
        if self._latency:
            self._latency.on_transcript_delta(item_id, text, now)
        if (
            self._speech_analytics
            and not self.session.test_mode
            and self._has_audience()
        ):
            self._speech_analytics.on_transcript_delta(item_id, text, now)
        await self.broadcaster.broadcast(
            {
                "type": "transcript",
                "lang": "ko",
                "text": text,
                "item_id": item_id,
                "final": False,
            }
        )

    async def _on_transcript_completed(self, item_id: str, text: str) -> None:
        now = time.monotonic()
        if self._latency:
            self._latency.on_transcript_completed(
                item_id, text, now, self.session.test_playback_sec
            )
        if (
            self._speech_analytics
            and not self.session.test_mode
            and self._has_audience()
        ):
            depth = self._translator.queue_size() if self._translator else 0
            self._speech_analytics.on_transcript_completed(
                item_id, text, now, translator_queue_depth=depth
            )
        self.session.add_transcript(text)
        await self.broadcaster.broadcast(
            {
                "type": "transcript",
                "lang": "ko",
                "text": text,
                "item_id": item_id,
                "final": True,
            }
        )
        if self._translator and self.session.state == SessionState.STREAMING and self._has_audience():
            await self._translator.on_transcript_completed(item_id, text)

    async def _on_translation(
        self, item_id: str, ko: str, es: str, tier: str
    ) -> None:
        now = time.monotonic()
        if self._latency:
            self._latency.on_translation(item_id, tier, now)
        if (
            tier == "final"
            and self._speech_analytics
            and not self.session.test_mode
            and self._has_audience()
        ):
            tts_depth = self._tts.queue_size() if self._tts else 0
            self._speech_analytics.on_translation(
                item_id, es, now, tts_queue_depth=tts_depth
            )
        if tier == "final":
            self.session.add_final_translation(es)
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
        if tier == "final" and self._tts and self._should_generate_tts():
            await self._tts.speak(item_id, es)

    async def _status_broadcaster(self) -> None:
        while self.session.state in (
            SessionState.STREAMING,
            SessionState.PAUSED,
            SessionState.MONITORING,
        ):
            await self._broadcast_live_status()
            await asyncio.sleep(1)

    async def _file_replay_loop(self, pcm: bytes, duration_sec: float) -> None:
        chunk_bytes = int(config.TARGET_SAMPLE_RATE * config.AUDIO_CHUNK_MS / 1000) * 2
        offset = 0
        self.session.test_duration_sec = duration_sec
        self.session.test_playback_sec = 0.0

        while offset < len(pcm) and self.session.state in (
            SessionState.STREAMING,
            SessionState.PAUSED,
        ):
            if self.session.state == SessionState.PAUSED:
                await asyncio.sleep(0.1)
                continue

            chunk = pcm[offset : offset + chunk_bytes]
            if len(chunk) < chunk_bytes:
                chunk = chunk + b"\x00" * (chunk_bytes - len(chunk))

            processed = apply_gain(chunk, self.session.gain)
            samples = np.frombuffer(processed, dtype=np.int16).astype(np.float32) / 32767.0
            self._on_level(
                {
                    "rms_db": _rms_db(samples),
                    "peak_db": _peak_db(samples),
                    "clipping": bool(np.any(np.abs(samples) > 0.99)),
                }
            )
            self._on_pcm(processed)

            offset += chunk_bytes
            self.session.test_playback_sec = min(
                duration_sec, offset / 2 / config.TARGET_SAMPLE_RATE
            )
            remaining = max(0.0, duration_sec - self.session.test_playback_sec)
            await self.broadcaster.broadcast(
                {
                    "type": "test_progress",
                    "elapsed_sec": self.session.test_playback_sec,
                    "duration_sec": duration_sec,
                    "remaining_sec": remaining,
                }
            )
            await asyncio.sleep(config.AUDIO_CHUNK_MS / 1000)

        await asyncio.sleep(3)
        if self.session.test_mode and self.session.state == SessionState.STREAMING:
            await self._finalize_latency_report()
            await self.stop()

    def _start_level_task(self) -> None:
        if self._level_task and not self._level_task.done():
            self._level_task.cancel()
        self._level_task = asyncio.get_running_loop().create_task(
            self._level_broadcaster()
        )

    async def ensure_input_monitor(self) -> None:
        if self.session.state in (SessionState.STREAMING, SessionState.PAUSED):
            return

        self._stop_audio()
        self.session.start_monitoring()
        self._audio = AudioCapture(
            device_index=self.session.device_index,
            gain=self.session.gain,
            on_pcm=self._on_pcm,
            on_level=self._on_level,
        )
        try:
            self._audio.start()
            self.session.device_index = self._audio.device_index
        except Exception:
            self.session.stop()
            self._audio = None
            logger.exception("Input monitor failed to start")
            raise

        self._start_level_task()
        logger.info(
            "Input monitor active: device=%s gain=%.2f",
            self.session.device_index,
            self.session.gain,
        )

    def stop_monitor(self) -> None:
        self._stop_audio()
        if self.session.state == SessionState.MONITORING:
            self.session.stop()

    async def start_streaming(self) -> None:
        self._stop_all()
        self.session.clear_session_log()
        self.session.session_label = "transmision"
        self.session.test_mode = False
        self._latency = None
        self._speech_analytics = SpeechAnalyticsCollector(
            stream_start_mono=time.monotonic()
        )

        self._translator = Translator(
            on_translation=self._on_translation,
            bible_text=self.session.bible_text,
            manuscript=self.session.manuscript,
        )

        self._transcriber = TranscriptionClient(
            on_delta=self._on_transcript_delta,
            on_completed=self._on_transcript_completed,
        )

        if config.TTS_ENABLED:
            self._tts = TTSClient(
                on_audio=self._on_tts_audio,
                on_level=self._on_output_level,
            )

        self._audio = AudioCapture(
            device_index=self.session.device_index,
            gain=self.session.gain,
            on_pcm=self._on_pcm,
            on_level=self._on_level,
        )

        self.session.start_streaming()
        try:
            self._audio.start()
            self.session.device_index = self._audio.device_index
        except Exception:
            logger.exception("Streaming audio failed to start")
            await self.stop()
            raise

        self._tasks = [
            asyncio.create_task(self._transcriber.run()),
            asyncio.create_task(self._translator.run()),
            asyncio.create_task(self._audio_forwarder()),
            asyncio.create_task(self._level_broadcaster()),
            asyncio.create_task(self._status_broadcaster()),
        ]
        if self._tts:
            self._tasks.append(asyncio.create_task(self._tts.run()))
            self._tasks.append(asyncio.create_task(self._output_level_broadcaster()))

        await self._broadcast_live_status()
        await self.broadcaster.broadcast({"type": "resumed"})

    async def start_test_streaming(
        self, pcm: bytes, duration_sec: float, filename: str
    ) -> None:
        self._stop_all()
        self.session.clear_session_log()
        self.session.session_label = filename or "prueba"
        self.session.test_mode = True
        self.session.test_filename = filename
        self.session.test_duration_sec = duration_sec
        self.session.test_playback_sec = 0.0
        self._latency = LatencyProfiler()
        self._speech_analytics = None

        self._translator = Translator(
            on_translation=self._on_translation,
            bible_text=self.session.bible_text,
            manuscript=self.session.manuscript,
        )

        self._transcriber = TranscriptionClient(
            on_delta=self._on_transcript_delta,
            on_completed=self._on_transcript_completed,
        )

        if config.TTS_ENABLED:
            self._tts = TTSClient(
                on_audio=self._on_tts_audio,
                on_level=self._on_output_level,
            )

        self.session.start_streaming()

        self._tasks = [
            asyncio.create_task(self._transcriber.run()),
            asyncio.create_task(self._translator.run()),
            asyncio.create_task(self._audio_forwarder()),
            asyncio.create_task(self._file_replay_loop(pcm, duration_sec)),
            asyncio.create_task(self._status_broadcaster()),
        ]
        if self._tts:
            self._tasks.append(asyncio.create_task(self._tts.run()))
            self._tasks.append(asyncio.create_task(self._output_level_broadcaster()))

        await self._broadcast_live_status()
        await self.broadcaster.broadcast({"type": "resumed"})

    async def pause(self) -> None:
        self.session.pause()
        await self.broadcaster.broadcast({"type": "paused"})

    async def resume(self) -> None:
        self.session.resume()
        await self._broadcast_live_status()
        await self.broadcaster.broadcast({"type": "resumed"})

    async def _finalize_latency_report(self) -> None:
        if not self._latency:
            return
        report = self._latency.build_report(self.session.session_label)
        self.session.latency_report = report
        summary = report.get("summary", {}).get("final_after_utterance_ms", {})
        logger.info(
            "Latency report: %d utterances, final avg=%sms p95=%sms",
            report.get("utterance_count", 0),
            summary.get("avg"),
            summary.get("p95"),
        )

    async def _finalize_speech_analytics(self) -> None:
        if not self._speech_analytics:
            return
        report = self._speech_analytics.build_report(self.session.session_label)
        self._speech_analytics = None
        if report.get("utterance_count", 0) < 1:
            return
        self.session.speech_analytics_report = report
        path = save_session_report(report)
        update_cumulative_summary(report, path)
        bp5 = report.get("buffer_projection", {}).get("5", {}).get("total_est_ms", {})
        logger.info(
            "Speech analytics: %d utterances, buffer-5 p50=%sms saved=%s",
            report.get("utterance_count", 0),
            bp5.get("p50"),
            path.name,
        )

    async def stop(self) -> None:
        if self._latency and self.session.latency_report is None:
            await self._finalize_latency_report()
        if self._speech_analytics and not self.session.test_mode:
            await self._finalize_speech_analytics()
        self._latency = None
        self._stop_all()
        self.session.stop()
        await self._broadcast_live_status()

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
        if self._tts:
            self._tts.stop()
            self._tts = None
        self._latest_output_level = {}
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
