"""Classic v2 lookahead recombine — log-only, does not replace OutputComposer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from hki import config
from hki.live.context import format_context_for_recombine
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.output_composer import (
    FragmentItem,
    _fallback_join,
    _is_faithful,
    _ko_summary,
    _ko_summary_for_anchor,
    _needs_recombine_llm,
    _strip_incierto_markers,
    _joined_has_incierto,
)
from hki.live.release_pacer import ReleaseItem

logger = logging.getLogger(__name__)

OnRelease = Callable[["ReleaseItem"], Awaitable[None]]
OnUsage = Callable[[int, int], None]

MAX_WINDOW = 3

# Lookahead value, not a sentence-complete detector. No large exception lists.
_KO_CLEAR_FINAL = re.compile(
    r"(다|요|니다|습니다|ㅂ니다|십시오|시오|세요|죠|네요|라|아멘)[.!?。]*$",
)
_KO_OPEN_END = re.compile(
    r"(은|는|이|가|을|를|의|에|에서|에게|께서|와|과|로|으로|고|며|면|"
    r"니까|지만|는데|그리고|그런데|그래서|왜냐하면|그러나|하지만|즉)$"
)
_CONNECTOR_KO = (
    "그리고",
    "그런데",
    "그래서",
    "왜냐하면",
    "그러나",
    "하지만",
    "즉",
    "또한",
)

V2_RECOMBINE_SYSTEM = """Eres editor de subtítulos en vivo con lookahead corto (no detector de oración).
Recibes 1..3 fragmentos YA TRADUCIDOS al español, en orden.
Tu pregunta principal: cuántos fragmentos DEL INICIO se publican AHORA.

Contrato:
- consume: 1..N fragmentos del inicio. consume=1 es un resultado NORMAL.
- No intentes unir toda la ventana en una sola frase natural.
- hold=true solo si N<3 y conviene esperar el siguiente fragmento; entonces consume=0 y es="".
- Si N==3, hold está prohibido: consume al menos 1.
- es: texto SOLO del prefix consumido (fragmentos 1..consume), no de la ventana completa si consume<N.
- Une ese prefix con conectores mínimos y puntuación. NO inventes. NO mejores el sermón.
- Si el prefix está marcado [INCIERTO] o gramaticalmente roto y hay critical_sentences en el contexto, puedes reparar lo mínimo en ese prefix. Si el prefix ya tiene sentido, no lo reemplaces.
- JSON únicamente:
  {"es":"...","consume":1,"hold":false,"flags":[]}
"""


@dataclass
class V2Fragment:
    item_id: str
    ko: str
    es: str
    arrive_seq: int
    translated_at: float
    should_wait: bool
    grace_ms: int


@dataclass
class V2Decision:
    es: str
    consume: int
    hold: bool = False
    flags: list[str] = field(default_factory=list)
    used_llm: bool = False
    repair_rejected: bool = False
    anchor_repair: bool = False
    had_incierto: bool = False
    joined_preview: str = ""
    fallback: bool = False


def _ko_tokens(ko: str) -> list[str]:
    return [t for t in re.split(r"\s+", (ko or "").strip()) if t]


def _has_clear_final_ending(ko: str) -> bool:
    s = (ko or "").strip()
    if not s or s.endswith("...") or s.endswith("…"):
        return False
    if re.search(r"[.!?。]$", s):
        return True
    return bool(_KO_CLEAR_FINAL.search(s))


def should_wait_for_lookahead(ko: str, es: str = "") -> bool:
    """True = worth a short wait for the next fragment. Not 'is this a full sentence?'."""
    ko_s = (ko or "").strip()
    es_s = (es or "").strip()
    if ko_s.endswith("...") or ko_s.endswith("…"):
        return True
    if _has_clear_final_ending(ko_s):
        return False
    tokens = _ko_tokens(ko_s)
    if len(tokens) < 5 and not _has_clear_final_ending(ko_s):
        return True
    if ko_s.endswith("...") or ko_s.endswith("…"):
        return True
    if _KO_OPEN_END.search(ko_s):
        return True
    if es_s.endswith("...") or es_s.endswith("…") or es_s.endswith(","):
        return True
    return False


def classify_single_fragment(ko: str, es: str) -> str:
    """Quality label for a single-fragment release. Heuristic only."""
    ko_s = (ko or "").strip()
    es_s = (es or "").strip()
    if "..." in es_s or "…" in es_s or ko_s.endswith("...") or ko_s.endswith("…"):
        return "ellipsis"
    tokens = _ko_tokens(ko_s)
    last = tokens[-1] if tokens else ""
    if last in _CONNECTOR_KO or es_s.lower().rstrip(".!?").endswith(
        (" y", " pero", " porque", " y,", " pero,")
    ):
        return "connector"
    if _KO_OPEN_END.search(ko_s) or (
        len(tokens) < 5 and not _has_clear_final_ending(ko_s)
    ):
        if len(tokens) <= 2:
            return "noun_only"
        return "unfinished"
    return "ok"


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = min(len(ordered) - 1, max(0, round((p / 100.0) * (len(ordered) - 1))))
    return float(ordered[idx])


def window_stats_from_events(events: list[dict]) -> dict:
    deltas = [
        float(e["b_delta_ms"])
        for e in events
        if e.get("b_arrived") and e.get("b_delta_ms") is not None
    ]
    latencies = [
        float(e["release_latency_ms"])
        for e in events
        if e.get("release_latency_ms") is not None
    ]
    reasons: dict[str, int] = {}
    kinds: dict[str, int] = {}
    singles = 0
    for e in events:
        if e.get("action") != "release":
            continue
        r = str(e.get("release_reason") or "unknown")
        reasons[r] = reasons.get(r, 0) + 1
        if e.get("released_as_single"):
            singles += 1
            k = str(e.get("single_kind") or "ok")
            kinds[k] = kinds.get(k, 0) + 1
    return {
        "event_count": len(events),
        "a_to_b_p50_ms": _percentile(deltas, 50),
        "a_to_b_p75_ms": _percentile(deltas, 75),
        "a_to_b_p90_ms": _percentile(deltas, 90),
        "a_to_b_p95_ms": _percentile(deltas, 95),
        "release_latency_p50_ms": _percentile(latencies, 50),
        "release_latency_p75_ms": _percentile(latencies, 75),
        "release_latency_p90_ms": _percentile(latencies, 90),
        "release_latency_p95_ms": _percentile(latencies, 95),
        "release_reasons": reasons,
        "released_as_single": singles,
        "single_kinds": kinds,
    }


def _as_fragment_items(frags: list[V2Fragment]) -> list[FragmentItem]:
    return [FragmentItem(item_id=f.item_id, ko=f.ko, es=f.es) for f in frags]


async def recombine_for_output_v2(
    items: list[FragmentItem],
    *,
    context: dict | None = None,
    sermon_mode: bool = False,
    on_usage: OnUsage | None = None,
    hold_allowed: bool = True,
) -> V2Decision:
    n = len(items)
    if n == 0:
        return V2Decision(es="", consume=0, hold=False)
    joined = _fallback_join(items)
    preview = joined
    had_incierto = _joined_has_incierto(joined)

    numbered = "\n".join(f"{i + 1}. {it.es.strip()}" for i, it in enumerate(items))
    user_content = (
        f"Fragmentos (N={n}, hold_allowed={str(hold_allowed).lower()}):\n{numbered}"
    )
    system = V2_RECOMBINE_SYSTEM
    if sermon_mode and context:
        try:
            ctx_block = format_context_for_recombine(context)
            if ctx_block.strip():
                system = f"{system}\n\n{ctx_block}"
        except Exception:
            logger.debug("v2 recombine context skipped", exc_info=True)

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
            **chat_completion_extra(
                model,
                800,
                reasoning="none",
                temperature=config.RECOMBINE_TEMPERATURE,
            ),
        )
        raw = response.choices[0].message.content or "{}"
        if on_usage:
            prompt, completion = usage_from_response(response)
            if prompt or completion:
                on_usage(prompt, completion)
        data = json.loads(raw)
        hold = bool(data.get("hold"))
        consume_raw = data.get("consume", 0)
        try:
            consume = int(consume_raw)
        except (TypeError, ValueError):
            consume = 0
        es = str(data.get("es") or data.get("text") or "").strip()
        flags = [str(f) for f in (data.get("flags") or [])]
        if hold and not hold_allowed:
            hold = False
        if hold:
            consume = 0
            es = ""
        else:
            if consume < 1:
                consume = n if n else 0
            consume = max(1, min(n, consume))
            source = _fallback_join(items[:consume])
            anchor = _needs_recombine_llm(
                source,
                _ko_summary_for_anchor(items[:consume], context),
                context,
                sermon_mode,
            )
            if es and not _is_faithful(source, es, anchor_repair=anchor):
                return V2Decision(
                    es=_strip_incierto_markers(source),
                    consume=consume,
                    flags=flags,
                    used_llm=True,
                    repair_rejected=True,
                    anchor_repair=anchor,
                    had_incierto=had_incierto,
                    joined_preview=preview,
                )
            if not es:
                es = source
            return V2Decision(
                es=_strip_incierto_markers(es),
                consume=consume,
                hold=False,
                flags=flags,
                used_llm=True,
                anchor_repair=anchor,
                had_incierto=had_incierto,
                joined_preview=preview,
            )
        return V2Decision(
            es="",
            consume=0,
            hold=True,
            flags=flags,
            used_llm=True,
            had_incierto=had_incierto,
            joined_preview=preview,
        )
    except Exception as e:
        logger.error("v2 recombine LLM failed: %s", e)
        return V2Decision(
            es=_strip_incierto_markers(joined),
            consume=1,
            hold=False,
            fallback=True,
            had_incierto=had_incierto,
            joined_preview=preview,
        )


class OutputComposerV2:
    """Short grace + sliding consume. Log-only; never drives live captions."""

    def __init__(
        self,
        on_release: OnRelease,
        on_usage: OnUsage | None = None,
        on_event: Callable[[dict], None] | None = None,
    ):
        self._on_release = on_release
        self._on_usage = on_usage
        self._on_event = on_event
        self._window: list[V2Fragment] = []
        self._queued: list[V2Fragment] = []
        self._lock = asyncio.Lock()
        self._recombine_sem = asyncio.Semaphore(1)
        self._running = False
        self._deciding = False
        self._grace_task: asyncio.Task | None = None
        self._grace_token = 0
        self._seq = 0
        self._in_flight = 0
        self._context: dict | None = None
        self._sermon_mode = False
        self.events: list[dict] = []
        self.recombine_calls = 0

    def set_context(self, context: dict | None) -> None:
        self._context = context

    def set_sermon_mode(self, sermon_mode: bool) -> None:
        self._sermon_mode = sermon_mode

    def pending_count(self) -> int:
        return len(self._window) + len(self._queued) + self._in_flight

    async def run(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(0.25)

    def stop_sync(self) -> None:
        self._running = False
        self._cancel_grace()
        self._window.clear()
        self._queued.clear()

    def _cancel_grace(self) -> None:
        self._grace_token += 1
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
        self._grace_task = None

    def _arm_grace_locked(self) -> None:
        if not self._window:
            return
        self._cancel_grace()
        first = self._window[0]
        token = self._grace_token
        seq = first.arrive_seq
        ms = first.grace_ms
        loop = asyncio.get_running_loop()
        self._grace_task = loop.create_task(self._grace_fire(token, seq, ms))

    async def _grace_fire(self, token: int, seq: int, ms: int) -> None:
        try:
            await asyncio.sleep(max(0.0, ms / 1000.0))
        except asyncio.CancelledError:
            return
        async with self._lock:
            if token != self._grace_token:
                return
            if not self._window or self._window[0].arrive_seq != seq:
                return
            if self._deciding:
                return
            await self._emit_prefix_locked(
                n=1,
                reason="grace_expired",
                decision=None,
                b_arrived=False,
                b_delta_ms=None,
            )

    def _new_frag(self, item_id: str, ko: str, es: str) -> V2Fragment:
        self._seq += 1
        wait = should_wait_for_lookahead(ko, es)
        grace = (
            config.OUTPUT_V2_GRACE_INCOMPLETE_MS
            if wait
            else config.OUTPUT_V2_GRACE_COMPLETE_MS
        )
        return V2Fragment(
            item_id=item_id,
            ko=ko.strip(),
            es=es.strip(),
            arrive_seq=self._seq,
            translated_at=time.monotonic(),
            should_wait=wait,
            grace_ms=grace,
        )

    async def add(self, item_id: str, ko: str, es: str) -> None:
        text = (es or "").strip()
        if not text:
            return
        async with self._lock:
            frag = self._new_frag(item_id, ko, text)
            if self._deciding:
                self._queued.append(frag)
                return
            await self._admit_locked(frag)

    async def _admit_locked(self, frag: V2Fragment) -> None:
        if not self._window:
            self._window.append(frag)
            self._arm_grace_locked()
            return
        self._cancel_grace()
        self._window.append(frag)
        max_n = min(MAX_WINDOW, config.OUTPUT_V2_MAX_WINDOW)
        forced = len(self._window) >= max_n
        await self._decide_locked(forced_max=forced)

    async def _drain_queued_locked(self) -> None:
        while self._queued and not self._deciding:
            nxt = self._queued.pop(0)
            await self._admit_locked(nxt)

    async def _decide_locked(self, *, forced_max: bool) -> None:
        if len(self._window) < 2:
            self._arm_grace_locked()
            return
        items = list(self._window)
        a = items[0]
        b = items[1]
        b_delta = int((b.translated_at - a.translated_at) * 1000)
        self._deciding = True
        self._in_flight += 1
        hold_allowed = (not forced_max) and len(items) < MAX_WINDOW
        self._lock.release()
        t0 = time.perf_counter()
        async with self._recombine_sem:
            self.recombine_calls += 1
            try:
                decision = await recombine_for_output_v2(
                    _as_fragment_items(items),
                    context=self._context,
                    sermon_mode=self._sermon_mode,
                    on_usage=self._on_usage,
                    hold_allowed=hold_allowed,
                )
            except Exception as e:
                logger.error("v2 decide error: %s", e)
                decision = V2Decision(
                    es=items[0].es,
                    consume=1,
                    fallback=True,
                    joined_preview=_fallback_join(_as_fragment_items(items)),
                )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        await self._lock.acquire()
        self._in_flight -= 1
        self._deciding = False
        if decision.hold and hold_allowed:
            self._record_event(
                {
                    "a_item_id": a.item_id,
                    "should_wait": a.should_wait,
                    "grace_ms": a.grace_ms,
                    "b_arrived": True,
                    "b_delta_ms": b_delta,
                    "action": "hold",
                    "consume": 0,
                    "release_reason": "",
                    "latency_recombine": latency_ms,
                }
            )
            self._arm_grace_locked()
            await self._drain_queued_locked()
            return
        consume = decision.consume if decision.consume >= 1 else 1
        consume = min(consume, len(self._window), len(items))
        reason = "fallback" if decision.fallback else "recombine"
        if forced_max:
            reason = "forced_max_window" if reason == "recombine" else reason
        await self._emit_prefix_locked(
            n=consume,
            reason=reason,
            decision=decision,
            b_arrived=True,
            b_delta_ms=b_delta,
            latency_recombine=latency_ms,
        )
        if self._window:
            self._arm_grace_locked()
        await self._drain_queued_locked()

    async def _emit_prefix_locked(
        self,
        n: int,
        reason: str,
        decision: V2Decision | None,
        b_arrived: bool,
        b_delta_ms: int | None,
        latency_recombine: int = 0,
    ) -> None:
        if n < 1 or not self._window:
            return
        n = min(n, len(self._window))
        batch = self._window[:n]
        self._window = self._window[n:]
        now = time.monotonic()
        t0 = min(f.translated_at for f in batch)
        es = (
            decision.es.strip()
            if decision and decision.es.strip()
            else _strip_incierto_markers(_fallback_join(_as_fragment_items(batch)))
        )
        flags = list(decision.flags) if decision else []
        items = _as_fragment_items(batch)
        a = batch[0]
        single = n == 1
        kind = classify_single_fragment(a.ko, es) if single else ""
        latency = int((now - t0) * 1000)
        item = ReleaseItem(
            batch_id=a.item_id,
            es=es,
            item_ids=[f.item_id for f in batch],
            ko_summary=_ko_summary(items),
            recombine_flags=flags,
            repair_rejected=bool(decision and decision.repair_rejected),
            anchor_repair=bool(decision and decision.anchor_repair),
            had_incierto=bool(decision and decision.had_incierto),
            ko_corrected=_ko_summary_for_anchor(
                items, self._context if self._sermon_mode else None
            ),
            joined_preview=(decision.joined_preview if decision else _fallback_join(items)),
            stt_repair=False,
            latency_recombine=latency_recombine,
            release_reason=reason,
            consume=n,
            should_wait=a.should_wait,
            grace_ms=a.grace_ms,
            b_arrived=b_arrived,
            b_delta_ms=b_delta_ms,
            released_as_single=single,
            single_kind=kind,
            release_latency_ms=latency,
            translated_at_mono=t0,
        )
        self._record_event(
            {
                "a_item_id": a.item_id,
                "fragment_ids": item.item_ids,
                "should_wait": a.should_wait,
                "grace_ms": a.grace_ms,
                "b_arrived": b_arrived,
                "b_delta_ms": b_delta_ms,
                "action": "release",
                "consume": n,
                "release_reason": reason,
                "released_as_single": single,
                "single_kind": kind,
                "release_latency_ms": latency,
                "latency_recombine": latency_recombine,
            }
        )
        self._lock.release()
        try:
            await self._on_release(item)
        finally:
            await self._lock.acquire()

    def _record_event(self, event: dict) -> None:
        self.events.append(event)
        if self._on_event:
            self._on_event(event)

    async def drain(self, timeout: float = 180.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        async with self._lock:
            self._cancel_grace()
            while self._queued:
                self._window.append(self._queued.pop(0))
            if len(self._window) >= 2:
                await self._decide_locked(forced_max=True)
            elif self._window:
                await self._emit_prefix_locked(
                    n=1,
                    reason="drain",
                    decision=None,
                    b_arrived=False,
                    b_delta_ms=None,
                )
        idle = 0
        while asyncio.get_running_loop().time() < deadline:
            if self.pending_count() == 0:
                idle += 1
                if idle >= 4:
                    return True
            else:
                idle = 0
            await asyncio.sleep(0.05)
        logger.warning("OutputComposerV2 drain timeout (%d pending)", self.pending_count())
        return False
