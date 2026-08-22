"""KO pending → Understand (hold/release) → Translate on release → ReleasePacer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from hki import config
from hki.live.context import normalize_ko_stt
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.output_composer import FragmentItem, _ko_summary_for_anchor
from hki.live.release_pacer import ReleaseItem, ReleasePacer
from hki.live.sentence_guard import (
    join_source,
    resolve_release_index,
    select_translation_ko,
)
from hki.live.sentence_prompts import (
    build_translate_system_prompt,
    build_translate_user_message,
    build_understand_system_prompt,
    build_understand_user_message,
    describe_sentence_prompt,
)
from hki.live.translate import TRANSLATION_API_TIMEOUT_SEC

logger = logging.getLogger(__name__)

OnRelease = Callable[[ReleaseItem], Awaitable[None]]
OnUsage = Callable[..., None]
OnTrace = Callable[[dict], None]


@dataclass
class PendingFragment:
    item_id: str
    ko: str


def _strip_incierto_markers(text: str) -> str:
    cleaned = re.sub(r"\s*\[INCIERTO\]\s*", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _flags_has_incierto(flags) -> bool:
    if isinstance(flags, str):
        return flags.strip().lower() == "incierto"
    if isinstance(flags, list):
        return any(str(f).strip().lower() == "incierto" for f in flags)
    return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KoSentenceTranslator:
    def __init__(
        self,
        on_release: OnRelease,
        context: dict | None = None,
        sermon_mode: bool = False,
        on_usage: OnUsage | None = None,
        on_trace: OnTrace | None = None,
        manuscript: str = "",
    ):
        self._context = context
        self._sermon_mode = sermon_mode
        self._on_usage = on_usage
        self._on_trace = on_trace
        self._manuscript = manuscript or ""
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
        self._terminal_traced = False

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

    def set_manuscript(self, manuscript: str) -> None:
        self._manuscript = manuscript or ""

    def _ctx(self) -> dict | None:
        return self._context if self._sermon_mode else None

    def _record_usage(self, response, *, kind: str) -> None:
        if not self._on_usage:
            return
        prompt, completion = usage_from_response(response)
        if not prompt and not completion:
            return
        try:
            self._on_usage(prompt, completion, kind)
        except TypeError:
            self._on_usage(prompt, completion)

    def _emit_trace(self, payload: dict) -> None:
        if not self._on_trace:
            return
        self._on_trace(payload)

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
                await self._evaluate_pending(
                    force_release=True, release_reason="timeout"
                )
        except asyncio.CancelledError:
            pass

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        await self._final_queue.put((item_id, ko_text))

    def _absorb_final_queue(self) -> None:
        while not self._final_queue.empty():
            try:
                item_id, ko_text = self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if ko_text and ko_text.strip():
                self._pending.append(PendingFragment(item_id=item_id, ko=ko_text))

    async def drain(self, timeout: float = 180.0) -> bool:
        """Owner of terminal cleanup: flush translated ES, fail leftover STT."""
        deadline = asyncio.get_running_loop().time() + timeout
        attempts = 0
        while asyncio.get_running_loop().time() < deadline:
            self._absorb_final_queue()
            queue_busy = self._final_queue.qsize() > 0 or self._in_flight > 0
            if self._pending:
                await self._evaluate_pending(
                    force_release=True, release_reason="drain"
                )
                attempts += 1
                if self._pending:
                    if attempts >= 3:
                        break
                    await asyncio.sleep(0.25)
            elif not queue_busy:
                break
            else:
                await asyncio.sleep(0.05)

        self._absorb_final_queue()
        pacer_ok = True
        if self._pacer._running:
            pacer_ok = await self._pacer.drain(
                timeout=max(0.1, deadline - asyncio.get_running_loop().time())
            )
        leftover_es = self._pacer.pop_remaining()
        for item in leftover_es:
            try:
                await self._pacer.on_release(item)
            except Exception as e:
                logger.error("Sentence drain pacer flush error: %s", e)

        if self._pending:
            logger.warning(
                "Sentence drain still has %d pending after retries",
                len(self._pending),
            )
            self._trace_unreleased("translation_failed")
            self._pending.clear()
            self._terminal_traced = True
        return pacer_ok

    async def run(self) -> None:
        self._running = True
        await asyncio.gather(self._final_worker(), self._pacer.run())

    def stop_sync(self) -> None:
        """After drain: no second trace. Pacer queue should already be empty."""
        self._running = False
        self._cancel_timeout()
        self._absorb_final_queue()
        if self._pending:
            logger.warning(
                "Sentence stop with %d pending still held: %s",
                len(self._pending),
                " | ".join(f.ko[:40] for f in self._pending),
            )
            if not self._terminal_traced:
                self._trace_unreleased("translation_failed")
            self._pending.clear()
        self._pacer.stop_sync()
        self._terminal_traced = False

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
                await self._evaluate_pending(
                    force_release=True, release_reason="max_pending"
                )
            else:
                await self._evaluate_pending()

    async def _evaluate_pending(
        self,
        force_release: bool = False,
        release_reason: str = "",
    ) -> None:
        if not self._pending:
            return
        async with self._evaluate_lock:
            self._in_flight += 1
            try:
                while self._pending:
                    before = len(self._pending)
                    await self._call_evaluate(
                        force_release=force_release,
                        release_reason=release_reason,
                    )
                    force_release = False
                    release_reason = ""
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

    async def _chat_json(
        self,
        system: str,
        user: str,
        *,
        kind: str,
        max_tokens: int,
    ) -> dict | None:
        try:
            response = await asyncio.wait_for(
                get_async_openai().chat.completions.create(
                    model=config.FINAL_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    **chat_completion_extra(
                        config.FINAL_MODEL,
                        max_tokens,
                        reasoning="none",
                        temperature=config.FINAL_TEMPERATURE,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            raw = response.choices[0].message.content or "{}"
            self._record_usage(response, kind=kind)
            return json.loads(raw)
        except asyncio.TimeoutError:
            logger.error("Sentence %s timeout pending=%d", kind, len(self._pending))
            return None
        except Exception as e:
            logger.error("Sentence %s error: %s", kind, e)
            return None

    async def _call_evaluate(
        self,
        force_release: bool = False,
        release_reason: str = "",
    ) -> None:
        n = len(self._pending)
        if n <= 0:
            return
        normalized = self._normalized_pending()
        fragment_pairs = [(f.item_id, f.ko) for f in normalized]
        ctx = self._ctx()
        understand_system = build_understand_system_prompt(self._sermon_mode, ctx)
        ms = (self._manuscript or "").strip()
        if self._sermon_mode and ms:
            understand_system += (
                "\n\n원고 전문 (STT 대조용. 문장 생성·연속 구절 복사 금지):\n"
                + ms[:6000]
            )

        t0 = time.perf_counter()
        data = await self._chat_json(
            understand_system,
            build_understand_user_message(
                fragment_pairs,
                self._history,
                force_release=force_release,
            ),
            kind="understand",
            max_tokens=400,
        )
        latency_understand_ms = int((time.perf_counter() - t0) * 1000)

        raw_index = data.get("through_index") if data else None
        action = str((data or {}).get("action") or "").lower()
        k = resolve_release_index(raw_index, n, force=force_release)
        if not force_release and action != "release":
            k = 0

        if k <= 0:
            self._emit_trace(
                {
                    "timestamp": _utcnow_iso(),
                    "fragment_ids": [f.item_id for f in self._pending],
                    "original_stt": join_source([f.ko for f in self._pending]),
                    "action": "hold",
                    "through_index": 0,
                    "ko_corrected": "",
                    "stt_repair": False,
                    "release_reason": None,
                    "translation": "",
                    "latency_understand": latency_understand_ms,
                    "latency_translate": 0,
                    "repair_rejected": False,
                }
            )
            self._arm_timeout()
            return

        reason = release_reason if force_release else "sentence_complete"
        window_raw = self._pending[:k]
        window_norm = normalized[:k]
        original_stt = join_source([f.ko for f in window_raw])
        source = join_source([f.ko for f in window_norm])
        ko_corrected_raw = str((data or {}).get("ko_corrected") or "")
        ko_for_tr, stt_repair, repair_rejected = select_translation_ko(
            source,
            ko_corrected_raw,
            fragment_count=k,
            context=ctx,
            manuscript=self._manuscript,
        )
        ko_corrected = ko_for_tr if stt_repair else source

        t1 = time.perf_counter()
        es, had_incierto = await self._call_translate(ko_for_tr, original_stt)
        latency_translate_ms = int((time.perf_counter() - t1) * 1000)

        if not es:
            logger.warning(
                "Sentence translate produced no ES; keeping %d pending (k=%d)",
                n,
                k,
            )
            self._emit_trace(
                {
                    "timestamp": _utcnow_iso(),
                    "fragment_ids": [f.item_id for f in window_raw],
                    "original_stt": original_stt,
                    "action": "release",
                    "through_index": k,
                    "ko_corrected": ko_corrected,
                    "stt_repair": stt_repair,
                    "release_reason": reason,
                    "translation": "",
                    "latency_understand": latency_understand_ms,
                    "latency_translate": latency_translate_ms,
                    "repair_rejected": repair_rejected,
                }
            )
            self._arm_timeout()
            return

        ok = await self._release_through(
            k, es, reason, had_incierto=had_incierto
        )
        if not ok:
            logger.warning(
                "Sentence release failed; keeping %d pending",
                len(self._pending),
            )
            self._arm_timeout()
            return

        self._emit_trace(
            {
                "timestamp": _utcnow_iso(),
                "fragment_ids": [f.item_id for f in window_raw],
                "original_stt": original_stt,
                "action": "release",
                "through_index": k,
                "ko_corrected": ko_corrected,
                "stt_repair": stt_repair,
                "release_reason": reason,
                "translation": es,
                "latency_understand": latency_understand_ms,
                "latency_translate": latency_translate_ms,
                "repair_rejected": repair_rejected,
            }
        )

    async def _call_translate(self, ko_text: str, original_stt: str) -> tuple[str, bool]:
        ko_text = ko_text.strip()
        if not ko_text:
            return "", False
        ctx = self._ctx()
        data = await self._chat_json(
            build_translate_system_prompt(self._sermon_mode, ctx),
            build_translate_user_message(ko_text, original_stt, self._history),
            kind="translate",
            max_tokens=800,
        )
        if not data:
            return "", False
        es = str(data.get("es") or "").strip()
        if not es:
            return "", False
        es_clean = _strip_incierto_markers(es)
        if not es_clean:
            return "", False
        return es_clean, _flags_has_incierto(data.get("flags"))

    async def _release_through(
        self,
        through_index: int,
        es: str,
        release_reason: str,
        *,
        had_incierto: bool = False,
    ) -> bool:
        n = len(self._pending)
        if through_index < 1 or through_index > n:
            return False
        es_clean = _strip_incierto_markers(es)
        if not es_clean:
            return False
        batch = self._pending[:through_index]
        item_ids = [f.item_id for f in batch]
        ko_summary = _ko_summary_for_anchor(
            [FragmentItem(f.item_id, f.ko, "") for f in batch],
            self._context if self._sermon_mode else None,
        )
        self._pending = self._pending[through_index:]
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
                recombine_flags=[release_reason] if release_reason else [],
                anchor_repair=False,
                had_incierto=had_incierto,
            )
        )
        return True

    def _trace_unreleased(self, reason: str) -> None:
        """original_stt is backend join(pending), never LLM ko_corrected."""
        if not self._pending:
            return
        original_stt = join_source([f.ko for f in self._pending])
        self._emit_trace(
            {
                "timestamp": _utcnow_iso(),
                "fragment_ids": [f.item_id for f in self._pending],
                "original_stt": original_stt,
                "action": "hold",
                "through_index": 0,
                "ko_corrected": "",
                "stt_repair": False,
                "release_reason": reason,
                "translation": "",
                "latency_understand": 0,
                "latency_translate": 0,
                "repair_rejected": False,
            }
        )

    async def _force_release_all(self) -> None:
        """Tests and last-resort flush: force k=N then translate."""
        await self._call_evaluate(force_release=True, release_reason="drain")
