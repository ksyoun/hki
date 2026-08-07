"""Batch oralización LLM before TTS — natural spoken Spanish."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from hki import config
from hki.live.openai_client import get_async_openai

logger = logging.getLogger(__name__)

OnBatchReady = Callable[[str, str, list[str]], Awaitable[None]]

ORALIZE_SYSTEM = """Eres editor de texto para voz (TTS) en iglesia argentina (rioplatense).
Recibes 1–3 fragmentos de traducción al español y devuelves UN solo texto para leer en voz alta.

Reglas:
- Unir en flujo natural de oralidad, voseo rioplatense cuando aplique al sermón
- No agregar ni quitar significado; no explicar
- Mantener referencias bíblicas NVI (Mateo 1:1)
- Frases claras para TTS; evitar símbolos raros
- Solo JSON: {"text": "…"}"""


@dataclass
class PrepItem:
    item_id: str
    es: str


def _fallback_join(items: list[PrepItem]) -> str:
    return " ".join(i.es.strip() for i in items if i.es.strip())


async def oralize_for_speech(items: list[PrepItem]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0].es.strip()

    numbered = "\n".join(f"{i + 1}. {it.es.strip()}" for i, it in enumerate(items))
    model = config.TTS_PREP_MODEL or config.FINAL_MODEL
    client = get_async_openai()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ORALIZE_SYSTEM},
                {"role": "user", "content": f"Fragmentos:\n{numbered}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.15,
            max_tokens=800,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        text = str(data.get("text") or "").strip()
        if text:
            return text
    except Exception as e:
        logger.error("Oralize LLM failed: %s", e)
    return _fallback_join(items)


class TTSPrepBuffer:
    """Collect translations, oralize in batches, never drop items while active."""

    def __init__(self, on_batch_ready: OnBatchReady):
        self.on_batch_ready = on_batch_ready
        self._pending: list[PrepItem] = []
        self._work_queue: asyncio.Queue[list[PrepItem]] = asyncio.Queue()
        self._running = False
        self._in_flight = 0
        self._timeout_task: asyncio.Task | None = None
        self._batch_size = max(1, min(3, config.TTS_PREP_BATCH_SIZE))

        self._timeout_sec = config.TTS_PREP_TIMEOUT_MS / 1000.0

    def pending_count(self) -> int:
        return len(self._pending) + self._work_queue.qsize() + self._in_flight

    async def run(self) -> None:
        self._running = True
        await self._worker()

    def stop_sync(self) -> None:
        self._running = False
        self._cancel_timeout()
        self._pending.clear()
        while not self._work_queue.empty():
            try:
                self._work_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _cancel_timeout(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    def _arm_timeout(self) -> None:
        if not self._pending:
            return
        self._cancel_timeout()
        self._timeout_task = asyncio.get_running_loop().create_task(self._timeout_flush())

    async def _timeout_flush(self) -> None:
        try:
            await asyncio.sleep(self._timeout_sec)
            await self._enqueue_pending_batches(force_all=True)
        except asyncio.CancelledError:
            pass

    async def _enqueue_pending_batches(self, force_all: bool = False) -> None:
        self._cancel_timeout()
        while len(self._pending) >= self._batch_size:
            batch = self._pending[:self._batch_size]
            self._pending = self._pending[self._batch_size:]
            await self._work_queue.put(batch)
        if force_all and self._pending:
            batch = list(self._pending)
            self._pending.clear()
            await self._work_queue.put(batch)

    async def add(self, item_id: str, es: str) -> None:
        text = es.strip()
        if not text:
            return
        self._pending.append(PrepItem(item_id=item_id, es=text))
        if len(self._pending) >= self._batch_size:
            await self._enqueue_pending_batches()
        else:
            self._arm_timeout()

    async def drain(self, timeout: float = 180.0) -> bool:
        """Flush all pending and wait for oralize + downstream TTS handoff."""
        await self._enqueue_pending_batches(force_all=True)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.pending_count() == 0:
                return True
            await asyncio.sleep(0.05)
        logger.warning("TTS prep drain timeout (%d still pending)", self.pending_count())
        return False

    async def _worker(self) -> None:
        while self._running:
            try:
                batch = await asyncio.wait_for(self._work_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            self._in_flight += 1
            try:
                text = await oralize_for_speech(batch)
                if not text.strip():
                    continue
                item_ids = [it.item_id for it in batch]
                batch_id = item_ids[0]
                await self.on_batch_ready(batch_id, text.strip(), item_ids)
            except Exception as e:
                logger.error("TTS prep worker error: %s", e)
                fallback = _fallback_join(batch)
                if fallback.strip():
                    item_ids = [it.item_id for it in batch]
                    await self.on_batch_ready(item_ids[0], fallback.strip(), item_ids)
            finally:
                self._in_flight -= 1
