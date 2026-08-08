"""Shared CORS settings for LAN clients (phones, other PCs on the subnet)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Allow any host on the local network (http/https, with optional port).
_LAN_ORIGIN_REGEX = r"https?://[\w.\-]+(:\d+)?"


def add_lan_cors(app: FastAPI) -> None:
    """Permit cross-origin API/WebSocket from other devices on the LAN."""
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_LAN_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
