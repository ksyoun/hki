"""v2 rundown: normalize/insert/move and cue-driven pause/resume/sermon."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hki.live.session import SessionState
from hki.server import app as appmod
from hki.v2.engine import format_cue_caption
from hki.v2.prepare import body_without_append, join_body_append
from hki.v2.rundown import (
    CALL_APPEND_ES,
    CALL_APPEND_KO,
    CLOSING_ES,
    CREED_GUIDE_ES,
    CREED_LINES_ES,
    WELCOME_ES,
    culto_dominical_es,
    default_sunday_slides,
    insert_slide,
    move_slide,
    normalize_slides,
    page_of,
    reset_store,
)


def test_format_cue_caption():
    assert format_cue_caption("caption", "Tú eres mi refugio") == "♪ Tú eres mi refugio ♪"
    assert format_cue_caption("caption", "") == "♪"
    assert format_cue_caption("bible", "") == "📖 Lectura Bíblica 📖"
    assert format_cue_caption("speak", "") == "🎤 Servicio 🎤"
    assert format_cue_caption("speak", "oración") == "🎤 oración 🎤"
    assert format_cue_caption("sermon") == "✝ Sermón ✝"
    assert format_cue_caption("sermon", "El reino de Dios") == "✝ El reino de Dios ✝"


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
    assert b"Cargar modelo" in res.content
    assert b"Convertir" in res.content


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
    assert marks[0]["text"] == "✝ 설교 ✝"
    assert appmod.session.state == SessionState.STREAMING
    assert appmod.session.sermon_on is True


def test_sermon_caption_prefers_spanish_title(client, monkeypatch):
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
            "slides": [
                {
                    "cue": "sermon",
                    "ko": "하나님 나라",
                    "es": "El reino de Dios",
                }
            ],
            "cursor": -1,
        },
    )
    appmod.session.state = SessionState.STREAMING
    res = client.post("/api/v2/rundown/goto", json={"action": "next"}).json()
    assert res["ok"] is True
    marks = [m for m in messages if m.get("kind") == "sermon"]
    assert marks
    assert marks[0]["text"] == "✝ El reino de Dios ✝"


def test_default_sunday_slides_creed_split():
    slides = default_sunday_slides()
    cues = [s["cue"] for s in slides]
    assert cues[:7] == [
        "caption",
        "caption",
        "caption",
        "caption",
        "caption",
        "speak",
        "caption",
    ]
    assert "bible" in cues
    assert "sermon" in cues
    guide = next(i for i, s in enumerate(slides) if s.get("es") == CREED_GUIDE_ES)
    assert slides[guide]["ko"] == "사도신경"
    assert [s["es"] for s in slides[guide + 1 : guide + 1 + len(CREED_LINES_ES)]] == list(
        CREED_LINES_ES
    )
    assert slides[guide + len(CREED_LINES_ES)]["es"].endswith("vida perdurable. Amén.")
    call = next(s for s in slides if s.get("label") == "예배의 부름")
    assert call["append_ko"] == CALL_APPEND_KO
    assert call["append_es"] == CALL_APPEND_ES
    abs_slide = next(s for s in slides if s.get("label") == "사죄선언")
    assert "정죄함이 없느니라. 아멘" in abs_slide["append_ko"]
    captions = [s for s in slides if s["cue"] == "caption"]
    assert captions[0]["es"] == WELCOME_ES
    assert captions[2]["es"] == culto_dominical_es()
    assert slides[-1]["es"] == CLOSING_ES
    prayer = next(s for s in slides if s.get("label") == "대표기도")
    assert prayer["cue"] == "caption"
    assert prayer["es"] == "Oración:"
    assert any(s["cue"] == "speak" and s.get("label") == "Anuncios" for s in slides)
    assert any(
        s["cue"] == "speak" and s.get("label") == "Oración final de bendición"
        for s in slides
    )
    sermon = next(s for s in slides if s["cue"] == "sermon")
    assert not (sermon.get("label") or sermon.get("ko") or sermon.get("es"))


def test_empty_get_seeds_sunday_modelo(client):
    res = client.get("/api/v2/rundown").json()
    assert res["ok"] is True
    assert any(s.get("es") == CREED_GUIDE_ES for s in res["slides"])
    assert res["cursor"] == -1


def test_cargar_modelo_replaces_rundown(client):
    client.post("/api/v2/rundown", json={"slides": [{"cue": "speak", "label": "solo"}]})
    res = client.post("/api/v2/rundown/modelo").json()
    assert res["ok"] is True
    assert len(res["slides"]) > 1
    assert any(s.get("es") == CREED_GUIDE_ES for s in res["slides"])


def test_nvi_fill_order_and_single_amen(client, monkeypatch):
    seen: list[str] = []

    async def fake_resolve(bible_text):
        seen.append(bible_text)
        return [], [{"ref": "Salmos 100:1", "text": "Canten alegres a Dios"}], []

    monkeypatch.setattr("hki.v2.prepare._resolve_nvi_verses", fake_resolve)
    body = "시편 100:1 온 땅이여 여호와께 즐거운 찬송을 부를지어다"
    client.post(
        "/api/v2/rundown",
        json={
            "slides": [
                {
                    "cue": "caption",
                    "ko": body,
                    "append_ko": CALL_APPEND_KO,
                    "append_es": CALL_APPEND_ES,
                }
            ],
            "cursor": -1,
        },
    )
    res = client.post(
        "/api/v2/rundown/translate", json={"index": 0, "mode": "nvi"}
    ).json()
    assert res["ok"] is True
    slide = res["slides"][0]
    assert seen == [body]
    assert CALL_APPEND_KO not in seen[0]
    assert slide["ko"] == f"{body}\n{CALL_APPEND_KO}"
    assert slide["es"] == f"Salmos 100:1 Canten alegres a Dios\n{CALL_APPEND_ES}"
    assert slide["es"].count("Amén") == 1
    assert not slide["es"].startswith("Amén")
    assert slide["es"].endswith("Amén.")

    res2 = client.post(
        "/api/v2/rundown/translate", json={"index": 0, "mode": "nvi"}
    ).json()
    assert res2["ok"] is True
    slide2 = res2["slides"][0]
    assert seen == [body, body]
    assert slide2["es"] == slide["es"]
    assert slide2["ko"] == slide["ko"]
    assert slide2["es"].count("Amén") == 1


def test_body_without_append_strips_once():
    body = "본문"
    full = join_body_append(body, CALL_APPEND_KO)
    assert body_without_append(full, CALL_APPEND_KO) == body
    assert body_without_append(body, CALL_APPEND_KO) == body


def test_convertir_uses_nvi_and_es(client, monkeypatch):
    nvi_seen: list[str] = []
    es_seen: list[str] = []

    async def fake_resolve(bible_text):
        nvi_seen.append(bible_text)
        return [], [{"ref": "Salmos 100:1", "text": "Canten alegres a Dios"}], []

    async def fake_es(ko, *, context=None, passage_display=None):
        es_seen.append(ko)
        return f"ES:{ko}"

    monkeypatch.setattr("hki.v2.prepare._resolve_nvi_verses", fake_resolve)
    monkeypatch.setattr("hki.v2.prepare.translate_ko_line", fake_es)
    client.post(
        "/api/v2/rundown",
        json={
            "slides": [
                {"cue": "caption", "ko": "찬양1"},
                {
                    "cue": "caption",
                    "ko": "시편 100:1 온 땅이여",
                    "append_ko": CALL_APPEND_KO,
                    "append_es": CALL_APPEND_ES,
                },
                {"cue": "caption", "ko": "주 나의 피난처"},
                {"cue": "speak", "label": "기도1"},
                {"cue": "caption", "ko": "사도신경", "es": "Credo Apostólico"},
                {"cue": "sermon", "ko": "하나님 나라"},
            ],
            "cursor": -1,
        },
    )
    res = client.post("/api/v2/rundown/convertir").json()
    assert res["ok"] is True
    slides = res["slides"]
    assert nvi_seen == ["시편 100:1 온 땅이여"]
    assert es_seen == ["주 나의 피난처", "하나님 나라"]
    assert slides[0]["es"] == ""
    assert slides[1]["es"].endswith(CALL_APPEND_ES)
    assert slides[1]["es"].count("Amén") == 1
    assert slides[2]["es"] == "ES:주 나의 피난처"
    assert slides[4]["es"] == "Credo Apostólico"
    assert slides[5]["cue"] == "sermon"
    assert slides[5]["es"] == "ES:하나님 나라"


def test_convertir_empty_korean_warns(client):
    client.post(
        "/api/v2/rundown",
        json={"slides": [{"cue": "caption", "ko": "찬양1"}]},
    )
    res = client.post("/api/v2/rundown/convertir").json()
    assert res["ok"] is False
    assert "coreano" in res["error"].lower()
