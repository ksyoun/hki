"""Live speech pattern collection and TTS buffer delay projection."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hki import config

logger = logging.getLogger(__name__)

SESSIONS_DIR = config.ANALYTICS_DIR / "sessions"
SUMMARY_PATH = config.ANALYTICS_DIR / "summary.json"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _stats_ms(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": round(statistics.mean(values)),
        "p50": round(_percentile(values, 50) or 0),
        "p95": round(_percentile(values, 95) or 0),
        "min": round(min(values)),
        "max": round(max(values)),
    }


@dataclass
class _Utterance:
    item_id: str
    ko: str = ""
    char_count: int = 0
    es: str = ""
    es_char_count: int = 0
    first_delta_mono: float | None = None
    completed_mono: float | None = None
    final_mono: float | None = None
    tts_audio_mono: float | None = None
    elapsed_sec: float | None = None
    speech_duration_ms: int | None = None
    gap_since_prev_ms: int | None = None
    translation_ms: int | None = None
    translator_queue_depth: int = 0
    tts_queue_depth: int = 0
    tts_playback_ms: int | None = None
    observed_tts_lag_ms: int | None = None


class SpeechAnalyticsCollector:
    """Collects per-utterance timing during live broadcasts."""

    def __init__(self, stream_start_mono: float):
        self._stream_start_mono = stream_start_mono
        self._utterances: dict[str, _Utterance] = {}
        self._order: list[str] = []
        self._prev_completed_mono: float | None = None
        self._max_translator_queue = 0
        self._max_tts_queue = 0

    def _get(self, item_id: str) -> _Utterance:
        if item_id not in self._utterances:
            self._utterances[item_id] = _Utterance(item_id=item_id)
            self._order.append(item_id)
        return self._utterances[item_id]

    def on_transcript_delta(self, item_id: str, text: str, now_mono: float) -> None:
        u = self._get(item_id)
        if u.first_delta_mono is None:
            u.first_delta_mono = now_mono
        u.ko = text[:200]

    def on_transcript_completed(
        self,
        item_id: str,
        text: str,
        now_mono: float,
        translator_queue_depth: int = 0,
    ) -> None:
        u = self._get(item_id)
        u.completed_mono = now_mono
        u.elapsed_sec = round(now_mono - self._stream_start_mono, 2)
        u.ko = text
        u.char_count = len(text.strip())
        u.translator_queue_depth = translator_queue_depth
        self._max_translator_queue = max(self._max_translator_queue, translator_queue_depth)

        if u.first_delta_mono is not None:
            u.speech_duration_ms = round((now_mono - u.first_delta_mono) * 1000)

        if self._prev_completed_mono is not None:
            u.gap_since_prev_ms = round((now_mono - self._prev_completed_mono) * 1000)
        self._prev_completed_mono = now_mono

    def on_translation(
        self,
        item_id: str,
        es: str,
        now_mono: float,
        tts_queue_depth: int = 0,
    ) -> None:
        u = self._get(item_id)
        u.final_mono = now_mono
        u.es = es[:200]
        u.es_char_count = len(es.strip())
        u.tts_queue_depth = tts_queue_depth
        self._max_tts_queue = max(self._max_tts_queue, tts_queue_depth)
        if u.completed_mono is not None:
            u.translation_ms = round((now_mono - u.completed_mono) * 1000)

    def on_tts_audio(self, item_id: str, pcm: bytes, now_mono: float) -> None:
        u = self._get(item_id)
        u.tts_audio_mono = now_mono
        samples = len(pcm) // 2
        u.tts_playback_ms = round(samples / config.TTS_SAMPLE_RATE * 1000)
        if u.completed_mono is not None:
            u.observed_tts_lag_ms = round((now_mono - u.completed_mono) * 1000)

    def _utterance_rows(self) -> list[_Utterance]:
        return [
            self._utterances[iid]
            for iid in self._order
            if self._utterances[iid].completed_mono is not None
        ]

    def build_report(self, session_label: str) -> dict:
        rows = self._utterance_rows()
        gaps = [u.gap_since_prev_ms for u in rows if u.gap_since_prev_ms is not None]
        speech = [u.speech_duration_ms for u in rows if u.speech_duration_ms is not None]
        translations = [u.translation_ms for u in rows if u.translation_ms is not None]
        observed_lags = [u.observed_tts_lag_ms for u in rows if u.observed_tts_lag_ms is not None]

        backlog = 0
        backlog_total = 0
        for u in rows:
            if u.gap_since_prev_ms is not None and u.translation_ms is not None:
                backlog_total += 1
                if u.gap_since_prev_ms < u.translation_ms:
                    backlog += 1

        duration_sec = rows[-1].elapsed_sec or 0.0 if rows else 0.0
        utterance_count = len(rows)
        upm = round(utterance_count / (duration_sec / 60), 1) if duration_sec > 60 else None

        buffer_projection = compute_buffer_projection(rows)
        hints = _build_hints(buffer_projection, backlog, backlog_total, utterance_count)

        return {
            "session_label": session_label,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "utterance_count": utterance_count,
            "duration_sec": round(duration_sec, 1),
            "utterances_per_minute": upm,
            "config": {
                "vad_silence_ms": config.VAD_SILENCE_DURATION_MS,
                "final_model": config.FINAL_MODEL,
                "buffer_depths": config.BUFFER_PROJECTION_DEPTHS,
                "polish_ms_per_utterance": config.POLISH_MS_PER_UTTERANCE,
                "ms_per_es_char_tts_est": config.MS_PER_ES_CHAR_TTS_EST,
            },
            "summary": {
                "speech_duration_ms": _stats_ms([float(v) for v in speech]),
                "gap_since_prev_ms": _stats_ms([float(v) for v in gaps]),
                "translation_ms": _stats_ms([float(v) for v in translations]),
                "observed_tts_lag_ms": _stats_ms([float(v) for v in observed_lags]),
                "backlog_pressure_pct": round(100 * backlog / backlog_total)
                if backlog_total
                else None,
                "max_translator_queue_depth": self._max_translator_queue,
                "max_tts_queue_depth": self._max_tts_queue,
            },
            "buffer_projection": buffer_projection,
            "hints": hints,
            "utterances": [_utterance_to_dict(u) for u in rows],
        }


def _utterance_to_dict(u: _Utterance) -> dict:
    return {
        "item_id": u.item_id[:12],
        "elapsed_sec": u.elapsed_sec,
        "ko": u.ko[:80],
        "char_count": u.char_count,
        "es_char_count": u.es_char_count,
        "speech_duration_ms": u.speech_duration_ms,
        "gap_since_prev_ms": u.gap_since_prev_ms,
        "translation_ms": u.translation_ms,
        "tts_playback_ms": u.tts_playback_ms,
        "observed_tts_lag_ms": u.observed_tts_lag_ms,
        "translator_queue_depth": u.translator_queue_depth,
        "tts_queue_depth": u.tts_queue_depth,
    }


def _tts_playback_estimate_ms(u: _Utterance) -> float:
    if u.tts_playback_ms is not None:
        return float(u.tts_playback_ms)
    if u.es_char_count > 0:
        return u.es_char_count * config.MS_PER_ES_CHAR_TTS_EST
    return 0.0


def compute_buffer_projection(rows: list[_Utterance]) -> dict:
    """Estimate TTS lag if we buffer N utterances before playback."""
    result: dict[str, dict] = {}

    for n in config.BUFFER_PROJECTION_DEPTHS:
        if n < 1 or len(rows) < n:
            result[str(n)] = {
                "content_span_ms": _stats_ms([]),
                "translation_sum_ms": _stats_ms([]),
                "tts_playback_sum_ms": _stats_ms([]),
                "polish_ms": n * config.POLISH_MS_PER_UTTERANCE,
                "total_est_ms": _stats_ms([]),
                "window_count": 0,
            }
            continue

        content_spans: list[float] = []
        translation_sums: list[float] = []
        tts_sums: list[float] = []
        totals: list[float] = []
        polish = n * config.POLISH_MS_PER_UTTERANCE

        for i in range(len(rows) - n + 1):
            window = rows[i : i + n]
            if window[0].completed_mono is None or window[-1].completed_mono is None:
                continue
            span = (window[-1].completed_mono - window[0].completed_mono) * 1000
            trans = sum(u.translation_ms or 0 for u in window)
            tts = sum(_tts_playback_estimate_ms(u) for u in window)
            total = span + trans + polish + tts

            content_spans.append(span)
            translation_sums.append(trans)
            tts_sums.append(tts)
            totals.append(total)

        result[str(n)] = {
            "content_span_ms": _stats_ms(content_spans),
            "translation_sum_ms": _stats_ms(translation_sums),
            "tts_playback_sum_ms": _stats_ms(tts_sums),
            "polish_ms": polish,
            "total_est_ms": _stats_ms(totals),
            "window_count": len(totals),
        }

    return result


def _build_hints(
    buffer_projection: dict,
    backlog: int,
    backlog_total: int,
    utterance_count: int,
) -> list[dict]:
    hints: list[dict] = []

    if utterance_count < 3:
        hints.append(
            {
                "level": "info",
                "text": "Pocas frases registradas — repita una transmisión más larga para proyecciones fiables.",
            }
        )
        return hints

    for depth in ("3", "5"):
        proj = buffer_projection.get(depth, {})
        total = proj.get("total_est_ms", {})
        p50 = total.get("p50")
        if p50 is not None:
            hints.append(
                {
                    "level": "projection",
                    "text": (
                        f"Buffer de {depth} frases: retraso estimado ~{p50 / 1000:.0f}s (p50), "
                        f"~{total.get('p95', 0) / 1000:.0f}s (p95) hasta que el altavoz empiece a leer el bloque más antiguo."
                    ),
                }
            )

    if backlog_total and backlog / backlog_total > 0.3:
        hints.append(
            {
                "level": "warning",
                "text": (
                    f"Presión de cola de traducción: {round(100 * backlog / backlog_total)}% "
                    "de frases llegan más rápido de lo que se traducen."
                ),
            }
        )

    return hints


def save_session_report(report: dict) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = (report.get("session_label") or "live").replace("/", "-")[:40]
    path = SESSIONS_DIR / f"{ts}_{label}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Speech analytics saved: %s", path)
    return path


def _merge_projection_stats(existing: dict | None, new: dict) -> dict:
    merged = dict(existing or {})
    for depth, proj in new.items():
        if depth not in merged:
            merged[depth] = {"total_est_p50_sum": 0, "total_est_p50_count": 0, "sessions": 0}
        entry = merged[depth]
        p50 = proj.get("total_est_ms", {}).get("p50")
        if p50 is not None:
            entry["total_est_p50_sum"] = entry.get("total_est_p50_sum", 0) + p50
            entry["total_est_p50_count"] = entry.get("total_est_p50_count", 0) + 1
        entry["sessions"] = entry.get("sessions", 0) + 1
        if entry.get("total_est_p50_count"):
            entry["avg_total_est_p50_ms"] = round(
                entry["total_est_p50_sum"] / entry["total_est_p50_count"]
            )
    return merged


def update_cumulative_summary(report: dict, session_path: Path) -> None:
    config.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {}
    if SUMMARY_PATH.exists():
        try:
            summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        except Exception:
            summary = {}

    summary["session_count"] = summary.get("session_count", 0) + 1
    summary["utterance_count"] = summary.get("utterance_count", 0) + report.get(
        "utterance_count", 0
    )
    summary["last_session_at"] = report.get("recorded_at")
    summary["buffer_projection_cumulative"] = _merge_projection_stats(
        summary.get("buffer_projection_cumulative"),
        report.get("buffer_projection", {}),
    )

    recent = summary.get("recent_sessions", [])
    recent.insert(
        0,
        {
            "file": session_path.name,
            "recorded_at": report.get("recorded_at"),
            "utterance_count": report.get("utterance_count"),
            "buffer_5_p50_ms": report.get("buffer_projection", {})
            .get("5", {})
            .get("total_est_ms", {})
            .get("p50"),
        },
    )
    summary["recent_sessions"] = recent[:10]
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def list_sessions() -> list[dict]:
    if not SESSIONS_DIR.exists():
        return []
    items = []
    for path in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(
                {
                    "file": path.name,
                    "recorded_at": data.get("recorded_at"),
                    "session_label": data.get("session_label"),
                    "utterance_count": data.get("utterance_count"),
                    "buffer_5_p50_ms": data.get("buffer_projection", {})
                    .get("5", {})
                    .get("total_est_ms", {})
                    .get("p50"),
                }
            )
        except Exception:
            items.append({"file": path.name, "error": True})
    return items


def load_summary() -> dict:
    if not SUMMARY_PATH.exists():
        return {"session_count": 0, "utterance_count": 0, "recent_sessions": []}
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
