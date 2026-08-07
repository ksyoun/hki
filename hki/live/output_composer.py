"""Batch recombine translations then pace caption+TTS release."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from hki import config
from hki.live.openai_client import get_async_openai

logger = logging.getLogger(__name__)

# batch_id, es, item_ids, ko_summary
OnRelease = Callable[[str, str, list[str], str], Awaitable[None]]

RECOMBINE_SYSTEM = """Eres editor de texto para subtítulos y TTS en iglesia argentina.
Recibes fragmentos YA TRADUCIDOS al español. Tu trabajo es unirlos en un texto natural
para leer en voz alta y mostrar como subtítulo.

Reglas estrictas:
- Usa ÚNICAMENTE las palabras e ideas de los fragmentos; NO inventes contenido nuevo
- NO agregues explicaciones, saludos, comentarios ni contexto que no esté en los fragmentos
- NO cambies el significado; no «mejores» el sermón
- Puedes: unir con conectores mínimos, quitar repeticiones obvias, puntuación para oralidad
- Mantener referencias bíblicas NVI exactas (Mateo 1:1)
- Solo JSON: {"text": "…"}"""


@dataclass
class FragmentItem:
    item_id: str
    ko: str
    es: str


@dataclass
class ReleaseItem:
    batch_id: str
    es: str
    item_ids: list[str]
    ko_summary: str


def _fallback_join(items: list[FragmentItem]) -> str:
    return " ".join(i.es.strip() for i in items if i.es.strip())


def _ko_summary(items: list[FragmentItem]) -> str:
    return " ".join(i.ko.strip() for i in items if i.ko.strip())


def _is_faithful(source: str, polished: str) -> bool:
    src = source.strip()
    pol = polished.strip()
    if not pol:
        return False
    if len(pol) > max(int(len(src) * 1.2), len(src) + 40):
        return False
    src_words = {w.lower() for w in re.findall(r"\w+", src, re.UNICODE) if len(w) > 3}
    if not src_words:
        return True
    pol_lower = pol.lower()
    overlap = sum(1 for w in src_words if w in pol_lower)
    return overlap >= len(src_words) * 0.5


def release_interval_ms(depth: int, base_ms: int | None = None, min_ms: int | None = None) -> int:
    """Adaptive pacing: slower when idle, faster when backlog grows."""
    base = base_ms if base_ms is not None else config.OUTPUT_RELEASE_BASE_MS
    floor = min_ms if min_ms is not None else config.OUTPUT_RELEASE_MIN_MS
    d = max(1, depth)
    return max(floor, int(base / math.sqrt(d)))


async def recombine_for_output(
    items: list[FragmentItem],
    *,
    context: dict | None = None,
    sermon_mode: bool = False,
) -> str:
    if not items:
        return ""
    joined = _fallback_join(items)
    if len(items) == 1:
        return joined

    numbered = "\n".join(
        f"{i + 1}. {it.es.strip()}" for i, it in enumerate(items)
    )
    user_content = f"Fragmentos (solo unir, sin inventar):\n{numbered}"
    system = RECOMBINE_SYSTEM
    if sermon_mode and context:
        try:
            from hki.live.context import format_context_for_system

            ctx_block = format_context_for_system(context)
            if ctx_block.strip():
                system = (
                    f"{RECOMBINE_SYSTEM}\n\n"
                    f"Contexto del sermón (solo para coherencia; no inventes):\n{ctx_block}"
                )
        except Exception:
            logger.debug("Context injection for recombine skipped", exc_info=True)

    model = config.OUTPUT_PREP_MODEL or config.FINAL_MODEL
    client = get_async_openai()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.05,
            max_tokens=800,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        text = str(data.get("text") or "").strip()
        if text and _is_faithful(joined, text):
            return text
        if text:
            logger.warning(
                "Recombine rejected (unfaithful) joined=%s polished=%s",
                joined[:80],
                text[:80],
            )
    except Exception as e:
        logger.error("Recombine LLM failed: %s", e)
    return joined


class OutputComposer:
    """Batch fragments → recombine → paced release for captions and TTS."""

    def __init__(self, on_release: OnRelease):
        self.on_release = on_release
        self._pending: list[FragmentItem] = []
        self._work_queue: asyncio.Queue[list[FragmentItem]] = asyncio.Queue()
        self._release_queue: asyncio.Queue[ReleaseItem] = asyncio.Queue()
        self._running = False
        self._recombine_in_flight = 0
        self._release_in_flight = 0
        self._timeout_task: asyncio.Task | None = None
        self._last_release_mono = 0.0
        self._batch_size = max(1, min(3, config.OUTPUT_BATCH_SIZE))
        self._timeout_sec = config.OUTPUT_TIMEOUT_MS / 1000.0
        self._context: dict | None = None
        self._sermon_mode = False
        self._fast_drain = False

    def set_context(self, context: dict | None) -> None:
        self._context = context

    def set_sermon_mode(self, sermon_mode: bool) -> None:
        self._sermon_mode = sermon_mode

    def pending_count(self) -> int:
        return (
            len(self._pending)
            + self._work_queue.qsize()
            + self._recombine_in_flight
            + self._release_queue.qsize()
            + self._release_in_flight
        )

    def release_queue_depth(self) -> int:
        return self._release_queue.qsize() + self._release_in_flight

    def _effective_depth(self) -> int:
        """Release queue + capped upstream batches for catch-up pacing."""
        upstream = self._work_queue.qsize() + self._recombine_in_flight
        pending_batches = (len(self._pending) + self._batch_size - 1) // self._batch_size
        return max(
            1,
            self._release_queue.qsize()
            + min(upstream + pending_batches, 2),
        )

    async def run(self) -> None:
        self._running = True
        await asyncio.gather(self._recombine_worker(), self._pacer_loop())

    def stop_sync(self) -> None:
        self._running = False
        self._cancel_timeout()
        self._pending.clear()
        self._fast_drain = False
        while not self._work_queue.empty():
            try:
                self._work_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._release_queue.empty():
            try:
                self._release_queue.get_nowait()
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
        self._timeout_task = asyncio.get_running_loop().create_task(
            self._timeout_flush()
        )

    async def _timeout_flush(self) -> None:
        try:
            await asyncio.sleep(self._timeout_sec)
            await self._enqueue_pending_batches(force_all=True)
        except asyncio.CancelledError:
            pass

    async def _enqueue_pending_batches(self, force_all: bool = False) -> None:
        self._cancel_timeout()
        while len(self._pending) >= self._batch_size:
            batch = self._pending[: self._batch_size]
            self._pending = self._pending[self._batch_size :]
            await self._work_queue.put(batch)
        if force_all and self._pending:
            batch = list(self._pending)
            self._pending.clear()
            await self._work_queue.put(batch)

    async def add(self, item_id: str, ko: str, es: str) -> None:
        text = es.strip()
        if not text:
            return
        self._pending.append(
            FragmentItem(item_id=item_id, ko=ko.strip(), es=text)
        )
        if len(self._pending) >= self._batch_size:
            await self._enqueue_pending_batches()
        else:
            self._arm_timeout()

    async def drain(self, timeout: float = 180.0) -> bool:
        """Flush pending, pace releases quickly, wait until empty."""
        self._fast_drain = True
        try:
            await self._enqueue_pending_batches(force_all=True)
            deadline = asyncio.get_running_loop().time() + timeout
            idle_ticks = 0
            while asyncio.get_running_loop().time() < deadline:
                if self.pending_count() == 0:
                    # Require brief idle so we don't return between recombine tasks
                    idle_ticks += 1
                    if idle_ticks >= 4:
                        return True
                else:
                    idle_ticks = 0
                await asyncio.sleep(0.05)
            logger.warning(
                "OutputComposer drain timeout (%d still pending)",
                self.pending_count(),
            )
            return False
        finally:
            self._fast_drain = False

    async def _recombine_worker(self) -> None:
        while self._running:
            try:
                batch = await asyncio.wait_for(self._work_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            self._recombine_in_flight += 1
            try:
                text = await recombine_for_output(
                    batch,
                    context=self._context,
                    sermon_mode=self._sermon_mode,
                )
                if not text.strip():
                    continue
                item_ids = [it.item_id for it in batch]
                await self._release_queue.put(
                    ReleaseItem(
                        batch_id=item_ids[0],
                        es=text.strip(),
                        item_ids=item_ids,
                        ko_summary=_ko_summary(batch),
                    )
                )
            except Exception as e:
                logger.error("OutputComposer recombine worker error: %s", e)
                fallback = _fallback_join(batch)
                if fallback.strip():
                    item_ids = [it.item_id for it in batch]
                    await self._release_queue.put(
                        ReleaseItem(
                            batch_id=item_ids[0],
                            es=fallback.strip(),
                            item_ids=item_ids,
                            ko_summary=_ko_summary(batch),
                        )
                    )
            finally:
                self._recombine_in_flight -= 1

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
                depth = self._effective_depth()
                # Include the item we just took (+1) for interval calc
                wait_ms = release_interval_ms(depth + 1)
                elapsed = time.monotonic() - self._last_release_mono
                wait_sec = max(0.0, wait_ms / 1000.0 - elapsed)

            if wait_sec > 0:
                await asyncio.sleep(wait_sec)

            self._release_in_flight += 1
            try:
                await self.on_release(
                    item.batch_id, item.es, item.item_ids, item.ko_summary
                )
                self._last_release_mono = time.monotonic()
            except Exception as e:
                logger.error("OutputComposer release error: %s", e)
            finally:
                self._release_in_flight -= 1
