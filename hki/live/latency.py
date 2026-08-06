"""Per-utterance latency profiling for file-test sessions."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from hki import config


@dataclass
class _Utterance:
    item_id: str
    ko_preview: str = ""
    first_delta_mono: float | None = None
    completed_mono: float | None = None
    final_mono: float | None = None
    completed_playback_sec: float | None = None


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


def _stats(values: list[float]) -> dict:
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


class LatencyProfiler:
    """Tracks wall-clock latency between pipeline stages per utterance."""

    def __init__(self) -> None:
        self._utterances: dict[str, _Utterance] = {}

    def _get(self, item_id: str) -> _Utterance:
        if item_id not in self._utterances:
            self._utterances[item_id] = _Utterance(item_id=item_id)
        return self._utterances[item_id]

    def on_transcript_delta(self, item_id: str, text: str, now_mono: float) -> None:
        u = self._get(item_id)
        if u.first_delta_mono is None:
            u.first_delta_mono = now_mono
        u.ko_preview = text[:100]

    def on_transcript_completed(
        self, item_id: str, text: str, now_mono: float, playback_sec: float
    ) -> None:
        u = self._get(item_id)
        u.completed_mono = now_mono
        u.completed_playback_sec = round(playback_sec, 2)
        u.ko_preview = text[:100]

    def on_translation(self, item_id: str, tier: str, now_mono: float) -> None:
        if tier != "final":
            return
        u = self._get(item_id)
        u.final_mono = now_mono

    def build_report(self, session_label: str) -> dict:
        completed = [
            u for u in self._utterances.values() if u.completed_mono is not None
        ]

        final_after_utterance: list[float] = []
        rows: list[dict] = []

        for u in completed:
            row: dict = {
                "item_id": u.item_id[:12],
                "ko": u.ko_preview,
                "playback_sec": u.completed_playback_sec,
                "final_ms": None,
            }

            if u.final_mono is not None and u.completed_mono is not None:
                ms = (u.final_mono - u.completed_mono) * 1000
                final_after_utterance.append(ms)
                row["final_ms"] = round(ms)

            rows.append(row)

        summary = {
            "final_after_utterance_ms": _stats(final_after_utterance),
        }

        return {
            "session_label": session_label,
            "utterance_count": len(completed),
            "config": {
                "vad_silence_ms": config.VAD_SILENCE_DURATION_MS,
                "final_model": config.FINAL_MODEL,
                "audio_chunk_ms": config.AUDIO_CHUNK_MS,
            },
            "summary": summary,
            "utterances": rows,
            "bottlenecks": _analyze_bottlenecks(summary),
        }


def _analyze_bottlenecks(summary: dict) -> list[dict]:
    hints: list[dict] = []

    vad_ms = config.VAD_SILENCE_DURATION_MS
    hints.append(
        {
            "stage": "VAD (silencio)",
            "impact": "fixed",
            "detail": (
                f"Whisper espera {vad_ms}ms de silencio para cerrar cada frase. "
                "Es latencia mínima antes de la traducción final."
            ),
            "mitigation": (
                f"Reducir HKI_VAD_SILENCE_DURATION_MS (actual {vad_ms}ms, ej. 400). "
                "Riesgo: cortar frases largas."
            ),
        }
    )

    final_stats = summary.get("final_after_utterance_ms", {})
    final_avg = final_stats.get("avg")
    if final_avg is not None and final_avg > 1200:
        hints.append(
            {
                "stage": "Traducción final (GPT)",
                "impact": "high",
                "detail": (
                    f"Promedio {final_avg}ms desde fin de frase en coreano "
                    f"hasta subtítulo español confirmado (p95: {final_stats.get('p95')}ms)."
                ),
                "mitigation": (
                    "Acortar manuscrito en contexto; probar modelo más rápido en HKI_FINAL_MODEL."
                ),
            }
        )
    elif final_avg is not None:
        hints.append(
            {
                "stage": "Traducción final (GPT)",
                "impact": "low",
                "detail": f"Promedio {final_avg}ms — dentro de rango razonable.",
                "mitigation": "Sin cambio urgente.",
            }
        )

    return hints
