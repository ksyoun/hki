"""Adaptive pacing for caption + TTS release."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from hki import config

logger = logging.getLogger(__name__)

OnRelease = Callable[["ReleaseItem"], Awaitable[None]]


@dataclass
class ReleaseItem:
    batch_id: str
    es: str
    item_ids: list[str]
    ko_summary: str
    recombine_flags: list[str] = field(default_factory=list)
    repair_rejected: bool = False
    anchor_repair: bool = False
    had_incierto: bool = False
    ko_corrected: str = ""
    joined_preview: str = ""
    stt_repair: bool = False
    latency_recombine: int = 0
    release_reason: str = ""
    consume: int = 0
    should_wait: bool | None = None
    grace_ms: int = 0
    b_arrived: bool | None = None
    b_delta_ms: int | None = None
    released_as_single: bool = False
    single_kind: str = ""
    release_latency_ms: int = 0
    translated_at_mono: float = 0.0
    first_fragment_at_mono: float = 0.0
    last_fragment_at_mono: float = 0.0
    fragment_count: int = 0
    unit_index: int = 0
    unit_count: int = 0
    fragment_indexes: list[int] = field(default_factory=list)
    original_stt: str = ""
    latency_translate: int = 0
    latency_first_fragment_to_caption: int = 0
    latency_last_fragment_to_caption: int = 0
    latency_release_to_caption: int = 0
    mapping_fallback: bool = False
    recombine_id: str = ""


def release_interval_ms(
    depth: int,
    base_ms: int | None = None,
    min_ms: int | None = None,
) -> int:
    """Adaptive pacing: slower when idle, faster when backlog grows."""
    base = base_ms if base_ms is not None else config.OUTPUT_RELEASE_BASE_MS
    floor = min_ms if min_ms is not None else config.OUTPUT_RELEASE_MIN_MS
    d = max(1, depth)
    return max(floor, int(base / math.sqrt(d)))


class ReleasePacer:
    def __init__(
        self,
        on_release: OnRelease,
        depth_fn: Callable[[], int] | None = None,
    ):
        self.on_release = on_release
        self._depth_fn = depth_fn or (lambda: 1)
        self._release_queue: asyncio.Queue[ReleaseItem] = asyncio.Queue()
        self._running = False
        self._release_in_flight = 0
        self._last_release_mono = 0.0
        self._fast_drain = False

    def release_queue_depth(self) -> int:
        return self._release_queue.qsize() + self._release_in_flight

    def pending_count(self) -> int:
        return self.release_queue_depth()

    def set_fast_drain(self, fast: bool) -> None:
        self._fast_drain = fast

    async def enqueue(self, item: ReleaseItem) -> None:
        await self._release_queue.put(item)

    def pop_remaining(self) -> list[ReleaseItem]:
        items: list[ReleaseItem] = []
        while not self._release_queue.empty():
            try:
                items.append(self._release_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def stop_sync(self) -> None:
        self._running = False
        self._fast_drain = False
        self.pop_remaining()

    async def drain(self, timeout: float = 180.0) -> bool:
        self._fast_drain = True
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            idle_ticks = 0
            while asyncio.get_running_loop().time() < deadline:
                if self.pending_count() == 0:
                    idle_ticks += 1
                    if idle_ticks >= 4:
                        return True
                else:
                    idle_ticks = 0
                await asyncio.sleep(0.05)
            logger.warning(
                "ReleasePacer drain timeout (%d still pending)",
                self.pending_count(),
            )
            return False
        finally:
            self._fast_drain = False

    async def run(self) -> None:
        self._running = True
        await self._pacer_loop()

    async def _pacer_loop(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(
                    self._release_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            if self._fast_drain:
                wait_sec = config.OUTPUT_RELEASE_MIN_MS / 1000.0
            else:
                depth = self._depth_fn()
                wait_ms = release_interval_ms(depth)
                elapsed = time.monotonic() - self._last_release_mono
                wait_sec = max(0.0, wait_ms / 1000.0 - elapsed)

            if wait_sec > 0:
                await asyncio.sleep(wait_sec)

            self._release_in_flight += 1
            try:
                await self.on_release(item)
                self._last_release_mono = time.monotonic()
            except Exception as e:
                logger.error("ReleasePacer release error: %s", e)
            finally:
                self._release_in_flight -= 1
