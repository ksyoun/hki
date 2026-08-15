"""KO fragment queue → LLM hold/release + single-line ES → ReleasePacer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from hki import config
from hki.live.context import normalize_ko_stt
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.output_composer import FragmentItem, _ko_summary_for_anchor
from hki.live.release_pacer import ReleaseItem, ReleasePacer
from hki.live.sentence_prompts import (
    build_sentence_system_prompt,
    build_sentence_user_message,
    describe_sentence_prompt,
)
from hki.live.translate import INCIERTO_MARKER, TRANSLATION_API_TIMEOUT_SEC

logger = logging.getLogger(__name__)

OnRelease = Callable[[ReleaseItem], Awaitable[None]]
OnUsage = Callable[[int, int], None]


@dataclass
class PendingFragment:
    item_id: str
    ko: str


def _strip_incierto_markers(text: str) -> str:
    cleaned = re.sub(r"\s*\[INCIERTO\]\s*", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


class KoSentenceTranslator:
    def __init__(
        self,
        on_release: OnRelease,
        context: dict | None = None,
        sermon_mode: bool = False,
        on_usage: OnUsage | None = None,
    ):
        self._context = context
        self._sermon_mode = sermon_mode
        self._on_usage = on_usage
        self._history: list[dict] = []
        self._pending: list[PendingFragment] = []
        self._final_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._running = False
        self._in_flight = 0
        self._evaluate_lock = asyncio.Lock()
        self._timeout_task: asyncio.Task | None = None
        self._hold_timeout_sec = config.SENTENCE_HOLD_TIMEOUT_MS / 1000.0
        self._max_pending = config.SENTENCE_MAX_PENDING
        self._pacer = ReleasePacer(on_release, depth_fn=self._pacer_depth)

    def _pacer_depth(self) -> int:
        return max(1, len(self._pending) + self._pacer.release_queue_depth())

    def pending_count(self) -> int:
        return (
            len(self._pending)
            + self._final_queue.qsize()
            + self._in_flight
            + self._pacer.pending_count()
        )

    def release_queue_depth(self) -> int:
        return self._pacer.release_queue_depth()

    def upstream_pending_count(self) -> int:
        return len(self._pending) + self._final_queue.qsize() + self._in_flight

    def describe_prompt(self) -> dict:
        ctx = self._context if self._sermon_mode else None
        return describe_sentence_prompt(self._sermon_mode, ctx)

    def set_sermon_mode(self, sermon_mode: bool) -> None:
        if self._sermon_mode == sermon_mode:
            return
        self._sermon_mode = sermon_mode
        self._history.clear()

    def set_context(self, context: dict | None) -> None:
        self._context = context

    def _system_prompt(self) -> str:
        ctx = self._context if self._sermon_mode else None
        return build_sentence_system_prompt(self._sermon_mode, ctx)

    def _record_usage(self, response) -> None:
        if not self._on_usage:
            return
        prompt, completion = usage_from_response(response)
        if prompt or completion:
            self._on_usage(prompt, completion)

    def _cancel_timeout(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    def _arm_timeout(self) -> None:
        self._cancel_timeout()
        if not self._running or not self._pending:
            return
        self._timeout_task = asyncio.get_running_loop().create_task(
            self._hold_timeout()
        )

    async def _hold_timeout(self) -> None:
        try:
            await asyncio.sleep(self._hold_timeout_sec)
            if self._pending and self._running:
                await self._evaluate_pending(force_release=True)
        except asyncio.CancelledError:
            pass

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        await self._final_queue.put((item_id, ko_text))

    async def drain(self, timeout: float = 180.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        attempts = 0
        while asyncio.get_running_loop().time() < deadline:
            queue_busy = self._final_queue.qsize() > 0 or self._in_flight > 0
            if self._pending:
                await self._evaluate_pending(force_release=True)
                attempts += 1
                if self._pending:
                    if attempts >= 3:
                        break
                    await asyncio.sleep(0.25)
            elif not queue_busy:
                break
            else:
                await asyncio.sleep(0.05)
        if self._pending:
            logger.warning(
                "Sentence drain still has %d pending after retries",
                len(self._pending),
            )
        return await self._pacer.drain(
            timeout=max(0.1, deadline - asyncio.get_running_loop().time())
        )

    async def run(self) -> None:
        self._running = True
        await asyncio.gather(self._final_worker(), self._pacer.run())

    def stop_sync(self) -> None:
        self._running = False
        self._cancel_timeout()
        if self._pending:
            logger.warning(
                "Sentence stop with %d pending still held: %s",
                len(self._pending),
                " | ".join(f.ko[:40] for f in self._pending),
            )
        self._pending.clear()
        self._pacer.stop_sync()
        while not self._final_queue.empty():
            try:
                self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _final_worker(self) -> None:
        while self._running:
            try:
                item_id, ko_text = await asyncio.wait_for(
                    self._final_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            if not ko_text.strip():
                continue
            self._pending.append(PendingFragment(item_id=item_id, ko=ko_text))
            if len(self._pending) >= self._max_pending:
                await self._evaluate_pending(force_release=True)
            else:
                await self._evaluate_pending()

    async def _evaluate_pending(self, force_release: bool = False) -> None:
        if not self._pending:
            return
        async with self._evaluate_lock:
            self._in_flight += 1
            try:
                while self._pending:
                    before = len(self._pending)
                    await self._call_evaluate(force_release=force_release)
                    force_release = False
                    if len(self._pending) >= before:
                        break
            finally:
                self._in_flight -= 1

    def _normalized_pending(self) -> list[PendingFragment]:
        out: list[PendingFragment] = []
        for frag in self._pending:
            ko = frag.ko
            if self._sermon_mode and self._context:
                ko = normalize_ko_stt(ko, self._context)
            out.append(PendingFragment(item_id=frag.item_id, ko=ko))
        return out

    async def _call_evaluate(self, force_release: bool = False) -> None:
        normalized = self._normalized_pending()
        fragment_pairs = [(f.item_id, f.ko) for f in normalized]
        user_msg = build_sentence_user_message(
            fragment_pairs,
            self._history,
            force_release=force_release,
        )
        try:
            response = await asyncio.wait_for(
                get_async_openai().chat.completions.create(
                    model=config.FINAL_MODEL,
                    messages=[
                        {"role": "system", "content": self._system_prompt()},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    **chat_completion_extra(
                        config.FINAL_MODEL,
                        800,
                        reasoning="none",
                        temperature=config.FINAL_TEMPERATURE,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            raw = response.choices[0].message.content or "{}"
            self._record_usage(response)
            data = json.loads(raw)
        except asyncio.TimeoutError:
            logger.error("Sentence evaluate timeout pending=%d", len(self._pending))
            if force_release and self._pending:
                await self._force_release_all()
            else:
                self._arm_timeout()
            return
        except Exception as e:
            logger.error("Sentence evaluate error: %s", e)
            if force_release and self._pending:
                await self._force_release_all()
            else:
                self._arm_timeout()
            return

        action = str(data.get("action") or "").lower()
        through_index = int(data.get("through_index") or 0)
        es = str(data.get("es") or "").strip()
        flags = [str(f) for f in (data.get("flags") or [])]

        if action == "release" and through_index > 0 and _strip_incierto_markers(es):
            await self._release_through(through_index, es, flags)
            return
        if force_release:
            await self._force_release_all()
            return
        self._arm_timeout()

    async def _release_through(
        self,
        through_index: int,
        es: str,
        flags: list[str],
    ) -> bool:
        count = min(through_index, len(self._pending))
        if count <= 0:
            return False
        batch = self._pending[:count]
        item_ids = [f.item_id for f in batch]
        ko_summary = _ko_summary_for_anchor(
            [FragmentItem(f.item_id, f.ko, "") for f in batch],
            self._context if self._sermon_mode else None,
        )
        es_clean = _strip_incierto_markers(es)
        if not es_clean:
            return False

        had_incierto = INCIERTO_MARKER.lower() in es.lower()
        anchor_repair = "anchor_repair" in flags or bool(flags)
        self._pending = self._pending[count:]
        self._cancel_timeout()
        self._history.append({"ko": ko_summary, "es": es_clean})
        if len(self._history) > max(2, config.FINAL_HISTORY_LINES * 2):
            self._history = self._history[-(config.FINAL_HISTORY_LINES * 2) :]

        await self._pacer.enqueue(
            ReleaseItem(
                batch_id=item_ids[0],
                es=es_clean,
                item_ids=item_ids,
                ko_summary=ko_summary,
                recombine_flags=flags,
                anchor_repair=anchor_repair,
                had_incierto=had_incierto,
            )
        )
        return True

    async def _fallback_translate(self, ko_text: str) -> str:
        """Plain KO→ES when hold/release JSON did not yield a line."""
        ko_text = ko_text.strip()
        if not ko_text:
            return ""
        try:
            response = await asyncio.wait_for(
                get_async_openai().chat.completions.create(
                    model=config.FINAL_MODEL,
                    messages=[
                        {"role": "system", "content": self._system_prompt()},
                        {
                            "role": "user",
                            "content": (
                                "Timeout de respaldo: DEBES traducir ahora a UNA línea ES "
                                "para subtítulo/TTS. No respondas hold. Solo JSON "
                                '{"action":"release","through_index":1,"es":"..."}.\n'
                                f"{ko_text}"
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                    **chat_completion_extra(
                        config.FINAL_MODEL,
                        800,
                        reasoning="none",
                        temperature=config.FINAL_TEMPERATURE,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            raw = response.choices[0].message.content or "{}"
            self._record_usage(response)
            data = json.loads(raw)
            es = str(data.get("es") or "").strip()
            if not es and raw.strip() and not raw.strip().startswith("{"):
                es = raw.strip()
            return _strip_incierto_markers(es)
        except Exception as e:
            logger.error("Sentence fallback translate error: %s", e)
            return ""

    async def _force_release_all(self) -> None:
        if not self._pending:
            return
        ko_summary = _ko_summary_for_anchor(
            [FragmentItem(f.item_id, f.ko, "") for f in self._pending],
            self._context if self._sermon_mode else None,
        )
        es = await self._fallback_translate(ko_summary)
        if not es:
            logger.warning(
                "Sentence force-release has no ES yet; keeping %d pending",
                len(self._pending),
            )
            self._arm_timeout()
            return
        ok = await self._release_through(len(self._pending), es, ["force_flush"])
        if not ok:
            logger.warning(
                "Sentence force-release failed; keeping %d pending",
                len(self._pending),
            )
            self._arm_timeout()
