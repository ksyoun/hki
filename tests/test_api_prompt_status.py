"""Status API exposes live translation prompt mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import SessionState
from hki.server import app as appmod


@pytest.fixture()
def client():
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE
    appmod.session.sermon_on = False
    appmod.session.test_mode = False
    with TestClient(appmod.app) as c:
        yield c
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE
    appmod.session.sermon_on = False


def _fake_context():
    return {
        "generated_at": "t",
        "sermon_summary": "Resumen de prueba",
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "NVI texto"}],
        "outline": [],
        "terminology": [],
        "bible_books": [],
        "style_notes": "",
    }


def test_status_prompt_general_when_sermon_off(client):
    appmod.session.set_translation_context(
        "원고",
        _fake_context(),
        {"ko": "마태복음 1:1", "nvi": "…"},
    )
    appmod.session.sermon_on = False

    res = client.get("/api/live/status")
    data = res.json()
    assert data["context_ready"] is True
    assert data["sermon_on"] is False
    assert data["translation_prompt_mode"] == "general"
    assert data["translation_prompt_includes_nvi"] is False
    assert data["translation_prompt_includes_context_summary"] is False
    assert "general" in data["translation_prompt_label"]


def test_status_prompt_sermon_context_when_sermon_on(client):
    appmod.session.set_translation_context(
        "원고",
        _fake_context(),
        {"ko": "마태복음 1:1", "nvi": "…"},
    )
    appmod.session.sermon_on = True

    res = client.get("/api/live/status")
    data = res.json()
    assert data["translation_prompt_mode"] == "sermon_context"
    assert data["translation_prompt_includes_nvi"] is True
    assert data["translation_prompt_includes_context_summary"] is True
    assert data["translation_prompt_len"] > 200


def test_sermon_on_endpoint_returns_prompt_mode(client, monkeypatch):
    context = _fake_context()
    passage = {"ko": "마태복음 1:1", "nvi": "…"}

    async def fake_build(bible, manuscript):
        return context, passage, []

    monkeypatch.setattr(appmod, "build_translation_context", fake_build)
    client.post(
        "/api/live/contextualizar",
        json={"bible_text": "마태복음 1:1", "manuscript": ""},
    )
    appmod.session.state = SessionState.STREAMING
    appmod.session.sermon_on = False

    res = client.post("/api/live/sermon-on")
    data = res.json()
    assert data["ok"] is True
    assert data["sermon_on"] is True
    assert data["translation_prompt_mode"] == "sermon_context"
    assert data["translation_prompt_includes_nvi"] is True
