"""WebSocket broadcaster for LAN clients."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._roles: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    @property
    def audience_count(self) -> int:
        return sum(1 for role in self._roles.values() if role == "audience")

    def client_role(self, ws: WebSocket) -> str | None:
        return self._roles.get(ws)

    async def connect(self, ws: WebSocket, role: str = "operator") -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
            self._roles[ws] = role
        logger.info(
            "Client connected (role=%s, total=%d, audience=%d)",
            role,
            len(self._clients),
            self.audience_count,
        )

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            self._roles.pop(ws, None)
        logger.info(
            "Client disconnected (total=%d, audience=%d)",
            len(self._clients),
            self.audience_count,
        )

    async def broadcast(self, event: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
                    self._roles.pop(ws, None)
