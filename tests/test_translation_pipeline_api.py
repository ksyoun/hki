"""API / session log for dual translation pipelines."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import LiveSession, SessionState, TranslationPipelineMode
from hki.server import app as appmod


@pytest.fixture()
def client():
    appmod.session.clear_translation_context()
    appmod.session.clear_session_log()
    appmod.session.state = SessionState.IDLE
    appmod.session.sermon_on = False
    appmod.session.test_mode = False
    appmod.session.translation_pipeline = TranslationPipelineMode.LEGACY
    with TestClient(appmod.app) as c:
        yield c
    appmod.session.clear_translation_context()
    appmod.session.clear_session_log()
    appmod.session.state = SessionState.IDLE
    appmod.session.translation_pipeline = TranslationPipelineMode.LEGACY


def test_session_log_has_three_columns():
    session = LiveSession()
    session.add_transcript("안녕하세요")
    session.add_legacy_translation("Buenos días (clásico)")
    session.add_sentence_translation("Buenos días (oración)")
    session.add_sentence_trace(
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "fragment_ids": ["a"],
            "original_stt": "안녕하세요",
            "action": "release",
            "ko_corrected": "안녕하세요",
            "stt_repair": False,
            "release_reason": "closed_immediate",
            "translation": "Buenos días (oración)",
            "recombine_llm_ms": 10,
            "translate_llm_ms": 20,
            "fragment_count": 1,
            "unit_index": 0,
            "unit_count": 1,
            "recombine_id": "r1",
            "repair_rejected": False,
            "t_audio_start_source": "speech_started",
        }
    )
    session.add_legacy_trace(
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "fragment_ids": ["a"],
            "original_stt": "안녕하세요",
            "action": "release",
            "fragment_count": 1,
            "ko_corrected": "안녕하세요",
            "stt_repair": False,
            "release_reason": "closed_immediate",
            "translation": "Buenos días (clásico)",
            "joined_preview": "Buenos días (clásico)",
            "recombine_llm_ms": 0,
            "used_llm_recombine": False,
            "repair_rejected": False,
            "t_audio_start_source": "first_delta",
        }
    )
    log = session.to_log()
    assert log["transcripts"] == ["안녕하세요"]
    assert log["translations_legacy"] == ["Buenos días (clásico)"]
    assert log["translations_sentence"] == ["Buenos días (oración)"]
    assert log["sentence_traces"][0]["original_stt"] == "안녕하세요"
    assert log["legacy_traces"][0]["original_stt"] == "안녕하세요"
    assert log["sentence_release_stats"]["counts"]["closed_immediate"] == 1
    assert log["sentence_recombine_stats"]["recombine_count"] == 1
    assert log["sentence_recombine_stats"]["fragments_per_recombine"] == 1.0
    assert log["legacy_release_stats"]["counts"]["closed_immediate"] == 1
    assert "through_index" not in log["legacy_traces"][0]
    assert "latency_recombine" not in log["legacy_traces"][0]
    assert log["has_log"] is True
    assert "translations_legacy_v2" not in log
    assert "legacy_v2_traces" not in log
    assert "pipeline_legacy_v2_enabled" not in log


def test_session_token_comment():
    session = LiveSession()
    session.add_token_usage("legacy", 1000, 80, kind="translate")
    session.add_token_usage("legacy", 400, 40, kind="recombine")
    session.add_token_usage("sentence", 1000, 20, kind="recombine")
    session.add_token_usage("sentence", 1000, 40, kind="translate")
    session.add_sentence_trace(
        {
            "action": "hold",
            "release_reason": "translation_failed",
            "translation": "",
            "original_stt": "오늘 우리가",
        }
    )
    log = session.to_log()
    comment = log["token_comment"]
    assert "Clásico: 1400 in / 120 out  (traducir 1 + recombine 1)" in comment
    assert "recombinar 1 + traducir 1" in comment
    assert "translation_failed: 1" in comment
    assert "STT" in comment
    session.clear_session_log()
    assert session.to_log()["token_comment"] == ""


def test_status_exposes_pipeline_env_flags(client):
    res = client.get("/api/live/status")
    data = res.json()
    assert "pipeline_legacy_enabled" in data
    assert "pipeline_sentence_enabled" in data
    assert "pipeline_legacy_v2_enabled" not in data
    assert data["translation_pipeline"] in ("legacy", "sentence", "both")


def test_test_play_uses_env_pipelines(client, monkeypatch):
    appmod._test_pcm = b"\x00\x00" * 2400
    appmod._test_duration = 0.1
    appmod._test_filename = "test.wav"

    async def fake_start_test(pcm, duration, filename):
        appmod.pipeline._apply_pipeline_mode_from_config()

    monkeypatch.setattr(appmod.pipeline, "start_test_streaming", fake_start_test)
    monkeypatch.setattr(appmod.pipeline, "stop_monitor", lambda: None)

    res = client.post("/api/live/test/play")
    data = res.json()
    assert data["ok"] is True
    assert data["translation_pipeline"] in ("legacy", "sentence", "both")
    assert "pipeline_legacy_enabled" in data


def test_legacy_trace_from_item_in_session_log():
    from hki.live.pipeline import legacy_trace_from_item
    from hki.live.release_pacer import ReleaseItem

    item = ReleaseItem(
        batch_id="a",
        es="Buenos días",
        item_ids=["a", "b"],
        ko_summary="안녕 하세요",
        ko_corrected="안녕하세요",
        stt_repair=True,
        release_reason="closed_immediate",
        joined_preview="Buenos  días",
        used_llm_recombine=True,
        recombine_llm_ms=40,
        had_incierto=True,
        fragment_count=2,
        t_audio_start_source="speech_started",
    )
    session = LiveSession()
    session.add_legacy_translation(item.es)
    session.add_legacy_trace(legacy_trace_from_item(item))
    log = session.to_log()
    trace = log["legacy_traces"][0]
    assert trace["original_stt"] == "안녕 하세요"
    assert trace["ko_corrected"] == "안녕하세요"
    assert trace["stt_repair"] is True
    assert trace["translation"] == "Buenos días"
    assert trace["joined_preview"] == "Buenos  días"
    assert trace["fragment_count"] == 2
    assert trace["used_llm_recombine"] is True
    assert trace["recombine_llm_ms"] == 40
    assert "through_index" not in trace
    assert "latency_recombine" not in trace
    assert log["legacy_release_stats"]["counts"]["closed_immediate"] == 1
    assert "Clásico Release: closed_immediate: 100%" in log["token_comment"]
    assert "speech_started 1" in log["token_comment"]
