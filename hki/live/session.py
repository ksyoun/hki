"""Live session state management."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from hki import config
from hki.live.trace_schema import (
    RELEASE_REASONS,
    TRACE_KEYS,
    audio_start_source_counts,
    parse_release_trace,
)


class SessionState(enum.Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    STREAMING = "streaming"
    PAUSED = "paused"


@dataclass
class LiveSession:
    state: SessionState = SessionState.IDLE
    manuscript: str = ""
    device_index: int | None = None
    input_device_name: str = ""
    gain: float = 1.0

    # Broadcast timer
    _started_at: float | None = None
    _paused_at: float | None = None
    _accumulated_pause: float = 0.0

    # Session log (persists after stop until next broadcast/test)
    transcript_log: list[str] = field(default_factory=list)
    translation_final_log: list[str] = field(default_factory=list)
    translation_legacy_log: list[str] = field(default_factory=list)
    legacy_traces: list[dict] = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)
    session_label: str = ""
    latency_report: dict | None = None

    # Translation context (Contextualizar) — once ready, locked until Liberar / reset-context
    translation_context: dict | None = None
    passage_display: dict | None = None
    context_ready: bool = False
    sermon_on: bool = False

    # File test replay
    test_mode: bool = False
    test_filename: str = ""
    test_duration_sec: float = 0.0
    test_playback_sec: float = 0.0

    # Audience / speaker stats (synced from broadcaster on status updates)
    audience_count: int = 0
    speaker_subscribers: int = 0

    # Prepared alabanza lyrics (operator lookup/save). Cursor is live-only.
    lyrics_songs: list[dict] = field(default_factory=list)
    lyrics_song_index: int | None = None
    lyrics_slide_index: int = -1
    _lyrics_seq: int = 0

    @property
    def bible_text(self) -> str:
        """Korean passage text — sourced from passage_display.ko only."""
        if not self.passage_display:
            return ""
        return str(self.passage_display.get("ko") or "").strip()

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
        self.sermon_on = False
        self.reset_lyrics_cursor()

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
        self.sermon_on = False
        self.test_mode = False
        self.test_filename = ""
        self.test_duration_sec = 0.0
        self.test_playback_sec = 0.0
        self.reset_lyrics_cursor()

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
        self.translation_legacy_log.clear()
        self.legacy_traces.clear()
        self.token_usage = {}
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

    def add_legacy_translation(self, text: str) -> None:
        text = text.strip()
        if text:
            self.translation_legacy_log.append(text)

    def add_legacy_trace(self, trace: dict) -> int:
        self.legacy_traces.append(parse_release_trace(trace))
        return len(self.legacy_traces) - 1

    def patch_legacy_trace(self, index: int, fields: dict) -> None:
        if index < 0 or index >= len(self.legacy_traces):
            return
        row = self.legacy_traces[index]
        for key, value in fields.items():
            if key in TRACE_KEYS:
                row[key] = value

    def _release_stats(self, traces: list[dict]) -> dict:
        counts: dict[str, int] = {}
        total = 0
        for trace in traces:
            if trace.get("action") != "release" or not (trace.get("translation") or "").strip():
                continue
            reason = str(trace.get("release_reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
            total += 1
        return {"total": total, "counts": counts}

    def legacy_release_stats(self) -> dict:
        return self._release_stats(self.legacy_traces)

    def _release_comment_line(
        self, traces: list[dict], preferred_keys: tuple[str, ...]
    ) -> str:
        stats = self._release_stats(traces)
        total = stats["total"]
        if not total:
            return ""
        parts = []
        preferred = set(preferred_keys)
        for key in preferred_keys:
            n = stats["counts"].get(key, 0)
            if n:
                pct = int(round(100.0 * n / total))
                parts.append(f"{key}: {pct}%")
        extra = [
            f"{k}: {v}"
            for k, v in sorted(stats["counts"].items())
            if k not in preferred
        ]
        parts.extend(extra)
        if not parts:
            return ""
        return "Release: " + "  ".join(parts)

    def add_token_usage(
        self,
        bucket: str,
        prompt: int,
        completion: int,
        *,
        kind: str = "",
    ) -> None:
        if bucket != "legacy":
            return
        slot = self.token_usage.setdefault(
            bucket,
            {
                "prompt": 0,
                "completion": 0,
                "calls_translate": 0,
                "calls_recombine": 0,
                "calls": 0,
            },
        )
        slot["prompt"] += max(0, int(prompt))
        slot["completion"] += max(0, int(completion))
        if kind == "recombine":
            slot["calls_recombine"] += 1
        else:
            slot["calls_translate"] += 1

    def _audio_start_comment(self, traces: list[dict], prefix: str) -> str:
        counts = audio_start_source_counts(traces)
        if not any(counts.values()):
            return ""
        return (
            f"{prefix} audio_start: speech_started {counts['speech_started']} / "
            f"first_delta {counts['first_delta']} / fallback {counts['fallback']}"
        )

    def token_comment_lines(self) -> list[str]:
        legacy = self.token_usage.get("legacy") or {}
        if not legacy and not self.legacy_traces:
            return []
        lines: list[str] = []
        if legacy:
            lines.extend(
                [
                    "- tokens -",
                    "(STT / Contextualizar / TTS no incluidos)",
                ]
            )
            lines.append(
                "Clásico: {p} in / {c} out  (traducir {t} + recombine {r})".format(
                    p=legacy.get("prompt", 0),
                    c=legacy.get("completion", 0),
                    t=legacy.get("calls_translate", 0),
                    r=legacy.get("calls_recombine", 0),
                )
            )
        classic_release = self._release_comment_line(
            self.legacy_traces,
            RELEASE_REASONS,
        )
        if classic_release:
            lines.append("Clásico " + classic_release)
        classic_audio = self._audio_start_comment(self.legacy_traces, "Clásico")
        if classic_audio:
            lines.append(classic_audio)
        return lines

    @property
    def has_log(self) -> bool:
        return bool(
            self.transcript_log
            or self.translation_final_log
            or self.translation_legacy_log
            or self.legacy_traces
        )

    def to_log(self) -> dict:
        return {
            "session_label": self.session_label,
            "transcripts": list(self.transcript_log),
            "translations": list(self.translation_final_log),
            "translations_legacy": list(self.translation_legacy_log),
            "legacy_traces": list(self.legacy_traces),
            "legacy_release_stats": self.legacy_release_stats(),
            "token_usage": dict(self.token_usage),
            "token_comment": "\n".join(self.token_comment_lines()),
            "has_log": self.has_log,
        }

    def set_speaker_subscribers(self, count: int) -> None:
        self.speaker_subscribers = max(0, count)

    def lyrics_ready(self) -> bool:
        return bool(self.lyrics_songs)

    def reset_lyrics_cursor(self) -> None:
        self.lyrics_song_index = None
        self.lyrics_slide_index = -1

    def next_lyrics_item_id(self, kind: str) -> str:
        self._lyrics_seq += 1
        return f"lyrics-{kind}-{self._lyrics_seq}"

    def set_lyrics_songs(self, songs: list[dict]) -> None:
        self.lyrics_songs = list(songs)
        self.reset_lyrics_cursor()

    def clear_lyrics(self) -> None:
        self.lyrics_songs = []
        self.reset_lyrics_cursor()

    def lyrics_status(self) -> dict:
        from hki.live.lyrics import lyrics_chip_payload

        return {
            "lyrics_ready": self.lyrics_ready(),
            "lyrics_songs": lyrics_chip_payload(self.lyrics_songs),
            "lyrics_song_index": self.lyrics_song_index,
            "lyrics_slide_index": self.lyrics_slide_index,
        }

    def build_live_status(self, tts_available: bool) -> dict:
        """Session fields for live status. Gate flags come from pipeline.get_gate_status()."""
        status = self.to_status()
        status.update(
            {
                "tts_available": tts_available,
                "min_audience_count": config.MIN_AUDIENCE_COUNT,
                "tts_active": tts_available and self.speaker_subscribers > 0,
            }
        )
        return status

    def set_translation_context(
        self,
        manuscript: str,
        context: dict,
        passage_display: dict,
    ) -> None:
        self.manuscript = manuscript
        self.translation_context = context
        self.passage_display = passage_display
        self.context_ready = True

    def clear_translation_context(self) -> None:
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
            "input_device_name": self.input_device_name,
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
            "sermon_on": self.sermon_on,
            "translation_pipeline": "classic",
            **self.lyrics_status(),
        }

    def _context_display_payload(self) -> dict | None:
        from hki.live.context import format_context_display

        if not self.translation_context:
            return None
        return format_context_display(self.translation_context)
