"""TTS playback clock and speed telemetry.

Speed this round is queue-depth (frozen). gap_ms is content lag — sum of
source speech durations minus TTS audio elapsed — and is NOT used for speed.

stt→rel / pacer / synth wait are not added to gap. Speed can only close
unplayed TTS audio duration. A high gap while translation is in flight means
unplayed content is not on the play clock yet, not that processing ms were
summed into the number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from hki import config
from hki.live.trace_schema import unix_ms

TTS_SKIPPED = "tts_skipped"
TTS_ERROR = "tts_error"


def pcm_duration_ms(pcm: bytes, sample_rate: int | None = None) -> int:
    rate = sample_rate if sample_rate is not None else config.TTS_SAMPLE_RATE
    if not pcm or rate <= 0:
        return 0
    return int(round(len(pcm) / (2 * rate) * 1000))


def playback_rate_for_depth(depth: int) -> float:
    threshold = config.TTS_PLAYBACK_SPEED_THRESHOLD
    if depth <= threshold:
        return 1.0
    if depth <= config.TTS_PLAYBACK_SPEED_MID_QUEUE:
        return config.TTS_PLAYBACK_SPEED_MID
    return config.TTS_PLAYBACK_SPEED_MAX


def speed_trigger_reason(depth: int) -> str:
    threshold = config.TTS_PLAYBACK_SPEED_THRESHOLD
    mid = config.TTS_PLAYBACK_SPEED_MID_QUEUE
    if depth <= threshold:
        return f"queue<={threshold}"
    if depth <= mid:
        return f"queue<={mid}"
    return f"queue>{mid}"


def audio_duration_after_speed_ms(pcm_1x_ms: int, speed: float) -> int:
    if speed <= 0:
        return int(pcm_1x_ms)
    return int(round(pcm_1x_ms / speed))


def skipped_tts_fields(reason: str = TTS_SKIPPED) -> dict:
    return {
        "tts_play_start_ms": 0,
        "tts_play_end_ms": 0,
        "tts_speed_applied": 0.0,
        "tts_audio_duration_ms": 0,
        "tts_queue_len_at_enqueue": 0,
        "gap_ms_at_enqueue": 0,
        "speed_trigger_reason": reason,
    }


@dataclass
class TtsClip:
    start_mono: float
    end_mono: float
    duration_ms: int
    start_unix_ms: int
    end_unix_ms: int


class TtsPlaybackClock:
    """Canonical sequential TTS play clock (server-side)."""

    def __init__(self) -> None:
        self.source_speech_ms = 0
        self._clips: list[TtsClip] = []
        self.last_end_mono = 0.0

    def reset(self) -> None:
        self.source_speech_ms = 0
        self._clips.clear()
        self.last_end_mono = 0.0

    def add_source_speech_ms(self, speech_ms: int) -> None:
        self.source_speech_ms += max(0, int(speech_ms))

    def tts_elapsed_ms(self, now_mono: float | None = None) -> int:
        now = time.monotonic() if now_mono is None else now_mono
        elapsed = 0
        for clip in self._clips:
            if now >= clip.end_mono:
                elapsed += clip.duration_ms
            elif now <= clip.start_mono:
                break
            else:
                elapsed += int(round((now - clip.start_mono) * 1000))
        return elapsed

    def waiting_count(self, now_mono: float | None = None) -> int:
        """Clips scheduled but not yet started. Currently playing is excluded."""
        now = time.monotonic() if now_mono is None else now_mono
        return sum(1 for clip in self._clips if clip.start_mono > now)

    def gap_ms(self, now_mono: float | None = None) -> int:
        return self.source_speech_ms - self.tts_elapsed_ms(now_mono)

    def playback_until_mono(self) -> float:
        return self.last_end_mono

    def enqueue(
        self,
        pcm: bytes,
        *,
        synth_pending: int,
        composer_pending: int,
        now_mono: float | None = None,
        now_unix_ms: int | None = None,
    ) -> dict:
        now_m = time.monotonic() if now_mono is None else now_mono
        now_u = unix_ms() if now_unix_ms is None else now_unix_ms
        queue_len = (
            self.waiting_count(now_m)
            + max(0, int(synth_pending))
            + max(0, int(composer_pending))
        )
        gap = self.gap_ms(now_m)
        speed = playback_rate_for_depth(queue_len)
        reason = speed_trigger_reason(queue_len)
        pcm_1x = pcm_duration_ms(pcm)
        duration = audio_duration_after_speed_ms(pcm_1x, speed)

        start_mono = max(now_m, self.last_end_mono)
        end_mono = start_mono + (duration / 1000.0)
        start_unix = now_u + int(round((start_mono - now_m) * 1000))
        end_unix = start_unix + duration

        self._clips.append(
            TtsClip(
                start_mono=start_mono,
                end_mono=end_mono,
                duration_ms=duration,
                start_unix_ms=start_unix,
                end_unix_ms=end_unix,
            )
        )
        self.last_end_mono = end_mono
        return {
            "tts_play_start_ms": start_unix,
            "tts_play_end_ms": end_unix,
            "tts_speed_applied": speed,
            "tts_audio_duration_ms": duration,
            "tts_queue_len_at_enqueue": queue_len,
            "gap_ms_at_enqueue": gap,
            "speed_trigger_reason": reason,
        }
