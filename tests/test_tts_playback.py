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
    assert playback_rate_for_depth(0) == 1.15
    assert speed_trigger_reason(0) == "queue<=3"
    assert playback_rate_for_depth(3) == 1.15
    assert speed_trigger_reason(3) == "queue<=3"
    assert playback_rate_for_depth(4) == 1.2
    assert speed_trigger_reason(4) == "queue<=6"
    assert playback_rate_for_depth(6) == 1.2
    assert speed_trigger_reason(6) == "queue<=6"
    assert playback_rate_for_depth(7) == 1.25
    assert speed_trigger_reason(7) == "queue>6"


def test_duration_after_speed():
    assert audio_duration_after_speed_ms(1000, 1.0) == 1000
    assert audio_duration_after_speed_ms(1150, 1.15) == 1000
    assert audio_duration_after_speed_ms(1200, 1.2) == 1000
    assert audio_duration_after_speed_ms(1250, 1.25) == 1000


def test_speed_hysteresis_holds_max_at_mid_boundary():
    assert playback_rate_for_depth(6, previous=1.25) == 1.25
    assert speed_trigger_reason(6, applied=1.25) == "hysteresis"
    assert playback_rate_for_depth(5, previous=1.25) == 1.2
    assert speed_trigger_reason(5, applied=1.2) == "queue<=6"


def test_speed_hysteresis_holds_mid_at_base_boundary():
    assert playback_rate_for_depth(3, previous=1.2) == 1.2
    assert speed_trigger_reason(3, applied=1.2) == "hysteresis"
    assert playback_rate_for_depth(2, previous=1.2) == 1.15
    assert speed_trigger_reason(2, applied=1.15) == "queue<=3"


def test_speed_hysteresis_steps_down_one_tier():
    assert playback_rate_for_depth(2, previous=1.25) == 1.2
    assert playback_rate_for_depth(2, previous=1.2) == 1.15


def test_speed_hysteresis_raises_immediately():
    assert playback_rate_for_depth(7, previous=1.15) == 1.25
    assert playback_rate_for_depth(4, previous=1.15) == 1.2


def test_speed_hysteresis_smoothes_boundary_chatter():
    applied = []
    previous = None
    for depth in (7, 6, 3, 5):
        previous = playback_rate_for_depth(depth, previous=previous)
        applied.append(previous)
    assert applied == [1.25, 1.25, 1.2, 1.2]


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
    assert metrics["tts_speed_applied"] == 1.15
    assert metrics["speed_trigger_reason"] == "queue<=3"
    assert metrics["tts_audio_duration_ms"] == 870
    assert metrics["tts_pcm_1x_ms"] == 1000
    assert metrics["tts_clock_wait_ms"] == 0
    assert metrics["tts_play_start_ms"] == 1_000_000
    assert metrics["tts_play_end_ms"] == 1_000_870

    # Clip is 870ms at 1.15x; after 1s wall it has finished
    assert clock.tts_elapsed_ms(11.0) == 870
    assert clock.gap_ms(11.0) == 3130


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
    second = clock.enqueue(
        pcm,
        synth_pending=0,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert clock.waiting_count(10.0) == 1
    assert second["tts_clock_wait_ms"] == 870
    metrics = clock.enqueue(
        pcm,
        synth_pending=2,
        composer_pending=1,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    # one playing (excluded) + 1 waiting + 2 synth + 1 composer = 4
    assert metrics["tts_queue_len_at_enqueue"] == 4
    assert metrics["tts_speed_applied"] == 1.2
    assert metrics["speed_trigger_reason"] == "queue<=6"


def test_enqueue_holds_max_when_queue_dips_one():
    clock = TtsPlaybackClock()
    pcm = b"\x00\x00" * 24000
    first = clock.enqueue(
        pcm,
        synth_pending=7,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert first["tts_queue_len_at_enqueue"] == 7
    assert first["tts_speed_applied"] == 1.25
    second = clock.enqueue(
        pcm,
        synth_pending=6,
        composer_pending=0,
        now_mono=10.0,
        now_unix_ms=1_000_000,
    )
    assert second["tts_queue_len_at_enqueue"] == 6
    assert second["tts_speed_applied"] == 1.25
    assert second["speed_trigger_reason"] == "hysteresis"


def test_skipped_fields():
    fields = skipped_tts_fields()
    assert fields["speed_trigger_reason"] == "tts_skipped"
    assert fields["tts_speed_applied"] == 0.0
    assert fields["tts_synth_ms"] == 0
    assert fields["tts_input_chars"] == 0
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
