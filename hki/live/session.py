"""Live session state management."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


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

    # Stats
    listener_count: int = 0
    translation_history: list[dict] = field(default_factory=list)

    # Session log (persists after stop until next broadcast/test)
    transcript_log: list[str] = field(default_factory=list)
    translation_final_log: list[str] = field(default_factory=list)
    session_label: str = ""
    latency_report: dict | None = None

    # File test replay
    test_mode: bool = False
    test_filename: str = ""
    test_duration_sec: float = 0.0
    test_playback_sec: float = 0.0

    def configure(
        self,
        bible_text: str = "",
        manuscript: str = "",
        device_index: int | None = None,
        gain: float | None = None,
    ) -> None:
        self.bible_text = bible_text
        self.manuscript = manuscript
        if device_index is not None:
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

    def add_translation(self, ko: str, es: str, tier: str, item_id: str) -> None:
        entry = {"ko": ko, "es": es, "tier": tier, "item_id": item_id}
        self.translation_history.append(entry)
        if len(self.translation_history) > 20:
            self.translation_history = self.translation_history[-20:]

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

    def to_status(self) -> dict:
        return {
            "state": self.state.value,
            "elapsed_sec": self.elapsed_sec,
            "listeners": self.listener_count,
            "gain": self.gain,
            "device_index": self.device_index,
            "test_mode": self.test_mode,
            "test_filename": self.test_filename,
            "test_duration_sec": self.test_duration_sec,
            "test_playback_sec": self.test_playback_sec,
            "has_log": self.has_log,
            "has_latency_report": self.latency_report is not None,
        }
