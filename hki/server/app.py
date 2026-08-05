"""FastAPI server — REST API, WebSocket, static UI."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hki import config
from hki.live.audio import find_scarlett, list_devices
from hki.live.broadcaster import Broadcaster
from hki.live.pipeline import LivePipeline
from hki.live.session import LiveSession, SessionState

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="HKI Live Translation")
broadcaster = Broadcaster()
session = LiveSession()
pipeline = LivePipeline(session, broadcaster)

# Auto-detect Scarlett on startup
_scarlett = find_scarlett()
if _scarlett:
    session.device_index = _scarlett.index


class SessionConfig(BaseModel):
    bible_text: str = ""
    manuscript: str = ""
    device_index: int | None = None
    gain: float | None = None


class GainUpdate(BaseModel):
    gain: float


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/")
async def control_page():
    return FileResponse(STATIC_DIR / "control.html")


@app.get("/captions")
async def captions_page():
    return FileResponse(STATIC_DIR / "captions.html")


@app.get("/api/audio-devices")
async def audio_devices():
    devices = list_devices()
    scarlett = find_scarlett()
    return {
        "devices": [
            {"index": d.index, "name": d.name, "sample_rate": d.sample_rate}
            for d in devices
        ],
        "scarlett_index": scarlett.index if scarlett else None,
    }


@app.get("/api/live/status")
async def live_status():
    session.listener_count = broadcaster.listener_count
    return {
        **session.to_status(),
        "local_ip": _local_ip(),
        "port": config.PORT,
        "captions_url": f"http://{_local_ip()}:{config.PORT}/captions",
        "draft_enabled": config.DRAFT_ENABLED,
    }


@app.post("/api/live/session")
async def configure_session(cfg: SessionConfig):
    pipeline._stop_all()
    session.stop()
    session.configure(
        bible_text=cfg.bible_text,
        manuscript=cfg.manuscript,
        device_index=cfg.device_index,
        gain=cfg.gain,
    )
    return {"ok": True}


@app.post("/api/live/monitor/start")
async def monitor_start():
    if session.state == SessionState.STREAMING:
        return {"ok": False, "error": "스트리밍 중에는 모니터를 시작할 수 없습니다"}
    pipeline.start_monitor()
    return {"ok": True}


@app.post("/api/live/monitor/stop")
async def monitor_stop():
    pipeline.stop_monitor()
    return {"ok": True}


@app.patch("/api/live/gain")
async def update_gain(body: GainUpdate):
    pipeline.set_gain(body.gain)
    return {"ok": True, "gain": session.gain}


@app.post("/api/live/start")
async def start_streaming():
    if session.state == SessionState.STREAMING:
        return {"ok": False, "error": "이미 스트리밍 중입니다"}
    pipeline.stop_monitor()
    await pipeline.start_streaming()
    return {"ok": True}


@app.post("/api/live/pause")
async def pause_streaming():
    if session.state != SessionState.STREAMING:
        return {"ok": False, "error": "스트리밍 중이 아닙니다"}
    await pipeline.pause()
    return {"ok": True}


@app.post("/api/live/resume")
async def resume_streaming():
    if session.state != SessionState.PAUSED:
        return {"ok": False, "error": "일시정지 상태가 아닙니다"}
    await pipeline.resume()
    return {"ok": True}


@app.post("/api/live/stop")
async def stop_streaming():
    await pipeline.stop()
    return {"ok": True}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        session.listener_count = broadcaster.listener_count
        await ws.send_json({"type": "status", **session.to_status()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(ws)
        session.listener_count = broadcaster.listener_count


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
