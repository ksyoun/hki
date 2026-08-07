"""HTTP-only guide server — cert instructions without TLS warning (HTTPS on main port)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from hki import config

STATIC_DIR = Path(__file__).parent / "static"

guide_app = FastAPI(title="HKI Guide", docs_url=None, redoc_url=None)


@guide_app.get("/")
async def guide_root():
    port_q = f"?p={config.PORT}" if config.PORT != 8765 else ""
    return RedirectResponse(url=f"/join{port_q}")


@guide_app.get("/join")
async def guide_join():
    return FileResponse(STATIC_DIR / "join.html")

