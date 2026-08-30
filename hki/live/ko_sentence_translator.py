"""KO pending buffer → utterance debounce → Recombine once → Translate → ReleasePacer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from hki import config
from hki.live.context import normalize_ko_stt
from hki.live.ko_endings import fragment_looks_open_ko
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.output_composer import FragmentItem, _ko_summary_for_anchor
from hki.live.release_pacer import ReleaseItem, ReleasePacer
from hki.live.sentence_guard import (
    join_source,
    last_unit_open,
    parse_recombine_units,
    select_translation_ko,
)
from hki.live.sentence_prompts import (
    build_recombine_system_prompt,
    build_recombine_user_message,
    build_translate_system_prompt,
    build_translate_user_message,
    describe_sentence_prompt,
)
from hki.live.trace_schema import (
    ItemTiming,
    RELEASE_REASONS,
    build_release_trace,
    merge_item_timings,
    ms_since,
    stamp_release_latencies,
    trace_from_release_item,
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
    received_at: float = field(default_factory=time.monotonic)
    timing: ItemTiming = field(default_factory=ItemTiming.fallback_now)


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
        self._final_queue: asyncio.Queue[tuple[str, str, ItemTiming]] = asyncio.Queue()
        self._running = False
        self._stopped = False
        self._in_flight = 0
        self._last_llm = {"ms": 0, "in": 0, "out": 0, "ok": False}
        self._release_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task | None = None
        self._max_duration_task: asyncio.Task | None = None
        self._incomplete_task: asyncio.Task | None = None
        self._release_tasks: set[asyncio.Task] = set()
        self._in_flight_snapshots: list[list[PendingFragment]] = []
        # This is a release debounce, not a sentence boundary detector.
        self._release_pause_sec = config.SENTENCE_RELEASE_PAUSE_MS / 1000.0
        self._max_buffer_sec = config.SENTENCE_MAX_BUFFER_MS / 1000.0
        self._max_pending = config.SENTENCE_MAX_PENDING
        self._incomplete_timeout_sec = config.SENTENCE_INCOMPLETE_TIMEOUT_MS / 1000.0
        self._speech_active = False
        self._awaiting_transcript = False
        self._pacer = ReleasePacer(self._on_pacer_release, depth_fn=self._pacer_depth)
        self._user_on_release = on_release
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

    def _cancel_task(self, task: asyncio.Task | None) -> None:
        if task and not task.done():
            task.cancel()

    def _cancel_debounce(self) -> None:
        self._cancel_task(self._debounce_task)
        self._debounce_task = None

    def _cancel_max_duration(self) -> None:
        self._cancel_task(self._max_duration_task)
        self._max_duration_task = None

    def _cancel_incomplete(self) -> None:
        self._cancel_task(self._incomplete_task)
        self._incomplete_task = None

    def _incomplete_remaining(self, frag: PendingFragment) -> float:
        elapsed = time.monotonic() - frag.received_at
        return self._incomplete_timeout_sec - elapsed

    def _map_oracion_reason(
        self, reason: str, snapshot: list[PendingFragment]
    ) -> str:
        if reason in RELEASE_REASONS:
            return reason
        if reason == "vad_release":
            last = snapshot[-1] if snapshot else None
            if (
                last
                and fragment_looks_open_ko(last.ko)
                and self._incomplete_remaining(last) <= 0
            ):
                return "incomplete_cap_expired"
            return "closed_immediate"
        return reason or "closed_immediate"

    def _hold_for_snapshot(
        self, snapshot: list[PendingFragment], reason: str
    ) -> tuple[int, str]:
        if not snapshot:
            return 0, ""
        hold_ms = ms_since(snapshot[-1].received_at)
        if reason == "incomplete_cap_expired":
            return hold_ms, "incomplete_timeout_expired"
        if reason in ("max_pending", "max_duration"):
            return hold_ms, "batch_wait"
        if reason == "closed_immediate":
            return hold_ms, ""
        if fragment_looks_open_ko(snapshot[-1].ko):
            return hold_ms, "fragment_looks_open"
        return hold_ms, ""

    def _arm_debounce(self) -> None:
        self._cancel_debounce()
        if self._stopped or not self._pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._debounce_task = loop.create_task(self._debounce_fire())

    def _arm_max_duration(self) -> None:
        self._cancel_max_duration()
        if self._stopped or not self._pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        first = self._pending[0]
        remaining = self._max_buffer_sec - (time.monotonic() - first.received_at)
        if remaining <= 0:
            self._spawn_release("max_duration", force=True)
            return
        self._max_duration_task = loop.create_task(self._max_duration_fire(remaining))

    def _arm_incomplete(self, remaining_sec: float) -> None:
        self._cancel_incomplete()
        if self._stopped or not self._pending or remaining_sec <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._incomplete_task = loop.create_task(self._incomplete_fire(remaining_sec))

    def on_speech_started(self) -> None:
        """Timer hint only. Pending + completed remain source of truth."""
        self._speech_active = True
        self._awaiting_transcript = False
        self._cancel_debounce()

    def on_speech_stopped(self) -> None:
        """Timer hint only. Pending + completed remain source of truth.

        If fragments are already buffered, re-arm debounce — a timer that
        expired while speech was active must not become a dead end.
        If the buffer is empty, wait for the in-flight completed instead.
        """
        self._speech_active = False
        if self._pending:
            self._awaiting_transcript = False
            self._arm_debounce()
        else:
            self._awaiting_transcript = True

    async def on_transcript_completed(
        self,
        item_id: str,
        ko_text: str,
        timing: ItemTiming | None = None,
    ) -> None:
        if not (ko_text and str(ko_text).strip()):
            return
        logger.info(
            "Sentence STT item=%s chars=%d pending=%d running=%s",
            item_id,
            len(ko_text),
            len(self._pending),
            self._running,
        )
        stamp = timing or ItemTiming.fallback_now()
        try:
            self._append_fragment(item_id, ko_text, stamp)
        except RuntimeError:
            await self._final_queue.put((item_id, ko_text, stamp))

    def _absorb_final_queue(self) -> None:
        while not self._final_queue.empty():
            try:
                packed = self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            item_id, ko_text = packed[0], packed[1]
            timing = packed[2] if len(packed) > 2 else ItemTiming.fallback_now()
            if ko_text and ko_text.strip():
                self._pending.append(
                    PendingFragment(
                        item_id=item_id,
                        ko=ko_text,
                        received_at=time.monotonic(),
                        timing=timing,
                    )
                )

    def _append_fragment(
        self,
        item_id: str,
        ko_text: str,
        timing: ItemTiming | None = None,
    ) -> None:
        if self._stopped:
            logger.warning("Sentence STT after stop ignored item=%s", item_id)
            return
        was_empty = not self._pending
        self._awaiting_transcript = False
        self._cancel_incomplete()
        self._pending.append(
            PendingFragment(
                item_id=item_id,
                ko=ko_text,
                received_at=time.monotonic(),
                timing=timing or ItemTiming.fallback_now(),
            )
        )
        if was_empty:
            self._arm_max_duration()
        if len(self._pending) >= self._max_pending:
            self._spawn_release("max_pending", force=True)
        else:
            self._arm_debounce()

    def _detach_pending(self) -> list[PendingFragment]:
        snapshot = self._pending
        self._pending = []
        self._cancel_debounce()
        self._cancel_max_duration()
        self._cancel_incomplete()
        return snapshot

    def _spawn_release(self, reason: str, *, force: bool) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._try_release(reason, force=force))
        self._release_tasks.add(task)
        task.add_done_callback(self._release_tasks.discard)

    async def _debounce_fire(self) -> None:
        try:
            await asyncio.sleep(self._release_pause_sec)
            # Independent task: cancelling this timer must not abort an in-flight LLM.
            self._spawn_release("closed_immediate", force=False)
        except asyncio.CancelledError:
            pass

    async def _max_duration_fire(self, wait_sec: float | None = None) -> None:
        try:
            await asyncio.sleep(
                self._max_buffer_sec if wait_sec is None else wait_sec
            )
            self._spawn_release("max_duration", force=True)
        except asyncio.CancelledError:
            pass

    async def _incomplete_fire(self, wait_sec: float) -> None:
        try:
            await asyncio.sleep(wait_sec)
            self._spawn_release("incomplete_cap_expired", force=False)
        except asyncio.CancelledError:
            pass

    async def _try_release(self, reason: str, *, force: bool = False) -> None:
        async with self._release_lock:
            if not force:
                if self._speech_active:
                    return
                if self._awaiting_transcript:
                    return
                if not self._pending:
                    return
                last = self._pending[-1]
                remaining = self._incomplete_remaining(last)
                if fragment_looks_open_ko(last.ko) and remaining > 0:
                    self._arm_incomplete(remaining)
                    return
            if not self._pending:
                return
            snapshot = self._detach_pending()
        logger.info(
            "Sentence release start reason=%s fragments=%d",
            reason,
            len(snapshot),
        )
        self._in_flight += 1
        self._in_flight_snapshots.append(snapshot)
        leftover: list[PendingFragment] = []
        try:
            leftover = await self._release_snapshot(
                snapshot, reason, leftover_ok=not force
            )
        except asyncio.CancelledError:
            logger.warning("Sentence release cancelled (%s)", reason)
            self._trace_unreleased(snapshot, "translation_failed")
            raise
        except Exception:
            logger.exception("Sentence release failed (%s)", reason)
            self._trace_unreleased(snapshot, "translation_failed")
        finally:
            self._in_flight -= 1
            if snapshot in self._in_flight_snapshots:
                self._in_flight_snapshots.remove(snapshot)
            if leftover:
                async with self._release_lock:
                    self._pending = leftover + self._pending
                    rem = self._incomplete_remaining(leftover[-1])
                    if rem > 0:
                        self._arm_incomplete(rem)
            if self._pending:
                self._arm_max_duration()
                if not self._speech_active:
                    self._arm_debounce()

    async def drain(self, timeout: float = 180.0) -> bool:
        """Owner of terminal cleanup: flush translated ES, fail leftover STT."""
        logger.info(
            "Sentence drain begin pending=%d queue=%d in_flight=%d pacer=%d",
            len(self._pending),
            self._final_queue.qsize(),
            self._in_flight,
            self._pacer.pending_count(),
        )
        deadline = asyncio.get_running_loop().time() + timeout
        self._cancel_debounce()
        self._cancel_max_duration()
        self._cancel_incomplete()
        self._awaiting_transcript = False
        self._speech_active = False
        attempts = 0
        while asyncio.get_running_loop().time() < deadline:
            self._absorb_final_queue()
            queue_busy = self._final_queue.qsize() > 0 or self._in_flight > 0
            if self._pending:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        self._try_release("drain", force=True),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Sentence drain release timed out")
                    break
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
        leftover_tasks = list(self._release_tasks)
        for task in leftover_tasks:
            if not task.done():
                task.cancel()
        if leftover_tasks:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(
                    asyncio.gather(*leftover_tasks, return_exceptions=True),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                logger.warning("Sentence drain still waiting on cancelled releases")
        leftover_inflight = list(self._in_flight_snapshots)
        self._in_flight_snapshots.clear()
        for snapshot in leftover_inflight:
            self._trace_unreleased(snapshot, "translation_failed")
        if leftover_inflight:
            self._terminal_traced = True
        pacer_ok = True
        if self._pacer._running:
            pacer_ok = await self._pacer.drain(
                timeout=max(0.1, deadline - asyncio.get_running_loop().time())
            )
        leftover_es = self._pacer.pop_remaining()
        for item in leftover_es:
            try:
                stamp_release_latencies(item)
                await self._pacer.on_release(item)
            except Exception as e:
                logger.error("Sentence drain pacer flush error: %s", e)

        if self._pending:
            logger.warning(
                "Sentence drain still has %d pending after retries",
                len(self._pending),
            )
            self._trace_unreleased(self._pending, "translation_failed")
            self._pending.clear()
            self._terminal_traced = True
        logger.info(
            "Sentence drain end pending=%d leftover_es=%d pacer_ok=%s",
            len(self._pending),
            len(leftover_es),
            pacer_ok,
        )
        return pacer_ok

    async def run(self) -> None:
        self._stopped = False
        self._running = True
        logger.info("Sentence translator loop started")
        try:
            results = await asyncio.gather(
                self._final_worker(),
                self._pacer.run(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.error("Sentence translator task failed: %s", result)
        finally:
            logger.info("Sentence translator loop ended")

    def stop_sync(self) -> None:
        """After drain: no second trace. Pacer queue should already be empty."""
        self._stopped = True
        self._running = False
        self._cancel_debounce()
        self._cancel_max_duration()
        self._cancel_incomplete()
        self._absorb_final_queue()
        leftover_inflight = list(self._in_flight_snapshots)
        self._in_flight_snapshots.clear()
        if leftover_inflight and not self._terminal_traced:
            for snapshot in leftover_inflight:
                self._trace_unreleased(snapshot, "translation_failed")
        if self._pending:
            logger.warning(
                "Sentence stop with %d pending still held: %s",
                len(self._pending),
                " | ".join(f.ko[:40] for f in self._pending),
            )
            if not self._terminal_traced:
                self._trace_unreleased(self._pending, "translation_failed")
            self._pending.clear()
        self._pacer.stop_sync()
        self._terminal_traced = False

    async def _final_worker(self) -> None:
        while self._running:
            try:
                packed = await asyncio.wait_for(
                    self._final_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            item_id, ko_text = packed[0], packed[1]
            timing = packed[2] if len(packed) > 2 else ItemTiming.fallback_now()
            if not ko_text or not str(ko_text).strip():
                continue
            try:
                self._append_fragment(item_id, ko_text, timing)
            except Exception:
                logger.exception("Sentence fragment append failed item=%s", item_id)

    def _normalized(self, fragments: list[PendingFragment]) -> list[PendingFragment]:
        out: list[PendingFragment] = []
        for frag in fragments:
            ko = frag.ko
            if self._sermon_mode and self._context:
                ko = normalize_ko_stt(ko, self._context)
            out.append(
                PendingFragment(
                    item_id=frag.item_id,
                    ko=ko,
                    received_at=frag.received_at,
                    timing=frag.timing,
                )
            )
        return out

    async def _chat_json(
        self,
        system: str,
        user: str,
        *,
        kind: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> dict | None:
        extra_temp = (
            temperature if temperature is not None else config.FINAL_TEMPERATURE
        )
        self._last_llm = {"ms": 0, "in": 0, "out": 0, "ok": False}
        t0 = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                get_async_openai().chat.completions.create(
                    model=config.FINAL_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    timeout=TRANSLATION_API_TIMEOUT_SEC,
                    **chat_completion_extra(
                        config.FINAL_MODEL,
                        max_tokens,
                        reasoning="none",
                        temperature=extra_temp,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            raw = response.choices[0].message.content or "{}"
            prompt, completion = usage_from_response(response)
            self._last_llm = {
                "ms": max(0, int((time.perf_counter() - t0) * 1000)),
                "in": prompt,
                "out": completion,
                "ok": True,
            }
            self._record_usage(response, kind=kind)
            return json.loads(raw)
        except asyncio.TimeoutError:
            logger.error("Sentence %s timeout", kind)
            return None
        except Exception as e:
            logger.error("Sentence %s error: %s", kind, e)
            return None

    def _fallback_units(
        self, normalized: list[PendingFragment]
    ) -> list[tuple[str, list[int]]]:
        source = join_source([f.ko for f in normalized])
        return [(source, list(range(len(normalized))))] if source else []

    async def _release_snapshot(
        self,
        snapshot: list[PendingFragment],
        reason: str,
        *,
        leftover_ok: bool = False,
    ) -> list[PendingFragment]:
        if not snapshot:
            return []
        normalized = self._normalized(snapshot)
        n = len(normalized)
        fragment_pairs = [(f.item_id, f.ko) for f in normalized]
        original_stt = join_source([f.ko for f in snapshot])
        ctx = self._ctx()
        recombine_id = uuid.uuid4().hex[:12]
        canon_reason = self._map_oracion_reason(reason, snapshot)
        hold_ms, hold_reason = self._hold_for_snapshot(snapshot, canon_reason)

        data = await self._chat_json(
            build_recombine_system_prompt(self._sermon_mode, ctx),
            build_recombine_user_message(fragment_pairs, self._history),
            kind="recombine",
            max_tokens=600,
            temperature=config.RECOMBINE_TEMPERATURE,
        )
        recombine_llm = dict(self._last_llm)
        used_llm_recombine = bool(recombine_llm.get("ok"))
        recombine_llm_ms = int(recombine_llm.get("ms") or 0) if used_llm_recombine else 0
        tokens_recombine_in = int(recombine_llm.get("in") or 0) if used_llm_recombine else 0
        tokens_recombine_out = int(recombine_llm.get("out") or 0) if used_llm_recombine else 0
        if not used_llm_recombine and data is None:
            canon_reason = "recombine_fallback"

        units = parse_recombine_units(data, n)
        mapping_fallback = units is None
        if mapping_fallback:
            units = self._fallback_units(normalized)
        if not units:
            self._trace_unreleased(snapshot, "translation_failed", recombine_id)
            return []

        leftover: list[PendingFragment] = []
        llm_open = False if mapping_fallback else last_unit_open(data)
        if leftover_ok and len(units) > 1:
            last_text, last_indexes = units[-1]
            last_frags = [snapshot[i] for i in last_indexes]
            rem = self._incomplete_remaining(last_frags[-1])
            if rem > 0 and (fragment_looks_open_ko(last_text) or llm_open):
                leftover = last_frags
                units = units[:-1]

        if not units:
            return leftover

        guarded: list[tuple[str, list[int], bool]] = []
        any_rejected = False
        for text, indexes in units:
            source = join_source([normalized[i].ko for i in indexes])
            ko_for_tr, _changed, rejected = select_translation_ko(
                source,
                text,
                fragment_count=len(indexes),
                context=ctx,
                manuscript=self._manuscript,
            )
            if rejected:
                any_rejected = True
            guarded.append((ko_for_tr, indexes, rejected))

        first_at = snapshot[0].received_at
        last_at = snapshot[-1].received_at
        unit_count = len(guarded)

        for unit_index, (ko_text, indexes, rejected) in enumerate(guarded):
            unit_stt = join_source([snapshot[i].ko for i in indexes])
            unit_timing = merge_item_timings([snapshot[i].timing for i in indexes])
            es, had_incierto = await self._call_translate(ko_text, unit_stt)
            translate_llm = dict(self._last_llm)
            used_llm_translate = bool(translate_llm.get("ok"))
            translate_llm_ms = (
                int(translate_llm.get("ms") or 0) if used_llm_translate else 0
            )
            tokens_translate_in = (
                int(translate_llm.get("in") or 0) if used_llm_translate else 0
            )
            tokens_translate_out = (
                int(translate_llm.get("out") or 0) if used_llm_translate else 0
            )
            if not es:
                logger.warning(
                    "Sentence translate produced no ES; snapshot already detached"
                )
                self._emit_trace(
                    self._trace_payload(
                        snapshot=snapshot,
                        indexes=indexes,
                        original_stt=unit_stt or original_stt,
                        ko_corrected=ko_text,
                        translation="",
                        reason="translation_failed",
                        recombine_id=recombine_id,
                        unit_index=unit_index,
                        unit_count=unit_count,
                        used_llm_recombine=used_llm_recombine,
                        recombine_llm_ms=recombine_llm_ms,
                        used_llm_translate=used_llm_translate,
                        translate_llm_ms=translate_llm_ms,
                        tokens_recombine_in=tokens_recombine_in,
                        tokens_recombine_out=tokens_recombine_out,
                        tokens_translate_in=tokens_translate_in,
                        tokens_translate_out=tokens_translate_out,
                        hold_ms=hold_ms,
                        hold_reason=hold_reason,
                        mapping_fallback=mapping_fallback,
                        repair_rejected=rejected or any_rejected,
                        timing=unit_timing,
                    )
                )
                continue

            item_ids = [snapshot[i].item_id for i in indexes]
            ko_summary = _ko_summary_for_anchor(
                [FragmentItem(snapshot[i].item_id, snapshot[i].ko, "") for i in indexes],
                self._context if self._sermon_mode else None,
            )
            self._history.append({"ko": ko_summary, "es": es})
            if len(self._history) > max(2, config.FINAL_HISTORY_LINES * 2):
                self._history = self._history[-(config.FINAL_HISTORY_LINES * 2) :]

            await self._pacer.enqueue(
                ReleaseItem(
                    batch_id=item_ids[0],
                    es=es,
                    item_ids=item_ids,
                    ko_summary=ko_summary,
                    ko_corrected=ko_text,
                    original_stt=unit_stt or original_stt,
                    recombine_flags=[canon_reason] if canon_reason else [],
                    release_reason=canon_reason,
                    had_incierto=had_incierto,
                    repair_rejected=rejected,
                    latency_recombine=recombine_llm_ms,
                    latency_translate=translate_llm_ms,
                    translated_at_mono=time.monotonic(),
                    first_fragment_at_mono=first_at,
                    last_fragment_at_mono=last_at,
                    fragment_count=n,
                    unit_index=unit_index,
                    unit_count=unit_count,
                    fragment_indexes=list(indexes),
                    mapping_fallback=mapping_fallback,
                    recombine_id=recombine_id,
                    t_audio_start=unit_timing.t_audio_start,
                    t_audio_start_source=unit_timing.t_audio_start_source,
                    t_stt_final=unit_timing.t_stt_final,
                    t_audio_start_mono=unit_timing.t_audio_start_mono,
                    t_stt_final_mono=unit_timing.t_stt_final_mono,
                    used_llm_translate=used_llm_translate,
                    translate_llm_ms=translate_llm_ms,
                    used_llm_recombine=used_llm_recombine,
                    recombine_llm_ms=recombine_llm_ms,
                    hold_ms=hold_ms,
                    hold_reason=hold_reason,
                    fragment_open_final=fragment_looks_open_ko(ko_text),
                    tokens_translate_in=tokens_translate_in,
                    tokens_translate_out=tokens_translate_out,
                    tokens_recombine_in=tokens_recombine_in,
                    tokens_recombine_out=tokens_recombine_out,
                    pipeline="oracion",
                )
            )
        return leftover

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

    async def _on_pacer_release(self, item: ReleaseItem) -> None:
        item.pipeline = "oracion"
        self._emit_trace(
            trace_from_release_item(
                item, pipeline="oracion", timestamp=_utcnow_iso()
            )
        )
        await self._user_on_release(item)

    def _trace_payload(
        self,
        *,
        snapshot: list[PendingFragment],
        indexes: list[int],
        original_stt: str,
        ko_corrected: str,
        translation: str,
        reason: str,
        recombine_id: str,
        unit_index: int,
        unit_count: int,
        used_llm_recombine: bool = False,
        recombine_llm_ms: int = 0,
        used_llm_translate: bool = False,
        translate_llm_ms: int = 0,
        tokens_recombine_in: int = 0,
        tokens_recombine_out: int = 0,
        tokens_translate_in: int = 0,
        tokens_translate_out: int = 0,
        hold_ms: int = 0,
        hold_reason: str = "",
        mapping_fallback: bool = False,
        repair_rejected: bool = False,
        timing: ItemTiming | None = None,
    ) -> dict:
        merged = timing or merge_item_timings([snapshot[i].timing for i in indexes])
        return build_release_trace(
            timestamp=_utcnow_iso(),
            pipeline="oracion",
            action="release" if translation else "hold",
            fragment_ids=[snapshot[i].item_id for i in indexes],
            original_stt=original_stt,
            ko_corrected=ko_corrected,
            translation=translation,
            fragment_count=len(snapshot),
            unit_index=unit_index,
            unit_count=unit_count,
            fragment_indexes=list(indexes),
            recombine_id=recombine_id,
            t_audio_start=merged.t_audio_start,
            t_audio_start_source=merged.t_audio_start_source,
            t_stt_final=merged.t_stt_final,
            used_llm_translate=used_llm_translate,
            translate_llm_ms=translate_llm_ms,
            used_llm_recombine=used_llm_recombine,
            recombine_llm_ms=recombine_llm_ms,
            hold_ms=hold_ms,
            hold_reason=hold_reason,
            fragment_open_final=fragment_looks_open_ko(ko_corrected),
            release_reason=reason,
            tokens_translate_in=tokens_translate_in,
            tokens_translate_out=tokens_translate_out,
            tokens_recombine_in=tokens_recombine_in,
            tokens_recombine_out=tokens_recombine_out,
            mapping_fallback=mapping_fallback,
            repair_rejected=repair_rejected,
        )

    def _trace_unreleased(
        self,
        fragments: list[PendingFragment],
        reason: str,
        recombine_id: str = "",
    ) -> None:
        if not fragments:
            return
        original_stt = join_source([f.ko for f in fragments])
        merged = merge_item_timings([f.timing for f in fragments])
        self._emit_trace(
            build_release_trace(
                timestamp=_utcnow_iso(),
                pipeline="oracion",
                action="hold",
                fragment_ids=[f.item_id for f in fragments],
                original_stt=original_stt,
                release_reason=reason,
                translation="",
                fragment_count=len(fragments),
                unit_index=0,
                unit_count=0,
                fragment_indexes=list(range(len(fragments))),
                recombine_id=recombine_id,
                t_audio_start=merged.t_audio_start,
                t_audio_start_source=merged.t_audio_start_source,
                t_stt_final=merged.t_stt_final,
                mapping_fallback=True,
            )
        )

    async def _force_release_all(self) -> None:
        """Tests and last-resort flush: detach current pending and release."""
        await self._try_release("drain", force=True)
