"""v2 rundown: normalize/insert/move and cue-driven pause/resume/sermon."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import SessionState
from hki.server import app as appmod
from hki.v2.engine import format_cue_caption
from hki.v2.rundown import insert_slide, move_slide, normalize_slides, page_of, reset_store


def test_format_cue_caption():
    assert format_cue_caption("caption", "Tú eres mi refugio") == "♪ Tú eres mi refugio ♪"
    assert format_cue_caption("caption", "") == "♪"
    assert format_cue_caption("bible", "") == "📖 Lectura Bíblica 📖"
    assert format_cue_caption("speak", "") == "🎤 Servicio 🎤"
    assert format_cue_caption("speak", "oración") == "🎤 oración 🎤"
    assert format_cue_caption("sermon") == "✝ Sermón ✝"


@pytest.fixture()
def client():
    reset_store()
    appmod.session.clear_translation_context()
    appmod.session.clear_lyrics()
    appmod.session.state = SessionState.IDLE
    appmod.session.sermon_on = False
    appmod.session.test_mode = False
    with TestClient(appmod.app) as c:
        yield c
    reset_store()
    appmod.session.clear_lyrics()
    appmod.session.clear_translation_context()
    appmod.session.state = SessionState.IDLE
    appmod.session.sermon_on = False


async def _noop(*_a, **_k):
    return None


def test_insert_and_move_renumber():
    slides = normalize_slides(
        [
            {"cue": "speak", "label": "inicio"},
            {"cue": "caption", "ko": "주", "es": "Señor"},
        ]
    )
    assert page_of(-1) == 0
    slides = insert_slide(slides, 1, "bible")
    assert [s["cue"] for s in slides] == ["speak", "bible", "caption"]
    assert slides[1]["label"] == "Lectura bíblica"
    slides = move_slide(slides, 2, 0)
    assert [s["cue"] for s in slides] == ["caption", "speak", "bible"]
    assert slides[0]["es"] == "Señor"


def test_v2_page_served(client):
    res = client.get("/v2")
    assert res.status_code == 200
    assert b"Rundown" in res.content
    assert b"/v2-static/control.js" in res.content


def test_goto_rejected_when_idle(client):
    client.post(
        "/api/v2/rundown",
        json={"slides": [{"cue": "caption", "ko": "주", "es": "Hola"}]},
    )
    res = client.post("/api/v2/rundown/goto", json={"action": "next"})
    data = res.json()
    assert data["ok"] is False
    assert "transmisión" in data["error"].lower() or "transmision" in data["error"].lower()


def test_caption_enter_pauses_and_sends_lyrics(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    client.post(
        "/api/v2/rundown",
        json={
            "slides": [{"cue": "caption", "ko": "주 나의 피난처", "es": "Tú eres mi refugio"}],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    assert res["cursor"] == 0
    assert res["page"] == 1
    assert appmod.session.state == SessionState.PAUSED
    verses = [m for m in messages if m.get("type") == "lyrics" and m.get("kind") == "caption"]
    assert verses
    assert verses[0]["text"] == "♪ Tú eres mi refugio ♪"
    assert "audio" not in verses[0]


def test_speak_enter_announces_cue(client, monkeypatch):
    messages: list[dict] = []

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.pipeline, "set_sermon_mode", _noop)
    monkeypatch.setattr(appmod.pipeline, "resume", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    client.post(
        "/api/v2/rundown",
        json={
            "slides": [{"cue": "speak", "label": "oración"}],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    marks = [m for m in messages if m.get("type") == "lyrics" and m.get("kind") == "speak"]
    assert marks
    assert marks[0]["text"] == "🎤 oración 🎤"
    assert "audio" not in marks[0]


def test_caption_to_speak_announces_and_resume(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_resume():
        appmod.session.resume()

    async def fake_sermon(on):
        appmod.session.sermon_on = on

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "resume", fake_resume)
    monkeypatch.setattr(appmod.pipeline, "set_sermon_mode", fake_sermon)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    client.post(
        "/api/v2/rundown",
        json={
            "slides": [
                {"cue": "caption", "es": "Letra"},
                {"cue": "speak", "label": "oración"},
            ],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    client.post("/api/v2/rundown/goto", json={"index": 0})
    messages.clear()
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    assert res["current_cue"] == "speak"
    marks = [m for m in messages if m.get("kind") == "speak"]
    assert marks
    assert marks[0]["text"] == "🎤 oración 🎤"
    assert appmod.session.state == SessionState.STREAMING
    assert appmod.session.sermon_on is False


def test_speak_to_sermon_sets_mode(client, monkeypatch):
    called: list[bool] = []

    async def fake_sermon(on):
        called.append(on)
        appmod.session.sermon_on = on

    monkeypatch.setattr(appmod.pipeline, "set_sermon_mode", fake_sermon)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.pipeline, "resume", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", _noop)

    client.post(
        "/api/v2/rundown",
        json={
            "slides": [
                {"cue": "speak", "label": "inicio"},
                {"cue": "sermon", "label": "설교"},
            ],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    client.post("/api/v2/rundown/goto", json={"index": 0})
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    assert res["current_cue"] == "sermon"
    assert True in called
    assert appmod.session.sermon_on is True


def test_bible_cue_pauses_and_sends_nvi(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    appmod.session.passage_display = {
        "ko": "요 3:16",
        "nvi": "Juan 3:16 Porque tanto amó Dios al mundo",
    }
    appmod.session.translation_context = {
        "bible_es_nvi": [
            {"ref": "Juan 3:16", "text": "Porque tanto amó Dios al mundo"},
            {"ref": "Juan 3:17", "text": "Dios no envió a su Hijo"},
        ]
    }
    client.post(
        "/api/v2/rundown",
        json={"slides": [{"cue": "bible", "label": "본문봉독"}], "cursor": -1},
    )
    appmod.session.state = SessionState.STREAMING
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    assert res["current_cue"] == "bible"
    assert appmod.session.state == SessionState.PAUSED
    bible = [m for m in messages if m.get("type") == "lyrics" and m.get("kind") == "bible"]
    assert len(bible) == 2
    assert "Porque tanto amó Dios al mundo" in bible[0]["text"]
    assert bible[0]["text"].startswith("📖")
    assert "audio" not in bible[0]


def test_bible_without_nvi_sends_lectura_biblica(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    appmod.session.passage_display = None
    appmod.session.translation_context = None
    client.post(
        "/api/v2/rundown",
        json={"slides": [{"cue": "bible", "label": "본문봉독"}], "cursor": -1},
    )
    appmod.session.state = SessionState.STREAMING
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    bible = [m for m in messages if m.get("kind") == "bible"]
    assert bible
    assert bible[0]["text"] == "📖 Lectura Bíblica 📖"


def test_bible_to_sermon_announces_and_resume(client, monkeypatch):
    messages: list[dict] = []

    async def fake_pause():
        appmod.session.pause()

    async def fake_resume():
        appmod.session.resume()

    async def fake_sermon(on):
        appmod.session.sermon_on = on

    async def fake_broadcast(msg):
        messages.append(msg)

    monkeypatch.setattr(appmod.pipeline, "pause", fake_pause)
    monkeypatch.setattr(appmod.pipeline, "resume", fake_resume)
    monkeypatch.setattr(appmod.pipeline, "set_sermon_mode", fake_sermon)
    monkeypatch.setattr(appmod.pipeline, "broadcast_status", _noop)
    monkeypatch.setattr(appmod.broadcaster, "broadcast", fake_broadcast)

    appmod.session.passage_display = {"ko": "", "nvi": "Juan 3:16 Porque tanto amó Dios"}
    client.post(
        "/api/v2/rundown",
        json={
            "slides": [
                {"cue": "bible", "label": "봉독"},
                {"cue": "sermon", "label": "설교"},
            ],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    client.post("/api/v2/rundown/goto", json={"index": 0})
    messages.clear()
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    assert res["current_cue"] == "sermon"
    marks = [m for m in messages if m.get("kind") == "sermon"]
    assert marks
    assert marks[0]["text"] == "✝ Sermón ✝"
    assert appmod.session.state == SessionState.STREAMING
    assert appmod.session.sermon_on is True
