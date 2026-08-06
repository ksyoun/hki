"""Live session state management."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from hki import config


class SessionState(enum.Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    STREAMING = "streaming"
    PAUSED = "paused"


@dataclass
class LiveSession:
    state: SessionState = SessionState.IDLE
    bible_text: str = ""
    manuscript: str = ""
    device_index: int | None = None
    gain: float = 1.0

    # Broadcast timer
    _started_at: float | None = None
    _paused_at: float | None = None
    _accumulated_pause: float = 0.0

    # Session log (persists after stop until next broadcast/test)
    transcript_log: list[str] = field(default_factory=list)
    translation_final_log: list[str] = field(default_factory=list)
    session_label: str = ""
    latency_report: dict | None = None

    # Translation context (Guardar) — once ready, locked until server restart
    translation_context: dict | None = None
    passage_display: dict | None = None
    context_ready: bool = False

    # File test replay
    test_mode: bool = False
    test_filename: str = ""
    test_duration_sec: float = 0.0
    test_playback_sec: float = 0.0

    # Audience / speaker stats (synced from broadcaster on status updates)
    audience_count: int = 0
    speaker_subscribers: int = 0

    def configure(
        self,
        device_index: int | None = None,
        gain: float | None = None,
    ) -> None:
        if device_index is not None and device_index >= 0:
            self.device_index = device_index
        if gain is not None:
            self.gain = gain

    def start_streaming(self) -> None:
        self.state = SessionState.STREAMING
        self._started_at = time.monotonic()
        self._paused_at = None
        self._accumulated_pause = 0.0

    def start_monitoring(self) -> None:
        self.state = SessionState.MONITORING

    def pause(self) -> None:
        if self.state == SessionState.STREAMING:
            self.state = SessionState.PAUSED
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self.state == SessionState.PAUSED and self._paused_at is not None:
            self._accumulated_pause += time.monotonic() - self._paused_at
            self._paused_at = None
            self.state = SessionState.STREAMING

    def stop(self) -> None:
        self.state = SessionState.IDLE
        self._started_at = None
        self._paused_at = None
        self._accumulated_pause = 0.0
        self.test_mode = False
        self.test_filename = ""
        self.test_duration_sec = 0.0
        self.test_playback_sec = 0.0

    @property
    def elapsed_sec(self) -> int:
        if self._started_at is None:
            return 0
        if self.state == SessionState.PAUSED and self._paused_at is not None:
            elapsed = self._paused_at - self._started_at - self._accumulated_pause
        else:
            elapsed = time.monotonic() - self._started_at - self._accumulated_pause
        return max(0, int(elapsed))

    def clear_session_log(self) -> None:
        self.transcript_log.clear()
        self.translation_final_log.clear()
        self.session_label = ""
        self.latency_report = None

    def add_transcript(self, text: str) -> None:
        text = text.strip()
        if text:
            self.transcript_log.append(text)

    def add_final_translation(self, text: str) -> None:
        text = text.strip()
        if text:
            self.translation_final_log.append(text)

    @property
    def has_log(self) -> bool:
        return bool(self.transcript_log or self.translation_final_log)

    def to_log(self) -> dict:
        return {
            "session_label": self.session_label,
            "transcripts": list(self.transcript_log),
            "translations": list(self.translation_final_log),
            "has_log": self.has_log,
        }

    def set_speaker_subscribers(self, count: int) -> None:
        self.speaker_subscribers = max(0, count)

    def build_live_status(self, tts_available: bool) -> dict:
        audience = self.audience_count
        min_audience = config.MIN_AUDIENCE_COUNT
        audience_ready = audience >= min_audience
        status = self.to_status()
        status.update(
            {
                "tts_available": tts_available,
                "min_audience_count": min_audience,
                "transcription_active": audience_ready,
                "translation_active": audience_ready,
                "tts_active": tts_available and self.speaker_subscribers > 0,
            }
        )
        return status

    def set_translation_context(
        self,
        bible_text: str,
        manuscript: str,
        context: dict,
        passage_display: dict,
    ) -> None:
        self.bible_text = bible_text
        self.manuscript = manuscript
        self.translation_context = context
        self.passage_display = passage_display
        self.context_ready = True

    def clear_translation_context(self) -> None:
        self.bible_text = ""
        self.manuscript = ""
        self.translation_context = None
        self.passage_display = None
        self.context_ready = False

    def to_status(self) -> dict:
        return {
            "state": self.state.value,
            "elapsed_sec": self.elapsed_sec,
            "gain": self.gain,
            "device_index": self.device_index,
            "test_mode": self.test_mode,
            "test_filename": self.test_filename,
            "test_duration_sec": self.test_duration_sec,
            "test_playback_sec": self.test_playback_sec,
            "has_log": self.has_log,
            "has_latency_report": self.latency_report is not None,
            "audience_count": self.audience_count,
            "speaker_subscribers": self.speaker_subscribers,
            "bible_text": self.bible_text if self.context_ready else "",
            "manuscript": self.manuscript if self.context_ready else "",
            "context_ready": self.context_ready,
            "context_generated_at": (
                self.translation_context.get("generated_at")
                if self.translation_context
                else None
            ),
            "context_display": self._context_display_payload(),
            "passage_display": self.passage_display,
        }

    def _context_display_payload(self) -> dict | None:
        from hki.live.context import format_context_display

        if not self.translation_context:
            return None
        return format_context_display(self.translation_context)
