"""API route tests for Contextualizar and reset-context."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import SessionState
from hki.server import app as appmod


@pytest.fixture()
def client():
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE
    appmod.session.test_mode = False
    with TestClient(appmod.app) as c:
        yield c
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE


def _fake_context_payload():
    context = {
        "generated_at": "2026-08-06T12:00:00+00:00",
        "bible_references": ["Mateo 1:1"],
        "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro de la genealogía…"}],
        "bible_es_source": "bible_api",
        "sermon_summary": "Resumen",
        "outline": ["Intro"],
        "terminology": [],
        "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
        "style_notes": "tono respetuoso: usted, hermanos",
    }
    passage = {"ko": "마태복음 1:1", "nvi": "Mateo 1:1 Libro…"}
    return context, passage, []


def test_contextualizar_rejects_empty_bible(client):
    res = client.post(
        "/api/live/contextualizar",
        json={"bible_text": "  ", "manuscript": "x"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert "bíblico" in data["error"].lower() or "obligatorio" in data["error"].lower()


def test_contextualizar_success(client, monkeypatch):
    context, passage, warnings = _fake_context_payload()

    async def fake_build(bible, manuscript):
        assert bible == "마태복음 1:1"
        return context, passage, warnings

    monkeypatch.setattr(appmod, "build_translation_context", fake_build)

    res = client.post(
        "/api/live/contextualizar",
        json={"bible_text": "마태복음 1:1", "manuscript": "원고"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["context_ready"] is True
    assert data["passage_display"]["ko"] == "마태복음 1:1"
    assert appmod.session.context_ready is True
    assert appmod.session.bible_text == "마태복음 1:1"
    assert appmod.session.manuscript == "원고"


def test_contextualizar_locked_after_success(client, monkeypatch):
    context, passage, warnings = _fake_context_payload()

    async def fake_build(bible, manuscript):
        return context, passage, warnings

    monkeypatch.setattr(appmod, "build_translation_context", fake_build)

    first = client.post(
        "/api/live/contextualizar",
        json={"bible_text": "마태복음 1:1", "manuscript": ""},
    )
    assert first.json()["ok"] is True

    second = client.post(
        "/api/live/contextualizar",
        json={"bible_text": "요한복음 3:16", "manuscript": ""},
    )
    data = second.json()
    assert data["ok"] is False
    assert "bloqueado" in data["error"].lower()


def test_contextualizar_fatal_value_error(client, monkeypatch):
    async def fake_build(bible, manuscript):
        raise ValueError(
            "API Biblia no autorizada (HTTP 401). "
            "Revisá HKI_BIBLE_API_BASE / acceso a Midvash."
        )

    monkeypatch.setattr(appmod, "build_translation_context", fake_build)

    res = client.post(
        "/api/live/contextualizar",
        json={"bible_text": "마태복음 1:1", "manuscript": ""},
    )
    data = res.json()
    assert data["ok"] is False
    assert "401" in data["error"] or "autorizada" in data["error"].lower()
    assert appmod.session.context_ready is False


def test_reset_context_clears_ready(client, monkeypatch):
    context, passage, warnings = _fake_context_payload()

    async def fake_build(bible, manuscript):
        return context, passage, warnings

    monkeypatch.setattr(appmod, "build_translation_context", fake_build)
    client.post(
        "/api/live/contextualizar",
        json={"bible_text": "마태복음 1:1", "manuscript": "원고"},
    )
    assert appmod.session.context_ready is True

    res = client.post("/api/live/reset-context")
    data = res.json()
    assert data["ok"] is True
    assert data["context_ready"] is False
    assert appmod.session.context_ready is False
    assert appmod.session.passage_display is None
    assert appmod.session.bible_text == ""
    assert appmod.session.manuscript == ""


def test_reset_context_when_already_clear(client):
    res = client.post("/api/live/reset-context")
    data = res.json()
    assert data["ok"] is True
    assert data["context_ready"] is False
