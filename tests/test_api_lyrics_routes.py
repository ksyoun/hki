"""API: lyrics lookup/save/show and pause/resume caption marks (no TTS)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import SessionState
from hki.server import app as appmod


@pytest.fixture()
def client():
    appmod.session.clear_translation_context()
    appmod.session.clear_lyrics()
    appmod.session.state = SessionState.IDLE
    appmod.session.test_mode = False
    with TestClient(appmod.app) as c:
        yield c
    appmod.session.clear_lyrics()
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE


def _sample_songs():
    return [
        {
            "query": "새찬송가 310장",
            "label": "새찬송가 310장",
            "title_es": "Gracia admirable",
            "confidence": "medium",
            "note": "",
            "slides": ["Gracia admirable, cuán dulce el sonido", "Me salvó a mí"],
        }
    ]


async def _noop(*_a, **_k):
    return None


def test_lyrics_lookup_rejects_empty(client):
    res = client.post("/api/live/lyrics/lookup", json={"queries": "  \n  "})
    data = res.json()
    assert data["ok"] is False
    assert "canción" in data["error"].lower() or "cancion" in data["error"].lower()


def test_lyrics_lookup_saves_songs(client, monkeypatch):
    async def fake_lookup(queries):
        assert queries == ["새찬송가 310장"]
        return _sample_songs(), ["Revisar"]

    monkeypatch.setattr(appmod, "lookup_lyrics", fake_lookup)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)

    res = client.post(
        "/api/live/lyrics/lookup",
        json={"queries": "새찬송가 310장"},
    )
    data = res.json()
    assert data["ok"] is True
    assert data["lyrics_ready"] is True
    assert data["songs"][0]["slides"][0].startswith("Gracia")
    assert appmod.session.lyrics_ready() is True


def test_lyrics_save_roundtrip(client, monkeypatch):
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    res = client.post("/api/live/lyrics/save", json={"songs": _sample_songs()})
    data = res.json()
    assert data["ok"] is True
    got = client.get("/api/live/lyrics").json()
    assert got["songs"][0]["label"] == "새찬송가 310장"
    assert got["songs"][0]["slides"] == [
        "Gracia admirable, cuán dulce el sonido",
        "Me salvó a mí",
    ]


def test_lyrics_show_requires_pause(client, monkeypatch):
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    appmod.session.set_lyrics_songs(_sample_songs())
    res = client.post(
        "/api/live/lyrics/show",
        json={"action": "title", "song_index": 0},
    )
    assert res.json()["ok"] is False


def test_pause_then_chip_then_verses_then_fin(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_resume():
        appmod.session.resume()

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "resume", fake_resume)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    appmod.session.state = SessionState.STREAMING
    appmod.session.set_lyrics_songs(_sample_songs())

    pause = client.post("/api/live/pause").json()
    assert pause["ok"] is True
    assert appmod.session.state == SessionState.PAUSED
    marks = [m for m in messages if m.get("type") == "lyrics"]
    assert marks and marks[0]["kind"] == "mark"
    assert marks[0]["text"] == "♪"
    assert "audio" not in marks[0]
    assert appmod.session.translation_final_log[-1] == "♪"

    messages.clear()
    title = client.post(
        "/api/live/lyrics/show",
        json={"action": "title", "song_index": 0},
    ).json()
    assert title["ok"] is True
    assert title["lyrics_song_index"] == 0
    titles = [m for m in messages if m.get("kind") == "title"]
    assert titles[0]["text"] == "♪ 새찬송가 310장 ♪"

    messages.clear()
    nxt = client.post("/api/live/lyrics/show", json={"action": "next"}).json()
    assert nxt["ok"] is True
    verses = [m for m in messages if m.get("kind") == "verse"]
    assert verses[0]["text"].startswith("♪ Gracia admirable")

    messages.clear()
    nxt2 = client.post("/api/live/lyrics/show", json={"action": "next"}).json()
    assert nxt2["ok"] is True
    end = client.post("/api/live/lyrics/show", json={"action": "next"}).json()
    assert end.get("at_end") is True

    messages.clear()
    resume = client.post("/api/live/resume").json()
    assert resume["ok"] is True
    fins = [m for m in messages if m.get("kind") == "fin"]
    assert fins and fins[0]["text"] == "♪ FIN ♪"
    assert appmod.session.state == SessionState.STREAMING
    assert appmod.session.lyrics_song_index is None


def test_pause_without_chip_still_sends_mark_and_fin(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_resume():
        appmod.session.resume()

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "resume", fake_resume)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    appmod.session.state = SessionState.STREAMING
    client.post("/api/live/pause")
    kinds = [m.get("kind") for m in messages if m.get("type") == "lyrics"]
    assert kinds == ["mark"]
    messages.clear()
    client.post("/api/live/resume")
    kinds = [m.get("kind") for m in messages if m.get("type") == "lyrics"]
    assert kinds == ["fin"]
