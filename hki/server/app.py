"""FastAPI server — REST API, WebSocket, static UI."""

from __future__ import annotations

import json
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hki import config
from hki.asyncio_compat import install_benign_connection_reset_filter
from hki.live.audio import list_devices, resolve_input_device
from hki.live.bible_api import close_http_client
from hki.live.broadcaster import Broadcaster
from hki.live.file_replay import load_audio_file
from hki.live.context import build_translation_context, format_context_display
from hki.live.openai_client import close_async_openai
from hki.live.pipeline import LivePipeline
from hki.live.session import LiveSession, SessionState
from hki.server.cors import add_lan_cors

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    install_benign_connection_reset_filter()
    try:
        await _restart_input_monitor_async()
    except Exception:
        logger.exception("Input monitor failed on startup")
    yield
    pipeline.stop_monitor()
    await close_http_client()
    await close_async_openai()


app = FastAPI(title="HKI Live Translation", lifespan=_lifespan)
add_lan_cors(app)
broadcaster = Broadcaster()
session = LiveSession()
pipeline = LivePipeline(session, broadcaster)

UPLOAD_DIR = config.BASE_DIR / ".hki_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

_test_pcm: bytes | None = None
_test_duration: float = 0.0
_test_filename: str = ""
_speaker_subscribed: dict[WebSocket, bool] = {}


class SessionConfig(BaseModel):
    device_index: int | None = None
    gain: float | None = None


class ContextualizarBody(BaseModel):
    bible_text: str
    manuscript: str = ""


class GainUpdate(BaseModel):
    gain: float


def _sync_audience_count() -> None:
    session.audience_count = broadcaster.audience_count


def _live_runtime_status() -> dict:
    """Session + pipeline fields for REST and post-action responses."""
    _sync_audience_count()
    return {
        **session.build_live_status(config.TTS_ENABLED),
        **pipeline.get_gate_status(),
        **pipeline.get_translation_prompt_info(),
        **pipeline.get_input_level_metrics(),
    }


async def _broadcast_status() -> None:
    try:
        await pipeline.broadcast_status()
    except Exception:
        logger.exception("broadcast_status failed — sending minimal status")
        try:
            _sync_audience_count()
            await broadcaster.broadcast(
                {
                    "type": "status",
                    **session.to_status(),
                    **pipeline.get_gate_status(),
                    "tts_available": config.TTS_ENABLED,
                    "min_audience_count": config.MIN_AUDIENCE_COUNT,
                    "tts_active": config.TTS_ENABLED and session.speaker_subscribers > 0,
                }
            )
        except Exception:
            logger.exception("minimal status broadcast also failed")


def _recount_speaker_subscribers() -> None:
    count = sum(
        1
        for ws, on in _speaker_subscribed.items()
        if on and broadcaster.client_role(ws) == "audience"
    )
    session.set_speaker_subscribers(count)


async def _handle_ws_message(ws: WebSocket, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if msg.get("type") != "speaker_subscribe":
        return
    if broadcaster.client_role(ws) != "audience":
        return
    if not config.TTS_ENABLED:
        return
    enabled = bool(msg.get("enabled"))
    was = _speaker_subscribed.get(ws, False)
    if enabled == was:
        return
    _speaker_subscribed[ws] = enabled
    _recount_speaker_subscribers()
    await _broadcast_status()


def _unregister_speaker_subscriber(ws: WebSocket) -> None:
    if _speaker_subscribed.pop(ws, False):
        _recount_speaker_subscribers()


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


@app.get("/api/health")
async def health():
    """Lightweight probe for LAN connectivity (no auth)."""
    return {
        "ok": True,
        "service": "hki",
        "scheme": config.public_scheme(),
        "port": config.PORT,
    }


@app.get("/")
async def control_page():
    return FileResponse(STATIC_DIR / "control.html")


@app.get("/join")
async def join_page(request: Request):
    if config.is_https():
        host = request.headers.get("host", "").split(":")[0] or _local_ip()
        return RedirectResponse(config.audience_join_url(host), status_code=302)
    return FileResponse(STATIC_DIR / "join.html")


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
    return {
        "devices": [
            {"index": d.index, "name": d.name, "sample_rate": d.sample_rate}
            for d in devices
        ],
    }


@app.get("/api/live/status")
async def live_status():
    ip = _local_ip()
    join = config.audience_join_url(ip)
    return {
        **_live_runtime_status(),
        "local_ip": ip,
        "port": config.PORT,
        "http_guide_port": config.HTTP_GUIDE_PORT,
        "scheme": config.public_scheme(),
        "join_url": join,
        "captions_direct_url": config.captions_public_url(ip),
        # Alias for older clients — same as join_url (guide), not /captions
        "captions_url": join,
    }


@app.post("/api/live/session")
async def configure_session(cfg: SessionConfig):
    """Restart input monitor using the OS default device (legacy API)."""
    if session.state in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "No se puede cambiar la configuración durante la transmisión"}

    pipeline.stop_monitor()
    try:
        resolved = resolve_input_device(None)
        session.device_index = resolved.index
        session.input_device_name = resolved.name
        session.configure(gain=config.GAIN_DEFAULT)
        await _restart_input_monitor_async()
    except Exception as e:
        logger.exception("Failed to restart input monitor")
        return {"ok": False, "error": f"Error al iniciar entrada de audio: {e}"}
    return {"ok": True}


@app.patch("/api/live/gain")
async def update_gain(body: GainUpdate):
    pipeline.set_gain(body.gain)
    return {"ok": True, "gain": session.gain}


@app.post("/api/live/contextualizar")
async def contextualizar_content(body: ContextualizarBody):
    if session.context_ready:
        return {
            "ok": False,
            "error": "El contexto ya está bloqueado. Use Liberar contexto para volver a contextualizar",
        }

    is_live = session.state in (SessionState.STREAMING, SessionState.PAUSED)

    bible = body.bible_text.strip()
    manuscript = body.manuscript.strip()
    if not bible:
        return {"ok": False, "error": "El texto bíblico es obligatorio"}

    try:
        context, passage_display, warnings = await build_translation_context(
            bible, manuscript
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("Contextualizar failed")
        return {"ok": False, "error": f"Error al generar contexto: {e}"}

    session.set_translation_context(manuscript, context, passage_display)
    pipeline.apply_translation_context()
    await _broadcast_status()

    result = {
        "ok": True,
        "context_ready": True,
        "generated_at": context.get("generated_at"),
        "passage_display": passage_display,
        "context_display": format_context_display(context),
        "warnings": warnings,
        "context_applied_live": is_live,
    }
    if warnings:
        result["warning"] = "; ".join(warnings)
    return result


@app.post("/api/live/reset-context")
async def reset_context():
    """Clear translation context so the operator can contextualize again without restarting."""
    if not session.context_ready:
        return {"ok": True, "context_ready": False}

    session.clear_translation_context()
    pipeline.apply_translation_context()
    await _broadcast_status()
    return {"ok": True, "context_ready": False}


@app.post("/api/live/start")
async def start_streaming():
    if session.state == SessionState.STREAMING:
        return {"ok": False, "error": "La transmisión ya está en curso"}
    if session.test_mode:
        return {"ok": False, "error": "No se puede iniciar durante la prueba de audio"}

    warning = None
    if not session.context_ready:
        warning = (
            "No hay contexto de traducción. "
            "La transmisión iniciará sin contexto del sermón."
        )

    pipeline.stop_monitor()
    try:
        await pipeline.start_streaming()
    except ValueError as e:
        logger.warning("Start streaming failed: %s", e)
        try:
            await _restart_input_monitor_async()
        except Exception:
            logger.warning("Monitor restart after failed start failed", exc_info=True)
        msg = str(e)
        if "dispositivo" in msg.lower():
            return {"ok": False, "error": "No hay dispositivo de entrada de audio disponible"}
        return {"ok": False, "error": msg}
    except Exception as e:
        logger.exception("Start streaming failed")
        try:
            await _restart_input_monitor_async()
        except Exception:
            logger.warning("Monitor restart after failed start failed", exc_info=True)
        return {"ok": False, "error": f"Error al iniciar transmisión: {e}"}
    out: dict = {"ok": True, **_live_runtime_status()}
    if warning:
        out["warning"] = warning
    return out


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


@app.post("/api/live/sermon-on")
async def sermon_on():
    if session.state not in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "No hay transmisión en curso"}
    await pipeline.set_sermon_mode(True)
    return {"ok": True, "sermon_on": True, **pipeline.get_translation_prompt_info()}


@app.post("/api/live/sermon-off")
async def sermon_off():
    if session.state not in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "No hay transmisión en curso"}
    await pipeline.set_sermon_mode(False)
    return {"ok": True, "sermon_on": False, **pipeline.get_translation_prompt_info()}


@app.post("/api/live/stop")
async def stop_streaming():
    await pipeline.stop()
    return {"ok": True, **_live_runtime_status()}


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
        **_live_runtime_status(),
    }


@app.post("/api/live/test/stop")
async def test_stop():
    if session.test_mode or session.state in (
        SessionState.STREAMING,
        SessionState.PAUSED,
    ):
        # pipeline.stop() already restarts input monitor
        await pipeline.stop()
    elif session.state != SessionState.MONITORING:
        try:
            await _restart_input_monitor_async()
        except Exception as e:
            logger.warning("Monitor restart after test stop failed: %s", e)
    return {"ok": True, **_live_runtime_status()}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket, role: str = "operator"):
    if role not in ("audience", "operator"):
        role = "operator"
    await broadcaster.connect(ws, role=role)
    _speaker_subscribed[ws] = False
    try:
        await _broadcast_status()
        while True:
            raw = await ws.receive_text()
            await _handle_ws_message(ws, raw)
    except WebSocketDisconnect:
        pass
    finally:
        _unregister_speaker_subscriber(ws)
        await broadcaster.disconnect(ws)
        await _broadcast_status()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
