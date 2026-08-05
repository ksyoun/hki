"""FastAPI server — REST API, WebSocket, static UI."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hki import config
from hki.live.audio import find_scarlett, list_devices, resolve_input_device
from hki.live.broadcaster import Broadcaster
from hki.live.file_replay import load_audio_file
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

UPLOAD_DIR = config.BASE_DIR / ".hki_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

_test_pcm: bytes | None = None
_test_duration: float = 0.0
_test_filename: str = ""


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


async def _restart_input_monitor_async() -> None:
    """Resume live input level monitoring when idle."""
    if session.state in (SessionState.STREAMING, SessionState.PAUSED):
        return
    pipeline.stop_monitor()
    try:
        await pipeline.ensure_input_monitor()
    except ValueError as e:
        logger.info("Input monitor skipped: %s", e)


@app.get("/")
async def control_page():
    return FileResponse(STATIC_DIR / "control.html")


@app.get("/captions")
async def captions_page():
    return FileResponse(STATIC_DIR / "captions.html")


@app.get("/log")
async def log_page():
    return FileResponse(STATIC_DIR / "log.html")


@app.get("/latency")
async def latency_page():
    return FileResponse(STATIC_DIR / "latency.html")


@app.get("/api/live/log")
async def live_log():
    return session.to_log()


@app.get("/api/live/latency")
async def live_latency():
    if session.latency_report is None:
        return {"ok": False, "error": "No hay informe de latencia"}
    return {"ok": True, **session.latency_report}


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
    if session.state in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "No se puede cambiar la configuración durante la transmisión"}

    pipeline.stop_monitor()
    try:
        resolved = resolve_input_device(cfg.device_index)
        session.device_index = resolved.index
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    session.configure(
        bible_text=cfg.bible_text,
        manuscript=cfg.manuscript,
        device_index=session.device_index,
        gain=cfg.gain,
    )
    try:
        await _restart_input_monitor_async()
    except Exception as e:
        logger.exception("Failed to restart input monitor")
        return {"ok": False, "error": f"Error al iniciar entrada de audio: {e}"}
    return {"ok": True}


@app.post("/api/live/monitor/start")
async def monitor_start():
    if session.state == SessionState.STREAMING:
        return {"ok": False, "error": "No se puede iniciar el monitor durante la transmisión"}
    try:
        await pipeline.ensure_input_monitor()
    except Exception as e:
        return {"ok": False, "error": f"Error al iniciar entrada de audio: {e}"}
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
        return {"ok": False, "error": "La transmisión ya está en curso"}
    if session.test_mode:
        return {"ok": False, "error": "No se puede iniciar durante la prueba de audio"}
    pipeline.stop_monitor()
    await pipeline.start_streaming()
    return {"ok": True}


@app.post("/api/live/pause")
async def pause_streaming():
    if session.state != SessionState.STREAMING:
        return {"ok": False, "error": "No hay transmisión en curso"}
    await pipeline.pause()
    return {"ok": True}


@app.post("/api/live/resume")
async def resume_streaming():
    if session.state != SessionState.PAUSED:
        return {"ok": False, "error": "No está en pausa"}
    await pipeline.resume()
    return {"ok": True}


@app.post("/api/live/stop")
async def stop_streaming():
    await pipeline.stop()
    try:
        await _restart_input_monitor_async()
    except Exception as e:
        logger.warning("Monitor restart after stop failed: %s", e)
    return {"ok": True}


@app.post("/api/live/test/upload")
async def test_upload(file: UploadFile = File(...)):
    global _test_pcm, _test_duration, _test_filename

    if not file.filename:
        return {"ok": False, "error": "No hay archivo"}

    suffix = Path(file.filename).suffix.lower() or ".wav"
    dest = UPLOAD_DIR / f"test_audio{suffix}"
    data = await file.read()
    dest.write_bytes(data)

    try:
        pcm, duration = load_audio_file(dest)
    except ValueError as e:
        dest.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.exception("Test upload failed")
        return {"ok": False, "error": f"Error al procesar audio: {e}"}

    _test_pcm = pcm
    _test_duration = duration
    _test_filename = file.filename
    return {
        "ok": True,
        "filename": file.filename,
        "duration_sec": duration,
    }


@app.get("/api/live/test/status")
async def test_status():
    return {
        "ok": True,
        "has_file": _test_pcm is not None,
        "filename": _test_filename,
        "duration_sec": _test_duration,
        "test_mode": session.test_mode,
        "playback_sec": session.test_playback_sec,
        "state": session.state.value,
    }


@app.post("/api/live/test/play")
async def test_play():
    global _test_pcm, _test_duration, _test_filename

    if _test_pcm is None:
        return {"ok": False, "error": "Primero adjunte un archivo de audio"}
    if session.state in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "Ya hay una transmisión o prueba en curso"}

    pipeline.stop_monitor()
    await pipeline.start_test_streaming(
        _test_pcm, _test_duration, _test_filename
    )
    return {
        "ok": True,
        "duration_sec": _test_duration,
        "filename": _test_filename,
    }


@app.post("/api/live/test/stop")
async def test_stop():
    if session.test_mode or session.state in (
        SessionState.STREAMING,
        SessionState.PAUSED,
    ):
        await pipeline.stop()
    try:
        await _restart_input_monitor_async()
    except Exception as e:
        logger.warning("Monitor restart after test stop failed: %s", e)
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
