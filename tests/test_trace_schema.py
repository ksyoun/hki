"""Canonical release-trace schema for classic."""

from hki.live.trace_schema import (
    AUDIO_START_SOURCES,
    TRACE_KEYS,
    SttTimingTracker,
    build_release_trace,
    parse_release_trace,
    unique_recombine_traces,
)

TTS_KEYS = (
    "tts_play_start_ms",
    "tts_play_end_ms",
    "tts_speed_applied",
    "tts_audio_duration_ms",
    "tts_queue_len_at_enqueue",
    "gap_ms_at_enqueue",
    "speed_trigger_reason",
)


def test_parse_fills_canonical_keys_and_source():
    classic = parse_release_trace(
        {"pipeline": "classic", "action": "release", "translation": "Hola"}
    )
    assert set(classic) == set(TRACE_KEYS)
    assert classic["t_audio_start_source"] in AUDIO_START_SOURCES
    assert "latency_recombine" not in classic
    assert "through_index" not in classic
    assert "release_latency_ms" not in classic
    for key in TTS_KEYS:
        assert key in classic
    assert classic["tts_play_start_ms"] == 0
    assert classic["tts_speed_applied"] == 0.0
    assert classic["speed_trigger_reason"] == ""


def test_parse_keeps_tts_measurement_fields():
    trace = parse_release_trace(
        {
            "pipeline": "classic",
            "tts_play_start_ms": 100,
            "tts_play_end_ms": 900,
            "tts_speed_applied": 1.1,
            "tts_audio_duration_ms": 800,
            "tts_queue_len_at_enqueue": 4,
            "gap_ms_at_enqueue": 2500,
            "speed_trigger_reason": "queue<=6",
        }
    )
    assert trace["tts_speed_applied"] == 1.1
    assert trace["tts_queue_len_at_enqueue"] == 4
    assert trace["gap_ms_at_enqueue"] == 2500
    assert trace["speed_trigger_reason"] == "queue<=6"


def test_build_release_trace_drops_old_keys():
    trace = build_release_trace(
        pipeline="classic",
        through_index=3,
        latency_recombine=9,
        t_audio_start_source="first_delta",
    )
    assert "through_index" not in trace
    assert "latency_recombine" not in trace
    assert trace["t_audio_start_source"] == "first_delta"


def test_stt_timing_source_fallback_chain():
    tracker = SttTimingTracker()
    fallback = tracker.on_completed("a")
    assert fallback.t_audio_start_source == "fallback"
    assert fallback.t_audio_start == fallback.t_stt_final

    tracker.on_delta("b")
    delta = tracker.on_completed("b")
    assert delta.t_audio_start_source == "first_delta"

    tracker.on_speech_started()
    tracker.on_delta("c")
    speech = tracker.on_completed("c")
    assert speech.t_audio_start_source == "speech_started"


def test_unique_recombine_traces_keeps_unit_zero():
    rows = unique_recombine_traces(
        [
            {"recombine_id": "r1", "unit_index": 0, "hold_ms": 40},
            {"recombine_id": "r1", "unit_index": 1, "hold_ms": 40},
            {"recombine_id": "r2", "unit_index": 0, "hold_ms": 10},
        ]
    )
    assert [r["recombine_id"] for r in rows] == ["r1", "r2"]
