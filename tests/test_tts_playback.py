"""TTS playback clock, queue-depth speed, and content-lag gap."""

from hki.live.session import LiveSession
from hki.live.tts_playback import (
    TtsPlaybackClock,
    audio_duration_after_speed_ms,
    pcm_duration_ms,
    playback_rate_for_depth,
    skipped_tts_fields,
    speed_trigger_reason,
)


def test_pcm_duration_from_sample_count():
    # 24000 Hz * 2 bytes * 0.5s = 24000 bytes
    pcm = b"\x00\x00" * 12000
    assert pcm_duration_ms(pcm, sample_rate=24000) == 500


def test_playback_rate_and_reason_tiers():
    assert playback_rate_for_depth(0) == 1.0
    assert speed_trigger_reason(0) == "queue<=3"
    assert playback_rate_for_depth(3) == 1.0
    assert speed_trigger_reason(3) == "queue<=3"
    assert playback_rate_for_depth(4) == 1.1
    assert speed_trigger_reason(4) == "queue<=6"
    assert playback_rate_for_depth(6) == 1.1
    assert speed_trigger_reason(6) == "queue<=6"
    assert playback_rate_for_depth(7) == 1.15
    assert speed_trigger_reason(7) == "queue>6"


def test_duration_after_speed():
    assert audio_duration_after_speed_ms(1000, 1.0) == 1000
    assert audio_duration_after_speed_ms(1150, 1.15) == 1000


def test_gap_is_content_lag_not_processing_delay():
    clock = TtsPlaybackClock()
    clock.add_source_speech_ms(2000)
    clock.add_source_speech_ms(2000)
    assert clock.gap_ms(now_mono=10.0) == 4000

    # 24000 Hz, 1.0s of PCM
    pcm = b"\x00\x00" * 24000
    metrics = clock.enqueue(
        pcm,
        synth_pending=0,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert metrics["gap_ms_at_enqueue"] == 4000
    assert metrics["tts_queue_len_at_enqueue"] == 0
    assert metrics["tts_speed_applied"] == 1.0
    assert metrics["speed_trigger_reason"] == "queue<=3"
    assert metrics["tts_audio_duration_ms"] == 1000
    assert metrics["tts_play_start_ms"] == 1_000_000
    assert metrics["tts_play_end_ms"] == 1_001_000

    # After 1s of play, TTS elapsed = 1000; source still 4000
    assert clock.tts_elapsed_ms(11.0) == 1000
    assert clock.gap_ms(11.0) == 3000


def test_waiting_count_excludes_currently_playing():
    clock = TtsPlaybackClock()
    pcm = b"\x00\x00" * 24000  # 1s
    clock.enqueue(
        pcm,
        synth_pending=0,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert clock.waiting_count(10.0) == 0
    clock.enqueue(
        pcm,
        synth_pending=0,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert clock.waiting_count(10.0) == 1
    metrics = clock.enqueue(
        pcm,
        synth_pending=2,
        composer_pending=1,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    # one playing (excluded) + 1 waiting + 2 synth + 1 composer = 4
    assert metrics["tts_queue_len_at_enqueue"] == 4
    assert metrics["tts_speed_applied"] == 1.1
    assert metrics["speed_trigger_reason"] == "queue<=6"


def test_skipped_fields():
    fields = skipped_tts_fields()
    assert fields["speed_trigger_reason"] == "tts_skipped"
    assert fields["tts_speed_applied"] == 0.0
    assert skipped_tts_fields("tts_error")["speed_trigger_reason"] == "tts_error"


def test_patch_legacy_trace_updates_same_release_row():
    session = LiveSession()
    idx = session.add_legacy_trace({"action": "release", "translation": "Hola"})
    session.patch_legacy_trace(
        idx,
        {
            "tts_speed_applied": 1.15,
            "speed_trigger_reason": "queue>6",
            "tts_queue_len_at_enqueue": 8,
            "gap_ms_at_enqueue": 3200,
        },
    )
    row = session.legacy_traces[idx]
    assert row["tts_speed_applied"] == 1.15
    assert row["speed_trigger_reason"] == "queue>6"
    assert row["tts_queue_len_at_enqueue"] == 8
    assert row["gap_ms_at_enqueue"] == 3200
