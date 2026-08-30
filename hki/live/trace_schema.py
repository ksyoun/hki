"""Canonical per-caption release trace for classic and oración.

Weekly A/B comparison should import parse_release_trace / TRACE_KEYS only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

AUDIO_START_SOURCES = ("speech_started", "first_delta", "fallback")
HOLD_REASONS = (
    "",
    "fragment_looks_open",
    "batch_wait",
    "incomplete_timeout_expired",
)
RELEASE_REASONS = (
    "closed_immediate",
    "partner_arrived",
    "incomplete_cap_expired",
    "max_pending",
    "max_duration",
    "drain",
    "recombine_fallback",
    "translation_failed",
)
PIPELINES = ("classic", "oracion")

TRACE_KEYS: tuple[str, ...] = (
    "timestamp",
    "pipeline",
    "action",
    "fragment_ids",
    "original_stt",
    "ko_corrected",
    "translation",
    "joined_preview",
    "fragment_count",
    "unit_index",
    "unit_count",
    "fragment_indexes",
    "recombine_id",
    "t_audio_start",
    "t_audio_start_source",
    "t_stt_final",
    "t_release",
    "latency_stt_to_release",
    "latency_speech_to_release",
    "used_llm_translate",
    "translate_llm_ms",
    "used_llm_recombine",
    "recombine_llm_ms",
    "hold_ms",
    "hold_reason",
    "pacer_wait_ms",
    "fragment_open_final",
    "release_reason",
    "tokens_translate_in",
    "tokens_translate_out",
    "tokens_recombine_in",
    "tokens_recombine_out",
    "stt_repair",
    "repair_rejected",
    "anchor_repair",
    "had_incierto",
    "mapping_fallback",
    "recombine_flags",
)


def unix_ms() -> int:
    return int(time.time() * 1000)


def ms_since(start_mono: float, end_mono: float | None = None) -> int:
    end = time.monotonic() if end_mono is None else end_mono
    if not start_mono:
        return 0
    return max(0, int((end - start_mono) * 1000))


@dataclass
class ItemTiming:
    t_audio_start: int = 0
    t_audio_start_source: str = "fallback"
    t_stt_final: int = 0
    t_audio_start_mono: float = 0.0
    t_stt_final_mono: float = 0.0
    first_delta_unix_ms: int = 0
    first_delta_mono: float = 0.0
    speech_started_unix_ms: int = 0
    speech_started_mono: float = 0.0

    def resolve_audio_start(self) -> None:
        if self.speech_started_unix_ms:
            self.t_audio_start = self.speech_started_unix_ms
            self.t_audio_start_mono = self.speech_started_mono
            self.t_audio_start_source = "speech_started"
        elif self.first_delta_unix_ms:
            self.t_audio_start = self.first_delta_unix_ms
            self.t_audio_start_mono = self.first_delta_mono
            self.t_audio_start_source = "first_delta"
        else:
            self.t_audio_start = self.t_stt_final
            self.t_audio_start_mono = self.t_stt_final_mono
            self.t_audio_start_source = "fallback"

    @classmethod
    def fallback_now(cls) -> ItemTiming:
        now_u = unix_ms()
        now_m = time.monotonic()
        t = cls(
            t_stt_final=now_u,
            t_stt_final_mono=now_m,
        )
        t.resolve_audio_start()
        return t


class SttTimingTracker:
    """Per Realtime STT session: speech_started / first delta / completed."""

    def __init__(self) -> None:
        self._pending_speech_unix = 0
        self._pending_speech_mono = 0.0
        self._items: dict[str, ItemTiming] = {}

    def on_speech_started(self) -> None:
        now_u = unix_ms()
        now_m = time.monotonic()
        self._pending_speech_unix = now_u
        self._pending_speech_mono = now_m
        for t in self._items.values():
            if t.t_stt_final or t.speech_started_unix_ms:
                continue
            t.speech_started_unix_ms = now_u
            t.speech_started_mono = now_m
            self._pending_speech_unix = 0
            self._pending_speech_mono = 0.0
            break

    def on_delta(self, item_id: str) -> None:
        if not item_id:
            return
        t = self._items.setdefault(item_id, ItemTiming())
        if t.first_delta_unix_ms:
            return
        t.first_delta_unix_ms = unix_ms()
        t.first_delta_mono = time.monotonic()
        if self._pending_speech_unix:
            t.speech_started_unix_ms = self._pending_speech_unix
            t.speech_started_mono = self._pending_speech_mono
            self._pending_speech_unix = 0
            self._pending_speech_mono = 0.0

    def on_completed(self, item_id: str) -> ItemTiming:
        t = self._items.pop(item_id, None) or ItemTiming()
        t.t_stt_final = unix_ms()
        t.t_stt_final_mono = time.monotonic()
        if not t.first_delta_unix_ms and self._pending_speech_unix:
            t.speech_started_unix_ms = self._pending_speech_unix
            t.speech_started_mono = self._pending_speech_mono
            self._pending_speech_unix = 0
            self._pending_speech_mono = 0.0
        t.resolve_audio_start()
        return t

    def pop(self, item_id: str) -> ItemTiming:
        return self._items.pop(item_id, None) or ItemTiming.fallback_now()

    def clear(self) -> None:
        self._items.clear()
        self._pending_speech_unix = 0
        self._pending_speech_mono = 0.0


def merge_item_timings(timings: list[ItemTiming]) -> ItemTiming:
    if not timings:
        return ItemTiming.fallback_now()
    first, last = timings[0], timings[-1]
    return ItemTiming(
        t_audio_start=first.t_audio_start,
        t_audio_start_source=first.t_audio_start_source,
        t_stt_final=last.t_stt_final,
        t_audio_start_mono=first.t_audio_start_mono,
        t_stt_final_mono=last.t_stt_final_mono,
    )


def _defaults() -> dict:
    return {
        "timestamp": "",
        "pipeline": "classic",
        "action": "release",
        "fragment_ids": [],
        "original_stt": "",
        "ko_corrected": "",
        "translation": "",
        "joined_preview": "",
        "fragment_count": 0,
        "unit_index": 0,
        "unit_count": 1,
        "fragment_indexes": [],
        "recombine_id": "",
        "t_audio_start": 0,
        "t_audio_start_source": "fallback",
        "t_stt_final": 0,
        "t_release": 0,
        "latency_stt_to_release": 0,
        "latency_speech_to_release": 0,
        "used_llm_translate": False,
        "translate_llm_ms": 0,
        "used_llm_recombine": False,
        "recombine_llm_ms": 0,
        "hold_ms": 0,
        "hold_reason": "",
        "pacer_wait_ms": 0,
        "fragment_open_final": False,
        "release_reason": "closed_immediate",
        "tokens_translate_in": 0,
        "tokens_translate_out": 0,
        "tokens_recombine_in": 0,
        "tokens_recombine_out": 0,
        "stt_repair": False,
        "repair_rejected": False,
        "anchor_repair": False,
        "had_incierto": False,
        "mapping_fallback": False,
        "recombine_flags": [],
    }


def parse_release_trace(data: dict | None) -> dict:
    """Fill canonical keys. Unknown keys ignored. Old weekly JSON is not mapped."""
    out = _defaults()
    if not isinstance(data, dict):
        return out
    for key in TRACE_KEYS:
        if key in data and data[key] is not None:
            out[key] = data[key]
    if out["t_audio_start_source"] not in AUDIO_START_SOURCES:
        out["t_audio_start_source"] = "fallback"
    return out


def build_release_trace(**kwargs) -> dict:
    return parse_release_trace(kwargs)


def stamp_release_latencies(item) -> None:
    """Set t_release and derived latencies from monotonic fields on ReleaseItem."""
    now_mono = time.monotonic()
    item.t_release = unix_ms()
    if item.enqueued_mono:
        item.pacer_wait_ms = ms_since(item.enqueued_mono, now_mono)
    if item.t_stt_final_mono:
        item.latency_stt_to_release = ms_since(item.t_stt_final_mono, now_mono)
    elif item.last_fragment_at_mono:
        item.latency_stt_to_release = ms_since(item.last_fragment_at_mono, now_mono)
    if item.t_audio_start_mono:
        item.latency_speech_to_release = ms_since(item.t_audio_start_mono, now_mono)
    elif item.first_fragment_at_mono:
        item.latency_speech_to_release = ms_since(item.first_fragment_at_mono, now_mono)


def trace_from_release_item(item, *, pipeline: str, timestamp: str = "") -> dict:
    return build_release_trace(
        timestamp=timestamp,
        pipeline=pipeline,
        action="release" if (item.es or "").strip() else "hold",
        fragment_ids=list(item.item_ids),
        original_stt=item.original_stt or item.ko_summary,
        ko_corrected=item.ko_corrected or item.ko_summary,
        translation=item.es,
        joined_preview=item.joined_preview or "",
        fragment_count=item.fragment_count or len(item.item_ids),
        unit_index=item.unit_index,
        unit_count=item.unit_count or 1,
        fragment_indexes=list(item.fragment_indexes),
        recombine_id=item.recombine_id,
        t_audio_start=item.t_audio_start,
        t_audio_start_source=item.t_audio_start_source or "fallback",
        t_stt_final=item.t_stt_final,
        t_release=item.t_release,
        latency_stt_to_release=item.latency_stt_to_release,
        latency_speech_to_release=item.latency_speech_to_release,
        used_llm_translate=bool(item.used_llm_translate),
        translate_llm_ms=int(item.translate_llm_ms or 0),
        used_llm_recombine=bool(item.used_llm_recombine),
        recombine_llm_ms=int(item.recombine_llm_ms or 0),
        hold_ms=int(item.hold_ms or 0),
        hold_reason=item.hold_reason or "",
        pacer_wait_ms=int(item.pacer_wait_ms or 0),
        fragment_open_final=bool(item.fragment_open_final),
        release_reason=item.release_reason or "closed_immediate",
        tokens_translate_in=int(item.tokens_translate_in or 0),
        tokens_translate_out=int(item.tokens_translate_out or 0),
        tokens_recombine_in=int(item.tokens_recombine_in or 0),
        tokens_recombine_out=int(item.tokens_recombine_out or 0),
        stt_repair=bool(item.stt_repair),
        repair_rejected=bool(item.repair_rejected),
        anchor_repair=bool(item.anchor_repair),
        had_incierto=bool(item.had_incierto),
        mapping_fallback=bool(item.mapping_fallback),
        recombine_flags=list(item.recombine_flags),
    )


def unique_recombine_traces(traces: list[dict]) -> list[dict]:
    """One row per recombine_id (unit_index==0). Traces without id are kept."""
    seen: set[str] = set()
    out: list[dict] = []
    for t in traces:
        rid = str(t.get("recombine_id") or "")
        if not rid:
            out.append(t)
            continue
        if int(t.get("unit_index") or 0) != 0:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(t)
    return out


def audio_start_source_counts(traces: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in AUDIO_START_SOURCES}
    for t in traces:
        if t.get("action") != "release" or not (t.get("translation") or "").strip():
            continue
        src = t.get("t_audio_start_source") or "fallback"
        if src not in counts:
            src = "fallback"
        counts[src] += 1
    return counts
