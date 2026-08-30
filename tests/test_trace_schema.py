"""Canonical release-trace schema shared by classic and oración."""

from hki.live.trace_schema import (
    AUDIO_START_SOURCES,
    TRACE_KEYS,
    SttTimingTracker,
    build_release_trace,
    parse_release_trace,
    unique_recombine_traces,
)


def test_parse_fills_canonical_keys_and_source():
    classic = parse_release_trace(
        {"pipeline": "classic", "action": "release", "translation": "Hola"}
    )
    oracion = parse_release_trace(
        {"pipeline": "oracion", "action": "release", "translation": "Hola"}
    )
    assert set(classic) == set(TRACE_KEYS)
    assert set(classic) == set(oracion)
    assert classic["t_audio_start_source"] in AUDIO_START_SOURCES
    assert "latency_recombine" not in classic
    assert "through_index" not in classic
    assert "release_latency_ms" not in classic


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
