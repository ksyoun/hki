"""Late joiners receive prior audience captions over WebSocket."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import LiveSession, SessionState
from hki.server import app as appmod


def test_caption_display_log_tracks_ids_and_lyrics():
    session = LiveSession()
    session.add_final_translation("Hola", item_id="batch-1", item_ids=["a", "b"])
    session.add_final_translation(
        "♪ Gracia ♪",
        item_id="lyrics-title-1",
        lyrics=True,
    )
    session.add_final_translation("  ")
    lines = session.caption_snapshot_lines()
    assert session.translation_final_log == ["Hola", "♪ Gracia ♪"]
    assert lines[0]["item_id"] == "batch-1"
    assert lines[0]["item_ids"] == ["batch-1", "a", "b"]
    assert lines[0]["lyrics"] is False
    assert lines[1]["lyrics"] is True
    assert lines[1]["item_id"] == "lyrics-title-1"
    session.clear_session_log()
    assert session.caption_snapshot_lines() == []


@pytest.fixture()
def client():
    appmod.session.clear_session_log()
    appmod.session.state = SessionState.IDLE
    with TestClient(appmod.app) as c:
        yield c
    appmod.session.clear_session_log()
    appmod.session.state = SessionState.IDLE


async def _fake_status():
    await appmod.broadcaster.broadcast({"type": "status", "state": "idle"})


def _receive_until(ws, type_name: str, limit: int = 8) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == type_name:
            return msg
    raise AssertionError(f"no {type_name} in first {limit} messages")


def test_ws_sends_caption_snapshot_to_late_joiner(client, monkeypatch):
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _fake_status)
    appmod.session.add_final_translation(
        "Hola hermanos",
        item_id="batch-1",
        item_ids=["utt-1"],
    )
    appmod.session.add_final_translation(
        "♪ Tú eres mi refugio ♪",
        item_id="lyrics-caption-1",
        lyrics=True,
    )
    with client.websocket_connect("/ws/live?role=audience") as ws:
        snap = _receive_until(ws, "captions_snapshot")
        lines = snap["lines"]
        assert [row["text"] for row in lines] == [
            "Hola hermanos",
            "♪ Tú eres mi refugio ♪",
        ]
        assert lines[0]["item_id"] == "batch-1"
        assert "utt-1" in lines[0]["item_ids"]
        assert lines[1]["lyrics"] is True


def test_ws_skips_empty_caption_snapshot(client, monkeypatch):
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _fake_status)
    with client.websocket_connect("/ws/live?role=audience") as ws:
        first = ws.receive_json()
        assert first["type"] != "captions_snapshot"
        assert first["type"] == "status"
