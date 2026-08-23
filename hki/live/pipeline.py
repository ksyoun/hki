"""Orchestrates audio capture, transcription, and translation."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import datetime, timezone

import numpy as np

from hki import config
from hki.live.audio import AudioCapture, peak_db, pcm_to_base64, rms_db
from hki.live.broadcaster import Broadcaster
from hki.live.file_replay import apply_gain
from hki.live.latency import LatencyProfiler
from hki.live.ko_sentence_translator import KoSentenceTranslator
from hki.live.session import LiveSession, SessionState, TranslationPipelineMode
from hki.live.transcribe import TranscriptionClient
from hki.live.translate import Translator
from hki.live.tts import TTSClient
from hki.live.output_composer import OutputComposer
from hki.live.output_composer_v2 import OutputComposerV2
from hki.live.release_pacer import ReleaseItem

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def legacy_trace_from_item(item: ReleaseItem) -> dict:
    original = item.ko_summary or ""
    corrected = item.ko_corrected or original
    return {
        "timestamp": _utcnow_iso(),
        "fragment_ids": list(item.item_ids),
        "original_stt": original,
        "action": "release",
        "through_index": len(item.item_ids),
        "ko_corrected": corrected,
        "stt_repair": bool(item.stt_repair),
        "release_reason": item.release_reason or "release",
        "translation": item.es,
        "joined_preview": item.joined_preview or "",
        "latency_understand": 0,
        "latency_translate": 0,
        "latency_recombine": int(item.latency_recombine or 0),
        "repair_rejected": bool(item.repair_rejected),
        "anchor_repair": bool(item.anchor_repair),
        "had_incierto": bool(item.had_incierto),
        "recombine_flags": list(item.recombine_flags),
        "release_latency_ms": int(item.release_latency_ms or 0),
        "consume": int(item.consume or 0),
    }


def legacy_v2_trace_from_item(item: ReleaseItem) -> dict:
    trace = legacy_trace_from_item(item)
    trace.update(
        {
            "should_wait": item.should_wait,
            "grace_ms": int(item.grace_ms or 0),
            "b_arrived": item.b_arrived,
            "b_delta_ms": item.b_delta_ms,
            "released_as_single": bool(item.released_as_single),
            "single_kind": item.single_kind or "",
        }
    )
    return trace


class LivePipeline:
    def __init__(self, session: LiveSession, broadcaster: Broadcaster):
        self.session = session
        self.broadcaster = broadcaster
        self._audio: AudioCapture | None = None
        self._transcriber: TranscriptionClient | None = None
        self._translator: Translator | None = None
        self._tts: TTSClient | None = None
        self._output_composer: OutputComposer | None = None
        self._output_composer_v2: OutputComposerV2 | None = None
        self._sentence_translator: KoSentenceTranslator | None = None
        self._tasks: list[asyncio.Task] = []
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._level_task: asyncio.Task | None = None
        self._level_lock = threading.Lock()
        self._latest_level: dict = {}
        self._level_peak_hold = -60.0
        self._level_peak_hold_until = 0.0
        self._tts_output_peak = -60.0
        self._tts_output_phrase = ""
        self._tts_playback_until = 0.0
        self._tts_synth_active = False
        self._latency: LatencyProfiler | None = None
        self._pause_in_progress = False
        self._tts_batch_item_ids: dict[str, list[str]] = {}
        self._translated_at: dict[str, float] = {}

    def _has_audience(self) -> bool:
        return self.broadcaster.audience_count >= config.MIN_AUDIENCE_COUNT

    def _should_generate_tts(self) -> bool:
        return config.TTS_ENABLED and self.session.speaker_subscribers > 0

    def get_gate_status(self) -> dict:
        """Runtime STT/translation gate — streaming + enough audience."""
        gate = self._has_audience()
        streaming = self.session.state == SessionState.STREAMING
        return {
            "audience_gate_open": gate,
            "transcription_active": streaming and gate,
            "translation_active": streaming and gate,
        }

    def get_input_level_metrics(self) -> dict:
        level = self._snapshot_input_level()
        return {
            "input_peak_db": level["peak_db"],
            "input_rms_db": level["rms_db"],
            "input_clipping": level["clipping"],
        }

    def _snapshot_input_level(self) -> dict:
        now = time.monotonic()
        with self._level_lock:
            level = dict(self._latest_level) if self._latest_level else {}
            live_peak = float(level.get("peak_db", -60.0))
            if now < self._level_peak_hold_until:
                peak = max(live_peak, self._level_peak_hold)
            else:
                peak = live_peak
        return {
            "rms_db": float(level.get("rms_db", -60.0)),
            "peak_db": peak,
            "clipping": bool(level.get("clipping", False)),
        }

    def _on_level(self, level: dict) -> None:
        peak = float(level.get("peak_db", -60.0))
        now = time.monotonic()
        hold_sec = config.LEVEL_PEAK_HOLD_MS / 1000.0
        with self._level_lock:
            self._latest_level = level
            if peak >= self._level_peak_hold:
                self._level_peak_hold = peak
                self._level_peak_hold_until = now + hold_sec

    async def broadcast_status(self) -> None:
        self.session.audience_count = self.broadcaster.audience_count
        await self.broadcaster.broadcast(
            {
                "type": "status",
                **self.session.build_live_status(config.TTS_ENABLED),
                **self.get_gate_status(),
                **self.get_translation_prompt_info(),
                **self.get_voice_backlog_metrics(),
                **self.get_input_level_metrics(),
            }
        )

    def get_voice_backlog_metrics(self) -> dict:
        composer = 0
        release_q = 0
        translator = 0
        if self._output_composer:
            composer += self._output_composer.pending_count()
            release_q += self._output_composer.release_queue_depth()
        if self._translator:
            translator += self._translator.pending_count()
        if self._sentence_translator:
            composer += self._sentence_translator.pending_count()
            release_q += self._sentence_translator.release_queue_depth()
            translator += self._sentence_translator.upstream_pending_count()
        tts = self._tts.pending_count() if self._tts else 0
        return {
            "tts_prep_pending": composer,
            "output_release_pending": release_q,
            "tts_pending": tts,
            "translator_pending": translator,
            "voice_backlog": composer + tts,
            "tts_playback_speed_threshold": config.TTS_PLAYBACK_SPEED_THRESHOLD,
            "tts_playback_speed_max": config.TTS_PLAYBACK_SPEED_MAX,
            "caption_max_lines": config.CAPTION_MAX_LINES,
        }

    def _on_pcm(self, pcm: bytes) -> None:
        if self.session.state == SessionState.STREAMING:
            try:
                self._audio_queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    async def _level_broadcaster(self) -> None:
        interval = config.LEVEL_METER_INTERVAL_MS / 1000
        while self.session.state in (
            SessionState.STREAMING,
            SessionState.PAUSED,
            SessionState.MONITORING,
        ):
            level = self._snapshot_input_level()
            await self.broadcaster.broadcast({"type": "level", **level})
            await asyncio.sleep(interval)

    async def _output_level_broadcaster(self) -> None:
        interval = config.LEVEL_METER_INTERVAL_MS / 1000
        while self.session.state in (SessionState.STREAMING, SessionState.PAUSED):
            now = time.monotonic()
            playing = now < self._tts_playback_until
            synth = self._tts_synth_active
            active = playing or synth

            if active:
                if playing:
                    wobble = 2.5 * math.sin(now * 12.0)
                    peak = min(-3.0, self._tts_output_peak + wobble)
                    phase = "playing"
                else:
                    peak = self._tts_output_peak
                    phase = "synth"
                await self.broadcaster.broadcast(
                    {
                        "type": "output_level",
                        "peak_db": peak,
                        "active": True,
                        "phrase": self._tts_output_phrase,
                        "phase": phase,
                    }
                )
            else:
                await self.broadcaster.broadcast(
                    {
                        "type": "output_level",
                        "peak_db": -60.0,
                        "active": False,
                        "phrase": "",
                        "phase": "idle",
                    }
                )
            await asyncio.sleep(interval)

    async def _on_output_level(self, level: dict) -> None:
        if level.get("phrase"):
            self._tts_output_phrase = level["phrase"]
        if level.get("synth"):
            self._tts_synth_active = bool(level.get("active"))
        elif not level.get("active"):
            self._tts_synth_active = False
        if level.get("active") and level.get("peak_db") is not None:
            self._tts_output_peak = float(level["peak_db"])

    async def _on_tts_audio(self, item_id: str, text: str, pcm: bytes) -> None:
        self._tts_synth_active = False
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        if len(samples):
            self._tts_output_peak = peak_db(samples)
        phrase = text[:80] + ("…" if len(text) > 80 else "")
        self._tts_output_phrase = phrase
        duration = len(pcm) / (2 * config.TTS_SAMPLE_RATE)
        now = time.monotonic()
        self._tts_playback_until = max(now, self._tts_playback_until) + duration
        backlog = self.get_voice_backlog_metrics()
        item_ids = self._tts_batch_item_ids.pop(item_id, [item_id])
        await self.broadcaster.broadcast(
            {
                "type": "tts",
                "item_id": item_id,
                "item_ids": item_ids,
                "es": text,
                "audio": pcm_to_base64(pcm),
                "format": "pcm",
                "rate": config.TTS_SAMPLE_RATE,
                **backlog,
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
        if self._latency:
            self._latency.on_transcript_delta(item_id, text, time.monotonic())
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
        if self._latency:
            self._latency.on_transcript_completed(
                item_id, text, time.monotonic(), self.session.test_playback_sec
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
        live = (
            self.session.state == SessionState.STREAMING and self._has_audience()
        )
        if live and self._translator:
            await self._translator.on_transcript_completed(item_id, text)
        if live and self._sentence_translator:
            await self._sentence_translator.on_transcript_completed(item_id, text)

    def _on_speech_started(self) -> None:
        if self._sentence_translator:
            self._sentence_translator.on_speech_started()

    def _on_speech_stopped(self) -> None:
        if self._sentence_translator:
            self._sentence_translator.on_speech_stopped()

    async def _on_translation(self, item_id: str, ko: str, es: str) -> None:
        if self._latency:
            self._latency.on_translation(item_id, time.monotonic())
        self._translated_at[item_id] = time.monotonic()
        es_stripped = (es or "").strip()
        if es_stripped and es_stripped != "—":
            await self.broadcaster.broadcast(
                {
                    "type": "translation_draft",
                    "item_id": item_id,
                    "ko": ko,
                    "es": es_stripped,
                }
            )
        if self._output_composer:
            await self._output_composer.add(item_id, ko, es)
        if self._output_composer_v2:
            await self._output_composer_v2.add(item_id, ko, es)

    def _stamp_release_latency(self, item: ReleaseItem) -> None:
        now = time.monotonic()
        starts = [
            self._translated_at[i]
            for i in item.item_ids
            if i in self._translated_at
        ]
        if starts:
            item.translated_at_mono = min(starts)
            item.release_latency_ms = int((now - item.translated_at_mono) * 1000)

    async def _on_legacy_release(self, item: ReleaseItem) -> None:
        self._stamp_release_latency(item)
        self.session.add_legacy_translation(item.es)
        self.session.add_legacy_trace(legacy_trace_from_item(item))
        if not config.live_pipeline_is_sentence():
            await self._publish_live_release(item)

    async def _on_legacy_v2_release(self, item: ReleaseItem) -> None:
        self.session.add_legacy_v2_translation(item.es)
        self.session.add_legacy_v2_trace(legacy_v2_trace_from_item(item))

    def _on_legacy_v2_event(self, event: dict) -> None:
        self.session.add_legacy_v2_window_event(event)

    async def _on_sentence_release(self, item: ReleaseItem) -> None:
        self.session.add_sentence_translation(item.es)
        if config.live_pipeline_is_sentence():
            await self._publish_live_release(item)

    async def _publish_live_release(self, item: ReleaseItem) -> None:
        self.session.add_final_translation(item.es)
        payload: dict = {
            "type": "translation",
            "item_id": item.batch_id,
            "item_ids": item.item_ids,
            "ko": item.ko_summary,
            "es": item.es,
            "final": True,
            "batch": True,
        }
        if item.repair_rejected:
            payload["repair_rejected"] = True
            payload["anchor_repair"] = item.anchor_repair
        if item.recombine_flags:
            payload["recombine_flags"] = item.recombine_flags
        if item.had_incierto:
            payload["had_incierto"] = True
        await self.broadcaster.broadcast(payload)
        if self._tts and self._should_generate_tts():
            self._tts_batch_item_ids[item.batch_id] = list(item.item_ids)
            await self._tts.speak(item.batch_id, item.es)

    async def _status_broadcaster(self) -> None:
        while self.session.state in (
            SessionState.STREAMING,
            SessionState.PAUSED,
            SessionState.MONITORING,
        ):
            await self.broadcast_status()
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
                    "rms_db": rms_db(samples),
                    "peak_db": peak_db(samples),
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

    def _sync_input_device_from_audio(self) -> None:
        if self._audio:
            self.session.device_index = self._audio.device_index
            self.session.input_device_name = self._audio.device_name or ""

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
            self._sync_input_device_from_audio()
        except Exception:
            self.session.stop()
            self._audio = None
            logger.exception("Input monitor failed to start")
            raise

        self._start_level_task()
        logger.info(
            "Input monitor active: %s gain=%.2f",
            self.session.input_device_name or self.session.device_index,
            self.session.gain,
        )

    def stop_monitor(self) -> None:
        self._stop_audio()
        if self.session.state == SessionState.MONITORING:
            self.session.stop()

    def _apply_pipeline_mode_from_config(self) -> None:
        mode = config.translation_pipeline_status()
        if mode == "both":
            self.session.translation_pipeline = TranslationPipelineMode.BOTH
        elif mode == "sentence":
            self.session.translation_pipeline = TranslationPipelineMode.SENTENCE
        else:
            self.session.translation_pipeline = TranslationPipelineMode.LEGACY

    def _spawn_clients(self) -> None:
        self._apply_pipeline_mode_from_config()
        self._transcriber = TranscriptionClient(
            on_delta=self._on_transcript_delta,
            on_completed=self._on_transcript_completed,
            on_speech_started=self._on_speech_started,
            on_speech_stopped=self._on_speech_stopped,
        )
        if config.PIPELINE_LEGACY_ENABLED:
            self._translator = Translator(
                on_translation=self._on_translation,
                context=self.session.translation_context,
                sermon_mode=self.session.sermon_on,
                on_usage=lambda p, c: self.session.add_token_usage(
                    "legacy", p, c, kind="translate"
                ),
            )
            self._output_composer = OutputComposer(
                on_release=self._on_legacy_release,
                on_usage=lambda p, c: self.session.add_token_usage(
                    "legacy", p, c, kind="recombine"
                ),
            )
            self._output_composer.set_context(self.session.translation_context)
            self._output_composer.set_sermon_mode(self.session.sermon_on)
            if config.PIPELINE_LEGACY_V2_ENABLED:
                self._output_composer_v2 = OutputComposerV2(
                    on_release=self._on_legacy_v2_release,
                    on_usage=lambda p, c: self.session.add_token_usage(
                        "legacy_v2", p, c, kind="recombine"
                    ),
                    on_event=self._on_legacy_v2_event,
                )
                self._output_composer_v2.set_context(self.session.translation_context)
                self._output_composer_v2.set_sermon_mode(self.session.sermon_on)
        if config.PIPELINE_SENTENCE_ENABLED:
            self._sentence_translator = KoSentenceTranslator(
                on_release=self._on_sentence_release,
                context=self.session.translation_context,
                sermon_mode=self.session.sermon_on,
                on_usage=lambda p, c, kind="": self.session.add_token_usage(
                    "sentence", p, c, kind=kind
                ),
                on_trace=self.session.add_sentence_trace,
                manuscript=self.session.manuscript,
            )
        if config.TTS_ENABLED:
            self._tts = TTSClient(
                on_audio=self._on_tts_audio,
                on_level=self._on_output_level,
            )
        self._latency = LatencyProfiler()

    def _start_pipeline_tasks(self, *extra_coros) -> None:
        coros = [self._transcriber.run()]
        if self._translator:
            coros.append(self._translator.run())
        if self._output_composer:
            coros.append(self._output_composer.run())
        if self._output_composer_v2:
            coros.append(self._output_composer_v2.run())
        if self._sentence_translator:
            coros.append(self._sentence_translator.run())
        coros.extend(
            [
                self._audio_forwarder(),
                self._level_broadcaster(),
                *extra_coros,
                self._status_broadcaster(),
            ]
        )
        if self._tts:
            coros.append(self._tts.run())
            coros.append(self._output_level_broadcaster())
        self._tasks = [asyncio.create_task(c) for c in coros]

    async def start_streaming(self) -> None:
        self._stop_all()
        self.session.clear_session_log()
        self.session.session_label = "transmision"
        self.session.test_mode = False

        self.session.start_streaming()
        if config.AUTO_SERMON_ON and self.session.context_ready:
            self.session.sermon_on = True

        self._spawn_clients()

        self._audio = AudioCapture(
            device_index=self.session.device_index,
            gain=self.session.gain,
            on_pcm=self._on_pcm,
            on_level=self._on_level,
        )

        try:
            self._audio.start()
            self._sync_input_device_from_audio()
        except Exception:
            logger.exception("Streaming audio failed to start")
            await self.stop()
            raise

        self._start_pipeline_tasks()
        await self.broadcast_status()
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

        self.session.start_streaming()
        if config.AUTO_SERMON_ON and self.session.context_ready:
            self.session.sermon_on = True
        self._spawn_clients()
        self._start_pipeline_tasks(self._file_replay_loop(pcm, duration_sec))
        await self.broadcast_status()
        await self.broadcaster.broadcast({"type": "resumed"})

    async def pause(self) -> None:
        if self.session.state != SessionState.STREAMING:
            return
        if self._pause_in_progress:
            return
        self._pause_in_progress = True
        try:
            self.session.pause()
            await self.broadcaster.broadcast({"type": "pausing"})

            if self._translator:
                await self._translator.drain()
            if self._sentence_translator:
                await self._sentence_translator.drain()
            if self._output_composer:
                await self._output_composer.drain()
            if self._output_composer_v2:
                await self._output_composer_v2.drain()
            if self._tts and config.TTS_ENABLED:
                await self._tts.drain()

            await self.broadcaster.broadcast({"type": "paused"})
        finally:
            self._pause_in_progress = False

    async def resume(self) -> None:
        if self._pause_in_progress:
            return
        self.session.resume()
        await self.broadcast_status()
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

    async def stop(self) -> None:
        if self._latency and self.session.latency_report is None:
            await self._finalize_latency_report()
        if self.session.state in (SessionState.STREAMING, SessionState.PAUSED):
            if self._translator:
                await self._translator.drain(timeout=45.0)
            if self._sentence_translator:
                await self._sentence_translator.drain(timeout=90.0)
            if self._output_composer:
                await self._output_composer.drain(timeout=90.0)
            if self._output_composer_v2:
                await self._output_composer_v2.drain(timeout=90.0)
            if self._tts and config.TTS_ENABLED:
                await self._tts.drain()
        self._latency = None
        self._stop_all()
        self.session.stop()
        await self.broadcast_status()
        try:
            await self.ensure_input_monitor()
        except ValueError as e:
            logger.info("Input monitor skipped after stop: %s", e)
        except Exception:
            logger.exception("Input monitor restart after stop failed")

    def set_gain(self, gain: float) -> None:
        self.session.gain = gain
        if self._audio:
            self._audio.set_gain(gain)

    def apply_translation_context(self) -> None:
        """Push session translation_context to live Translator / OutputComposer."""
        if self._translator:
            self._translator.set_context(self.session.translation_context)
            logger.info(
                "Translator context updated (ready=%s, sermon_on=%s)",
                self.session.context_ready,
                self.session.sermon_on,
            )
        if self._sentence_translator:
            self._sentence_translator.set_context(self.session.translation_context)
            self._sentence_translator.set_manuscript(self.session.manuscript)
            logger.info(
                "Sentence translator context updated (ready=%s, sermon_on=%s)",
                self.session.context_ready,
                self.session.sermon_on,
            )
        if self._output_composer:
            self._output_composer.set_context(self.session.translation_context)
            self._output_composer.set_sermon_mode(self.session.sermon_on)
        if self._output_composer_v2:
            self._output_composer_v2.set_context(self.session.translation_context)
            self._output_composer_v2.set_sermon_mode(self.session.sermon_on)

    async def set_sermon_mode(self, sermon_on: bool) -> None:
        self.session.sermon_on = sermon_on
        if self._translator:
            self._translator.set_sermon_mode(sermon_on)
        if self._sentence_translator:
            self._sentence_translator.set_sermon_mode(sermon_on)
        if self._output_composer:
            self._output_composer.set_sermon_mode(sermon_on)
        if self._output_composer_v2:
            self._output_composer_v2.set_sermon_mode(sermon_on)
        await self.broadcaster.broadcast(
            {
                "type": "sermon_mode",
                "sermon_on": sermon_on,
                **self.get_translation_prompt_info(),
            }
        )
        await self.broadcast_status()

    def get_translation_prompt_info(self) -> dict:
        if config.live_pipeline_is_sentence():
            if self._sentence_translator:
                return self._sentence_translator.describe_prompt()
            from hki.live.sentence_prompts import describe_sentence_prompt

            context = self.session.translation_context if self.session.sermon_on else None
            info = describe_sentence_prompt(self.session.sermon_on, context)
            info["translator_live"] = False
            return info
        if self._translator:
            return self._translator.describe_prompt()
        from hki.live.translate import describe_translation_prompt

        context = self.session.translation_context if self.session.sermon_on else None
        info = describe_translation_prompt(self.session.sermon_on, context)
        info["translator_live"] = False
        return info

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
        if self._sentence_translator:
            self._sentence_translator.stop_sync()
            self._sentence_translator = None
        if self._tts:
            self._tts.stop()
            self._tts = None
        if self._output_composer:
            self._output_composer.stop_sync()
            self._output_composer = None
        if self._output_composer_v2:
            self._output_composer_v2.stop_sync()
            self._output_composer_v2 = None
        self._translated_at.clear()
        self._tts_batch_item_ids.clear()
        self._tts_output_peak = -60.0
        self._tts_output_phrase = ""
        self._tts_playback_until = 0.0
        self._tts_synth_active = False
        with self._level_lock:
            self._latest_level = {}
        self._level_peak_hold = -60.0
        self._level_peak_hold_until = 0.0
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
