"""HTTP-only guide server — cert instructions without TLS warning (HTTPS on main port)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from hki import config
from hki.asyncio_compat import install_benign_connection_reset_filter
from hki.server.cors import add_lan_cors

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _guide_lifespan(_app: FastAPI):
    install_benign_connection_reset_filter()
    yield


guide_app = FastAPI(
    title="HKI Guide",
    docs_url=None,
    redoc_url=None,
    lifespan=_guide_lifespan,
)
add_lan_cors(guide_app)


@guide_app.get("/api/health")
async def guide_health():
    return {"ok": True, "service": "hki-guide", "port": config.HTTP_GUIDE_PORT}


@guide_app.get("/")
async def guide_root():
    port_q = f"?p={config.PORT}" if config.PORT != 8765 else ""
    return RedirectResponse(url=f"/join{port_q}")


@guide_app.get("/join")
async def guide_join():
    return FileResponse(STATIC_DIR / "join.html")

