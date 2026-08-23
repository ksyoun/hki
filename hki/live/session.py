"""Live session state management."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from hki import config


class TranslationPipelineMode(enum.Enum):
    LEGACY = "legacy"
    SENTENCE = "sentence"
    BOTH = "both"


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
    translation_sentence_log: list[str] = field(default_factory=list)
    sentence_traces: list[dict] = field(default_factory=list)
    legacy_traces: list[dict] = field(default_factory=list)
    translation_legacy_v2_log: list[str] = field(default_factory=list)
    legacy_v2_traces: list[dict] = field(default_factory=list)
    legacy_v2_window_events: list[dict] = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)
    session_label: str = ""
    latency_report: dict | None = None

    # Translation context (Contextualizar) — once ready, locked until Liberar / reset-context
    translation_context: dict | None = None
    passage_display: dict | None = None
    context_ready: bool = False
    sermon_on: bool = False
    translation_pipeline: TranslationPipelineMode = TranslationPipelineMode.LEGACY

    # File test replay
    test_mode: bool = False
    test_filename: str = ""
    test_duration_sec: float = 0.0
    test_playback_sec: float = 0.0

    # Audience / speaker stats (synced from broadcaster on status updates)
    audience_count: int = 0
    speaker_subscribers: int = 0

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
        self.translation_sentence_log.clear()
        self.sentence_traces.clear()
        self.legacy_traces.clear()
        self.translation_legacy_v2_log.clear()
        self.legacy_v2_traces.clear()
        self.legacy_v2_window_events.clear()
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

    def add_sentence_translation(self, text: str) -> None:
        text = text.strip()
        if text:
            self.translation_sentence_log.append(text)

    def add_sentence_trace(self, trace: dict) -> None:
        self.sentence_traces.append(dict(trace))

    def add_legacy_trace(self, trace: dict) -> None:
        self.legacy_traces.append(dict(trace))

    def add_legacy_v2_translation(self, text: str) -> None:
        text = text.strip()
        if text:
            self.translation_legacy_v2_log.append(text)

    def add_legacy_v2_trace(self, trace: dict) -> None:
        self.legacy_v2_traces.append(dict(trace))

    def add_legacy_v2_window_event(self, event: dict) -> None:
        self.legacy_v2_window_events.append(dict(event))

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

    def sentence_release_stats(self) -> dict:
        return self._release_stats(self.sentence_traces)

    def sentence_recombine_stats(self) -> dict:
        by_id: dict[str, dict[str, int]] = {}
        for trace in self.sentence_traces:
            rid = str(trace.get("recombine_id") or "")
            if not rid:
                continue
            slot = by_id.setdefault(
                rid, {"fragments": 0, "units": 0, "translates": 0}
            )
            slot["fragments"] = max(
                slot["fragments"], int(trace.get("fragment_count") or 0)
            )
            slot["units"] += 1
            if (trace.get("translation") or "").strip():
                slot["translates"] += 1
        n = len(by_id)
        frags = sum(s["fragments"] for s in by_id.values())
        units = sum(s["units"] for s in by_id.values())
        translates = sum(s["translates"] for s in by_id.values())
        return {
            "recombine_count": n,
            "fragment_count": frags,
            "unit_count": units,
            "translate_count": translates,
            "fragments_per_recombine": round(frags / n, 2) if n else 0,
            "units_per_recombine": round(units / n, 2) if n else 0,
            "translate_per_recombine": round(translates / n, 2) if n else 0,
        }

    def legacy_release_stats(self) -> dict:
        return self._release_stats(self.legacy_traces)

    def legacy_v2_release_stats(self) -> dict:
        return self._release_stats(self.legacy_v2_traces)

    def legacy_v2_window_stats(self) -> dict:
        from hki.live.output_composer_v2 import window_stats_from_events

        return window_stats_from_events(self.legacy_v2_window_events)

    def _release_comment_line(
        self, traces: list[dict], preferred_keys: tuple[str, ...]
    ) -> str:
        stats = self._release_stats(traces)
        total = stats["total"]
        if not total:
            return ""
        preferred = set(preferred_keys)
        parts = []
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
        if bucket not in ("legacy", "sentence", "legacy_v2"):
            return
        slot = self.token_usage.setdefault(
            bucket,
            {
                "prompt": 0,
                "completion": 0,
                "calls_translate": 0,
                "calls_recombine": 0,
                "calls_understand": 0,
                "calls": 0,
            },
        )
        slot["prompt"] += max(0, int(prompt))
        slot["completion"] += max(0, int(completion))
        if bucket == "legacy":
            if kind == "recombine":
                slot["calls_recombine"] += 1
            else:
                slot["calls_translate"] += 1
        elif bucket == "legacy_v2":
            slot["calls_recombine"] += 1
        else:
            if kind == "recombine":
                slot["calls_recombine"] += 1
            elif kind == "understand":
                slot["calls_understand"] += 1
            elif kind == "translate":
                slot["calls_translate"] += 1
            slot["calls"] += 1

    def token_comment_lines(self) -> list[str]:
        legacy = self.token_usage.get("legacy") or {}
        legacy_v2 = self.token_usage.get("legacy_v2") or {}
        sentence = self.token_usage.get("sentence") or {}
        if (
            not legacy
            and not legacy_v2
            and not sentence
            and not self.legacy_traces
            and not self.legacy_v2_traces
            and not self.sentence_traces
        ):
            return []
        lines: list[str] = []
        if legacy or legacy_v2 or sentence:
            lines.extend(
                [
                    "- tokens -",
                    "(STT / Contextualizar / TTS no incluidos)",
                ]
            )
        if legacy:
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
            ("passthrough", "recombine", "anchor_repair", "repair_rejected", "fallback"),
        )
        if classic_release:
            lines.append("Clásico " + classic_release)
        if legacy_v2 or self.legacy_v2_traces:
            lines.append(
                "Clásico v2: {p} in / {c} out  (recombine {r})".format(
                    p=legacy_v2.get("prompt", 0),
                    c=legacy_v2.get("completion", 0),
                    r=legacy_v2.get("calls_recombine", 0),
                )
            )
            t_calls = int(legacy.get("calls_translate") or 0)
            r_calls = int(legacy_v2.get("calls_recombine") or 0)
            if t_calls:
                pct = 100.0 * r_calls / t_calls
                lines.append(f"Clásico v2 recombine/traducir: {pct:.0f}%")
            v2_release = self._release_comment_line(
                self.legacy_v2_traces,
                (
                    "recombine",
                    "grace_expired",
                    "forced_max_window",
                    "drain",
                    "fallback",
                ),
            )
            if v2_release:
                lines.append("Clásico v2 " + v2_release)
        if sentence:
            lines.append(
                "Por oración: {p} in / {c} out  (recombinar {r} + traducir {t})".format(
                    p=sentence.get("prompt", 0),
                    c=sentence.get("completion", 0),
                    r=sentence.get("calls_recombine", 0),
                    t=sentence.get("calls_translate", 0),
                )
            )
            failed = sum(
                1
                for t in self.sentence_traces
                if str(t.get("release_reason") or "") == "translation_failed"
            )
            if failed:
                lines.append(f"translation_failed: {failed}")
            stats = self.sentence_recombine_stats()
            if stats["recombine_count"]:
                lines.append(
                    "Por oración avg: fragments/recombine {f}  "
                    "units/recombine {u}  translate/recombine {t}".format(
                        f=stats["fragments_per_recombine"],
                        u=stats["units_per_recombine"],
                        t=stats["translate_per_recombine"],
                    )
                )
        sentence_release = self._release_comment_line(
            self.sentence_traces,
            ("vad_release", "max_pending", "max_duration", "drain"),
        )
        if sentence_release:
            lines.append(sentence_release)
        return lines

    @property
    def has_log(self) -> bool:
        return bool(
            self.transcript_log
            or self.translation_final_log
            or self.translation_legacy_log
            or self.translation_sentence_log
            or self.sentence_traces
            or self.legacy_traces
            or self.legacy_v2_traces
            or self.translation_legacy_v2_log
        )

    def to_log(self) -> dict:
        return {
            "session_label": self.session_label,
            "transcripts": list(self.transcript_log),
            "translations": list(self.translation_final_log),
            "translations_legacy": list(self.translation_legacy_log),
            "translations_legacy_v2": list(self.translation_legacy_v2_log),
            "translations_sentence": list(self.translation_sentence_log),
            "sentence_traces": list(self.sentence_traces),
            "legacy_traces": list(self.legacy_traces),
            "legacy_v2_traces": list(self.legacy_v2_traces),
            "legacy_v2_window_events": list(self.legacy_v2_window_events),
            "legacy_v2_window_stats": self.legacy_v2_window_stats(),
            "sentence_release_stats": self.sentence_release_stats(),
            "sentence_recombine_stats": self.sentence_recombine_stats(),
            "legacy_release_stats": self.legacy_release_stats(),
            "legacy_v2_release_stats": self.legacy_v2_release_stats(),
            "pipeline_legacy_enabled": config.PIPELINE_LEGACY_ENABLED,
            "pipeline_legacy_v2_enabled": config.PIPELINE_LEGACY_V2_ENABLED,
            "pipeline_sentence_enabled": config.PIPELINE_SENTENCE_ENABLED,
            "token_usage": dict(self.token_usage),
            "token_comment": "\n".join(self.token_comment_lines()),
            "has_log": self.has_log,
        }

    def set_speaker_subscribers(self, count: int) -> None:
        self.speaker_subscribers = max(0, count)

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
            "translation_pipeline": config.translation_pipeline_status(),
            "pipeline_legacy_enabled": config.PIPELINE_LEGACY_ENABLED,
            "pipeline_legacy_v2_enabled": config.PIPELINE_LEGACY_V2_ENABLED,
            "pipeline_sentence_enabled": config.PIPELINE_SENTENCE_ENABLED,
        }

    def _context_display_payload(self) -> dict | None:
        from hki.live.context import format_context_display

        if not self.translation_context:
            return None
        return format_context_display(self.translation_context)
