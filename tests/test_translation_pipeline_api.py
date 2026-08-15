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
    log = session.to_log()
    assert log["transcripts"] == ["안녕하세요"]
    assert log["translations_legacy"] == ["Buenos días (clásico)"]
    assert log["translations_sentence"] == ["Buenos días (oración)"]
    assert log["has_log"] is True


def test_session_token_comment():
    session = LiveSession()
    session.add_token_usage("legacy", 1000, 80, kind="translate")
    session.add_token_usage("legacy", 400, 40, kind="recombine")
    session.add_token_usage("sentence", 2000, 60)
    log = session.to_log()
    comment = log["token_comment"]
    assert "Clásico: 1400 in / 120 out  (traducir 1 + recombine 1)" in comment
    assert "Por oración: 2000 in / 60 out  (1 llamadas)" in comment
    assert "STT" in comment
    session.clear_session_log()
    assert session.to_log()["token_comment"] == ""


def test_status_exposes_pipeline_env_flags(client):
    res = client.get("/api/live/status")
    data = res.json()
    assert "pipeline_legacy_enabled" in data
    assert "pipeline_sentence_enabled" in data
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
