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
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.output_composer import FragmentItem, _ko_summary_for_anchor
from hki.live.release_pacer import ReleaseItem, ReleasePacer
from hki.live.sentence_guard import join_source, parse_recombine_units, select_translation_ko
from hki.live.sentence_prompts import (
    build_recombine_system_prompt,
    build_recombine_user_message,
    build_translate_system_prompt,
    build_translate_user_message,
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
    received_at: float = field(default_factory=time.monotonic)


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
        self._release_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task | None = None
        self._max_duration_task: asyncio.Task | None = None
        # This is a release debounce, not a sentence boundary detector.
        self._release_pause_sec = config.SENTENCE_RELEASE_PAUSE_MS / 1000.0
        self._max_buffer_sec = config.SENTENCE_MAX_BUFFER_MS / 1000.0
        self._max_pending = config.SENTENCE_MAX_PENDING
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

    def _arm_debounce(self) -> None:
        self._cancel_debounce()
        if not self._running or not self._pending:
            return
        self._debounce_task = asyncio.get_running_loop().create_task(self._debounce_fire())

    def _arm_max_duration(self) -> None:
        self._cancel_max_duration()
        if not self._running or not self._pending:
            return
        self._max_duration_task = asyncio.get_running_loop().create_task(
            self._max_duration_fire()
        )

    def on_speech_started(self) -> None:
        """Timer hint only. Pending + completed remain source of truth."""
        self._speech_active = True
        self._awaiting_transcript = False
        self._cancel_debounce()

    def on_speech_stopped(self) -> None:
        """Timer hint only. Do not release until the next completed arrives."""
        self._speech_active = False
        self._awaiting_transcript = True

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        await self._final_queue.put((item_id, ko_text))

    def _absorb_final_queue(self) -> None:
        while not self._final_queue.empty():
            try:
                item_id, ko_text = self._final_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if ko_text and ko_text.strip():
                self._pending.append(
                    PendingFragment(
                        item_id=item_id,
                        ko=ko_text,
                        received_at=time.monotonic(),
                    )
                )

    def _append_fragment(self, item_id: str, ko_text: str) -> None:
        was_empty = not self._pending
        self._awaiting_transcript = False
        self._pending.append(
            PendingFragment(item_id=item_id, ko=ko_text, received_at=time.monotonic())
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
        return snapshot

    def _spawn_release(self, reason: str, *, force: bool) -> None:
        asyncio.get_running_loop().create_task(self._try_release(reason, force=force))

    async def _debounce_fire(self) -> None:
        try:
            await asyncio.sleep(self._release_pause_sec)
            await self._try_release("vad_release", force=False)
        except asyncio.CancelledError:
            pass

    async def _max_duration_fire(self) -> None:
        try:
            await asyncio.sleep(self._max_buffer_sec)
            await self._try_release("max_duration", force=True)
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
            if not self._pending:
                return
            snapshot = self._detach_pending()
        self._in_flight += 1
        try:
            await self._release_snapshot(snapshot, reason)
        finally:
            self._in_flight -= 1
            if self._pending:
                self._arm_max_duration()
                if not self._speech_active:
                    self._arm_debounce()

    async def drain(self, timeout: float = 180.0) -> bool:
        """Owner of terminal cleanup: flush translated ES, fail leftover STT."""
        deadline = asyncio.get_running_loop().time() + timeout
        self._cancel_debounce()
        self._cancel_max_duration()
        self._awaiting_transcript = False
        attempts = 0
        while asyncio.get_running_loop().time() < deadline:
            self._absorb_final_queue()
            queue_busy = self._final_queue.qsize() > 0 or self._in_flight > 0
            if self._pending:
                await self._try_release("drain", force=True)
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
            self._trace_unreleased(self._pending, "translation_failed")
            self._pending.clear()
            self._terminal_traced = True
        return pacer_ok

    async def run(self) -> None:
        self._running = True
        await asyncio.gather(self._final_worker(), self._pacer.run())

    def stop_sync(self) -> None:
        """After drain: no second trace. Pacer queue should already be empty."""
        self._running = False
        self._cancel_debounce()
        self._cancel_max_duration()
        self._absorb_final_queue()
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
                item_id, ko_text = await asyncio.wait_for(
                    self._final_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            if not ko_text.strip():
                continue
            self._append_fragment(item_id, ko_text)

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
                        temperature=extra_temp,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            raw = response.choices[0].message.content or "{}"
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
    ) -> None:
        if not snapshot:
            return
        normalized = self._normalized(snapshot)
        n = len(normalized)
        fragment_pairs = [(f.item_id, f.ko) for f in normalized]
        original_stt = join_source([f.ko for f in snapshot])
        ctx = self._ctx()
        recombine_id = uuid.uuid4().hex[:12]

        t0 = time.perf_counter()
        data = await self._chat_json(
            build_recombine_system_prompt(self._sermon_mode, ctx),
            build_recombine_user_message(fragment_pairs, self._history),
            kind="recombine",
            max_tokens=600,
            temperature=config.RECOMBINE_TEMPERATURE,
        )
        latency_recombine_ms = int((time.perf_counter() - t0) * 1000)

        units = parse_recombine_units(data, n)
        mapping_fallback = units is None
        if mapping_fallback:
            units = self._fallback_units(normalized)
        if not units:
            self._trace_unreleased(snapshot, "translation_failed", recombine_id)
            return

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
            t1 = time.perf_counter()
            es, had_incierto = await self._call_translate(ko_text, unit_stt)
            latency_translate_ms = int((time.perf_counter() - t1) * 1000)
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
                        latency_recombine_ms=latency_recombine_ms,
                        latency_translate_ms=latency_translate_ms,
                        mapping_fallback=mapping_fallback,
                        repair_rejected=rejected or any_rejected,
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
                    recombine_flags=[reason] if reason else [],
                    release_reason=reason,
                    had_incierto=had_incierto,
                    repair_rejected=rejected,
                    latency_recombine=latency_recombine_ms,
                    latency_translate=latency_translate_ms,
                    translated_at_mono=time.monotonic(),
                    first_fragment_at_mono=first_at,
                    last_fragment_at_mono=last_at,
                    fragment_count=n,
                    unit_index=unit_index,
                    unit_count=unit_count,
                    fragment_indexes=list(indexes),
                    mapping_fallback=mapping_fallback,
                    recombine_id=recombine_id,
                )
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

    async def _on_pacer_release(self, item: ReleaseItem) -> None:
        now = time.monotonic()
        if item.first_fragment_at_mono:
            item.latency_first_fragment_to_caption = int(
                (now - item.first_fragment_at_mono) * 1000
            )
        if item.last_fragment_at_mono:
            item.latency_last_fragment_to_caption = int(
                (now - item.last_fragment_at_mono) * 1000
            )
        if item.translated_at_mono:
            item.latency_release_to_caption = int(
                (now - item.translated_at_mono) * 1000
            )
            item.release_latency_ms = item.latency_release_to_caption
        self._emit_trace(
            {
                "timestamp": _utcnow_iso(),
                "fragment_ids": list(item.item_ids),
                "original_stt": item.original_stt or item.ko_summary,
                "action": "release",
                "ko_corrected": item.ko_corrected or item.ko_summary,
                "release_reason": item.release_reason,
                "translation": item.es,
                "latency_understand": 0,
                "latency_recombine": item.latency_recombine,
                "latency_translate": item.latency_translate,
                "latency_first_fragment_to_caption": item.latency_first_fragment_to_caption,
                "latency_last_fragment_to_caption": item.latency_last_fragment_to_caption,
                "latency_release_to_caption": item.latency_release_to_caption,
                "fragment_count": item.fragment_count,
                "unit_index": item.unit_index,
                "unit_count": item.unit_count,
                "fragment_indexes": list(item.fragment_indexes),
                "mapping_fallback": item.mapping_fallback,
                "repair_rejected": item.repair_rejected,
                "had_incierto": item.had_incierto,
                "recombine_id": item.recombine_id,
                "stt_repair": False,
            }
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
        latency_recombine_ms: int,
        latency_translate_ms: int,
        mapping_fallback: bool,
        repair_rejected: bool,
    ) -> dict:
        return {
            "timestamp": _utcnow_iso(),
            "fragment_ids": [snapshot[i].item_id for i in indexes],
            "original_stt": original_stt,
            "action": "release" if translation else "hold",
            "ko_corrected": ko_corrected,
            "release_reason": reason,
            "translation": translation,
            "latency_understand": 0,
            "latency_recombine": latency_recombine_ms,
            "latency_translate": latency_translate_ms,
            "fragment_count": len(snapshot),
            "unit_index": unit_index,
            "unit_count": unit_count,
            "fragment_indexes": list(indexes),
            "mapping_fallback": mapping_fallback,
            "repair_rejected": repair_rejected,
            "had_incierto": False,
            "recombine_id": recombine_id,
            "stt_repair": False,
        }

    def _trace_unreleased(
        self,
        fragments: list[PendingFragment],
        reason: str,
        recombine_id: str = "",
    ) -> None:
        if not fragments:
            return
        original_stt = join_source([f.ko for f in fragments])
        self._emit_trace(
            {
                "timestamp": _utcnow_iso(),
                "fragment_ids": [f.item_id for f in fragments],
                "original_stt": original_stt,
                "action": "hold",
                "ko_corrected": "",
                "release_reason": reason,
                "translation": "",
                "latency_understand": 0,
                "latency_recombine": 0,
                "latency_translate": 0,
                "fragment_count": len(fragments),
                "unit_index": 0,
                "unit_count": 0,
                "fragment_indexes": list(range(len(fragments))),
                "mapping_fallback": True,
                "repair_rejected": False,
                "recombine_id": recombine_id,
                "stt_repair": False,
            }
        )

    async def _force_release_all(self) -> None:
        """Tests and last-resort flush: detach current pending and release."""
        await self._try_release("drain", force=True)
