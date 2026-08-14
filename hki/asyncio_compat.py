"""Windows asyncio quirks — suppress benign connection reset noise."""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

_BENIGN_WINERRORS = frozenset({10053, 10054, 10058})
_PATCHED_LOOPS: set[int] = set()


def _is_benign_connection_lost(context: dict) -> bool:
    message = context.get("message", "")
    if "_call_connection_lost" not in message:
        return False
    exc = context.get("exception")
    if exc is None:
        return True
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    return getattr(exc, "winerror", None) in _BENIGN_WINERRORS


def install_benign_connection_reset_filter() -> None:
    """Silence harmless Windows Proactor connection-lost callback errors."""
    if sys.platform != "win32":
        return

    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    if loop_id in _PATCHED_LOOPS:
        return

    previous = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        if _is_benign_connection_lost(context):
            logger.debug("Ignored benign connection close: %s", context.get("message"))
            return
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    _PATCHED_LOOPS.add(loop_id)
