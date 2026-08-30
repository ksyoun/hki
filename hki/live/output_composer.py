"""Batch recombine translations then pace caption+TTS release."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Awaitable, Callable

from hki import config
from hki.live.context import (
    ANCHOR_PRIORITY_RULES,
    format_context_for_recombine,
    normalize_critical_sentences,
    normalize_ko_stt,
)
from hki.live.ko_endings import (
    fragment_looks_open_ko,
    has_clear_final_ending,
)
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.release_pacer import ReleaseItem, ReleasePacer, release_interval_ms
from hki.live.trace_schema import ItemTiming, merge_item_timings, ms_since
from hki.live.translate import TranslateStats

logger = logging.getLogger(__name__)

OnRelease = Callable[["ReleaseItem"], Awaitable[None]]
OnUsage = Callable[[int, int], None]

RECOMBINE_SYSTEM = (
    """Eres editor de texto para subtítulos y TTS en iglesia argentina.
Recibes fragmentos YA TRADUCIDOS al español, junto con un contexto que incluye critical_sentences
(frases ancla del manuscrito en español) y key_names.
Tu trabajo es unirlos en un texto natural para leer en voz alta y mostrar como subtítulo.

Reglas estrictas:
- Usa ÚNICAMENTE las palabras e ideas de los fragmentos; NO inventes contenido nuevo
- NO agregues explicaciones, saludos, comentarios ni contexto que no esté en los fragmentos
- NO cambies el significado; no «mejores» el sermón
- Si un fragmento termina en puntos suspensivos (…), coma o conjunción (pero, y, porque, que),
  unilo OBLIGATORIAMENTE con el siguiente. No lo publiques como subtítulo cerrado aparte
- Puede unir con conectores mínimos, quitar repeticiones obvias, puntuación para oralidad
- Mantener referencias bíblicas NVI exactas (Mateo 1:1)
- Mantener el registro formal (usted/ustedes) que ya viene en los fragmentos. NO convierta a
  voseo (vos, tenés, podés) — el registro lo define la traducción de origen; su trabajo es unir,
  no recalibrar el tono
- Si un fragmento está marcado [INCIERTO] o el sujeto de la oración parece faltar (ej. "vio a X y no
  tiene confianza" sin quedar claro quién), y el contenido coincide temáticamente con una
  critical_sentence del contexto: puede corregir la gramática mínima necesaria (reponer un sujeto,
  arreglar una oración rota) para que coincida con el sentido de esa critical_sentence — pero SIN
  agregar ideas, ejemplos o datos que no estén ni en los fragmentos ni en la critical_sentence
  correspondiente
- Esta corrección es una excepción limitada: si no hay una critical_sentence clara que respalde el
  cambio, deje el fragmento como está aunque suene extraño — no adivine
- Solo JSON: {"text": "…", "flags": ["lista de correcciones hechas via critical_sentences, si hubo"]}

"""
    + ANCHOR_PRIORITY_RULES
)

INCIERTO_MARKER = "[INCIERTO]"
_ES_OPEN_CONJ = (" y", " pero", " porque", " que", " y,", " pero,")
ANCHOR_KO_MIN_LEN = 8
ANCHOR_KO_MIN_FRAG_LEN = 6
ANCHOR_KO_SIM_THRESHOLD = 0.55
ANCHOR_KO_SIM_STRICT = 0.6
ANCHOR_KO_LONG = 12


@dataclass
class FragmentItem:
    item_id: str
    ko: str
    es: str
    timing: ItemTiming = field(default_factory=ItemTiming.fallback_now)
    used_llm_translate: bool = False
    translate_llm_ms: int = 0
    tokens_translate_in: int = 0
    tokens_translate_out: int = 0
    received_mono: float = field(default_factory=time.monotonic)
    flush_release_reason: str = "closed_immediate"
    hold_ms: int = 0
    hold_reason: str = ""
    fragment_open_final: bool = False


@dataclass
class RecombineResult:
    text: str
    repair_rejected: bool = False
    anchor_repair: bool = False
    flags: list[str] = field(default_factory=list)
    joined_preview: str = ""
    had_incierto: bool = False
    used_llm: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    llm_ms: int = 0


def _fallback_join(items: list[FragmentItem]) -> str:
    return " ".join(i.es.strip() for i in items if i.es.strip())


def fragment_looks_open_es(es: str) -> bool:
    es_s = (es or "").strip()
    if es_s.endswith("...") or es_s.endswith("…") or es_s.endswith(","):
        return True
    es_tail = es_s.lower().rstrip(".!?")
    if es_tail.endswith(_ES_OPEN_CONJ):
        return True
    return False


def fragment_looks_open(ko: str, es: str = "") -> bool:
    """Classic wrapper: KO open wins; KO final ignores ES; else ES extras."""
    if fragment_looks_open_ko(ko):
        return True
    if has_clear_final_ending(ko):
        return False
    return fragment_looks_open_es(es)


def _strip_incierto_markers(text: str) -> str:
    cleaned = re.sub(r"\s*\[INCIERTO\]\s*", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _joined_has_incierto(joined: str) -> bool:
    return INCIERTO_MARKER.lower() in joined.lower()


def _text_word_set(text: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"\w+", text, re.UNICODE)
        if len(w) > 3
    }


def _ko_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _ko_has_key_name_signal(ko_text: str, context: dict) -> bool:
    for item in context.get("key_names") or []:
        canonical = str(item.get("ko") or "").strip()
        if canonical and canonical in ko_text:
            return True
        for variant in item.get("stt_variants") or []:
            v = str(variant).strip()
            if v and v in ko_text:
                return True
    return False


def _matches_critical_sentence_ko(ko_joined: str, context: dict | None) -> bool:
    """Match KO transcript fragments to manuscript anchors (not ES word overlap)."""
    if not context or not ko_joined.strip():
        return False
    ko_text = ko_joined.strip()
    if len(ko_text) < ANCHOR_KO_MIN_FRAG_LEN:
        return False
    has_keys = _ko_has_key_name_signal(ko_text, context)
    for item in normalize_critical_sentences(context.get("critical_sentences")):
        anchor_ko = (item.get("ko") or "").strip()
        if len(anchor_ko) < ANCHOR_KO_MIN_LEN:
            continue
        if anchor_ko in ko_text or ko_text in anchor_ko:
            if has_keys or len(anchor_ko) >= ANCHOR_KO_LONG:
                return True
        sim = _ko_similarity(ko_text, anchor_ko)
        if sim >= ANCHOR_KO_SIM_STRICT:
            return True
        if sim >= ANCHOR_KO_SIM_THRESHOLD and has_keys:
            return True
    return False


def _needs_recombine_llm(
    joined_es: str,
    ko_joined: str,
    context: dict | None,
    sermon_mode: bool,
) -> bool:
    if _joined_has_incierto(joined_es):
        return True
    if sermon_mode and context and _matches_critical_sentence_ko(ko_joined, context):
        return True
    return False


def _ko_summary(items: list[FragmentItem]) -> str:
    return " ".join(i.ko.strip() for i in items if i.ko.strip())


def _ko_summary_for_anchor(
    items: list[FragmentItem],
    context: dict | None,
) -> str:
    parts: list[str] = []
    for it in items:
        ko = it.ko.strip()
        if not ko:
            continue
        if context:
            ko = normalize_ko_stt(ko, context)
        parts.append(ko)
    return " ".join(parts)


def _is_faithful(
    source: str,
    polished: str,
    *,
    anchor_repair: bool = False,
) -> bool:
    src = source.strip()
    pol = polished.strip()
    if not pol:
        return False
    max_extra = 40
    max_ratio = 1.2
    min_overlap = 0.5
    if anchor_repair:
        max_extra = 200
        max_ratio = 1.8
        min_overlap = 0.35
    if len(pol) > max(int(len(src) * max_ratio), len(src) + max_extra):
        return False
    src_words = {w.lower() for w in re.findall(r"\w+", src, re.UNICODE) if len(w) > 3}
    if not src_words:
        return True
    pol_lower = pol.lower()
    overlap = sum(1 for w in src_words if w in pol_lower)
    return overlap >= len(src_words) * min_overlap


def release_item_from_batch(
    batch: list[FragmentItem],
    result: RecombineResult,
    *,
    context: dict | None = None,
    fallback: bool = False,
    release_reason: str = "",
    hold_ms: int = 0,
    hold_reason: str = "",
    recombine_id: str = "",
) -> ReleaseItem:
    original = _ko_summary(batch)
    corrected = _ko_summary_for_anchor(batch, context)
    item_ids = [it.item_id for it in batch]
    last = batch[-1] if batch else None
    merged = merge_item_timings([it.timing for it in batch]) if batch else ItemTiming.fallback_now()
    reason = release_reason or (last.flush_release_reason if last else "closed_immediate")
    if fallback:
        reason = "recombine_fallback"
    used_recombine = bool(result.used_llm)
    recombine_ms = int(result.llm_ms or 0) if used_recombine else 0
    return ReleaseItem(
        batch_id=item_ids[0] if item_ids else "",
        es=result.text.strip(),
        item_ids=item_ids,
        ko_summary=original,
        recombine_flags=list(result.flags),
        repair_rejected=result.repair_rejected,
        anchor_repair=result.anchor_repair,
        had_incierto=result.had_incierto,
        ko_corrected=corrected,
        joined_preview=result.joined_preview,
        stt_repair=bool(corrected) and corrected != original,
        latency_recombine=recombine_ms,
        release_reason=reason,
        original_stt=original,
        fragment_count=len(item_ids),
        unit_index=0,
        unit_count=1,
        fragment_indexes=list(range(len(item_ids))),
        recombine_id=recombine_id or uuid.uuid4().hex[:12],
        t_audio_start=merged.t_audio_start,
        t_audio_start_source=merged.t_audio_start_source,
        t_stt_final=merged.t_stt_final,
        t_audio_start_mono=merged.t_audio_start_mono,
        t_stt_final_mono=merged.t_stt_final_mono,
        used_llm_translate=any(it.used_llm_translate for it in batch),
        translate_llm_ms=sum(int(it.translate_llm_ms or 0) for it in batch),
        used_llm_recombine=used_recombine,
        recombine_llm_ms=recombine_ms,
        hold_ms=int((last.hold_ms if last is not None else 0) or hold_ms),
        hold_reason=(last.hold_reason if last is not None else "") or hold_reason,
        fragment_open_final=(
            fragment_looks_open(last.ko, last.es) if last is not None else False
        ),
        tokens_translate_in=sum(int(it.tokens_translate_in or 0) for it in batch),
        tokens_translate_out=sum(int(it.tokens_translate_out or 0) for it in batch),
        tokens_recombine_in=int(result.tokens_in or 0) if used_recombine else 0,
        tokens_recombine_out=int(result.tokens_out or 0) if used_recombine else 0,
        pipeline="classic",
        last_fragment_at_mono=last.received_mono if last else 0.0,
        first_fragment_at_mono=batch[0].received_mono if batch else 0.0,
    )


async def recombine_for_output(
    items: list[FragmentItem],
    *,
    context: dict | None = None,
    sermon_mode: bool = False,
    on_usage: OnUsage | None = None,
) -> RecombineResult:
    if not items:
        return RecombineResult(text="")
    joined = _fallback_join(items)
    ko_joined = _ko_summary_for_anchor(items, context)
    if (
        len(items) == 1
        and not config.OUTPUT_ALWAYS_RECOMBINE
        and not _needs_recombine_llm(joined, ko_joined, context, sermon_mode)
    ):
        return RecombineResult(
            text=_strip_incierto_markers(joined),
            joined_preview=joined,
            had_incierto=_joined_has_incierto(joined),
        )

    numbered = "\n".join(
        f"{i + 1}. {it.es.strip()}" for i, it in enumerate(items)
    )
    user_content = f"Fragmentos (solo unir, sin inventar):\n{numbered}"
    system = RECOMBINE_SYSTEM
    if sermon_mode and context:
        try:
            ctx_block = format_context_for_recombine(context)
            if ctx_block.strip():
                system = f"{RECOMBINE_SYSTEM}\n\n{ctx_block}"
        except Exception:
            logger.debug("Context injection for recombine skipped", exc_info=True)

    anchor_repair = _needs_recombine_llm(joined, ko_joined, context, sermon_mode)
    had_incierto = _joined_has_incierto(joined)
    model = config.OUTPUT_PREP_MODEL or config.FINAL_MODEL
    client = get_async_openai()
    try:
        t0 = time.perf_counter()
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
        llm_ms = max(0, int((time.perf_counter() - t0) * 1000))
        raw = response.choices[0].message.content or "{}"
        prompt, completion = usage_from_response(response)
        if on_usage and (prompt or completion):
            on_usage(prompt, completion)
        data = json.loads(raw)
        text = str(data.get("text") or "").strip()
        flags = [str(f) for f in (data.get("flags") or [])]
        if flags:
            logger.info("Recombine anchor flags: %s", flags)
        if not anchor_repair:
            anchor_repair = bool(flags) or _joined_has_incierto(joined)
        if text and _is_faithful(joined, text, anchor_repair=anchor_repair):
            return RecombineResult(
                text=_strip_incierto_markers(text),
                flags=flags,
                anchor_repair=anchor_repair,
                joined_preview=joined,
                had_incierto=had_incierto,
                used_llm=True,
                tokens_in=prompt,
                tokens_out=completion,
                llm_ms=llm_ms,
            )
        if text:
            logger.warning(
                "Recombine rejected (unfaithful) joined=%s polished=%s anchor=%s",
                joined[:80],
                text[:80],
                anchor_repair,
            )
            return RecombineResult(
                text=_strip_incierto_markers(joined),
                repair_rejected=True,
                anchor_repair=anchor_repair,
                flags=flags,
                joined_preview=joined,
                had_incierto=had_incierto,
                used_llm=True,
                tokens_in=prompt,
                tokens_out=completion,
                llm_ms=llm_ms,
            )
    except Exception as e:
        logger.error("Recombine LLM failed: %s", e)
    return RecombineResult(
        text=_strip_incierto_markers(joined),
        joined_preview=joined,
        anchor_repair=anchor_repair,
        had_incierto=had_incierto,
    )


class OutputComposer:
    """Batch fragments → recombine → paced release for captions and TTS."""

    def __init__(self, on_release: OnRelease, on_usage: OnUsage | None = None):
        self._pending: list[FragmentItem] = []
        self._work_queue: asyncio.Queue[list[FragmentItem]] = asyncio.Queue()
        self._running = False
        self._recombine_in_flight = 0
        self._timeout_task: asyncio.Task | None = None
        self._batch_size = max(1, min(3, config.OUTPUT_BATCH_SIZE))
        self._timeout_sec = config.OUTPUT_TIMEOUT_MS / 1000.0
        self._incomplete_timeout_sec = config.OUTPUT_INCOMPLETE_TIMEOUT_MS / 1000.0
        self._context: dict | None = None
        self._sermon_mode = False
        self._on_usage = on_usage
        self._pacer = ReleasePacer(on_release, depth_fn=self._effective_depth)
        self._holding_open = False

    def set_context(self, context: dict | None) -> None:
        self._context = context

    def set_sermon_mode(self, sermon_mode: bool) -> None:
        self._sermon_mode = sermon_mode

    def pending_count(self) -> int:
        return (
            len(self._pending)
            + self._work_queue.qsize()
            + self._recombine_in_flight
            + self._pacer.pending_count()
        )

    def release_queue_depth(self) -> int:
        return self._pacer.release_queue_depth()

    def _effective_depth(self) -> int:
        """Release queue + capped upstream batches for catch-up pacing."""
        upstream = self._work_queue.qsize() + self._recombine_in_flight
        pending_batches = (len(self._pending) + self._batch_size - 1) // self._batch_size
        return max(
            1,
            self._pacer.release_queue_depth()
            + min(upstream + pending_batches, 2),
        )

    async def run(self) -> None:
        self._running = True
        await asyncio.gather(self._recombine_worker(), self._pacer.run())

    def stop_sync(self) -> None:
        self._running = False
        self._cancel_timeout()
        self._pending.clear()
        self._pacer.stop_sync()
        self._holding_open = False
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
        self._timeout_task = asyncio.get_running_loop().create_task(
            self._timeout_flush()
        )

    async def _timeout_flush(self) -> None:
        try:
            await asyncio.sleep(self._incomplete_timeout_sec)
            await self._enqueue_pending_batches(
                force_all=True,
                release_reason="incomplete_cap_expired",
                hold_reason="incomplete_timeout_expired",
            )
        except asyncio.CancelledError:
            pass

    def _stamp_batch(
        self,
        batch: list[FragmentItem],
        *,
        release_reason: str,
        hold_reason: str,
    ) -> None:
        last = batch[-1]
        hold_ms = 0
        if release_reason != "closed_immediate":
            hold_ms = ms_since(last.received_mono)
        open_final = fragment_looks_open(last.ko, last.es)
        stamped_hold_reason = "" if release_reason == "closed_immediate" else hold_reason
        for it in batch:
            it.flush_release_reason = release_reason
            it.hold_ms = hold_ms
            it.hold_reason = stamped_hold_reason
            it.fragment_open_final = open_final
        self._holding_open = False

    async def _enqueue_pending_batches(
        self,
        force_all: bool = False,
        *,
        release_reason: str = "closed_immediate",
        hold_reason: str = "",
    ) -> None:
        self._cancel_timeout()
        while len(self._pending) >= self._batch_size:
            batch = self._pending[: self._batch_size]
            self._pending = self._pending[self._batch_size :]
            self._stamp_batch(
                batch, release_reason=release_reason, hold_reason=hold_reason
            )
            await self._work_queue.put(batch)
        if force_all and self._pending:
            batch = list(self._pending)
            self._pending.clear()
            self._stamp_batch(
                batch, release_reason=release_reason, hold_reason=hold_reason
            )
            await self._work_queue.put(batch)

    async def add(
        self,
        item_id: str,
        ko: str,
        es: str,
        stats: TranslateStats | None = None,
    ) -> None:
        text = es.strip()
        if not text:
            return
        stats = stats or TranslateStats()
        timing = stats.timing or ItemTiming.fallback_now()
        self._pending.append(
            FragmentItem(
                item_id=item_id,
                ko=ko.strip(),
                es=text,
                timing=timing,
                used_llm_translate=bool(stats.used_llm_translate),
                translate_llm_ms=int(stats.translate_llm_ms or 0),
                tokens_translate_in=int(stats.tokens_in or 0),
                tokens_translate_out=int(stats.tokens_out or 0),
            )
        )
        last = self._pending[-1]
        opened = fragment_looks_open(last.ko, last.es)
        if len(self._pending) >= self._batch_size:
            reason = "partner_arrived" if self._holding_open else "closed_immediate"
            hold_reason = "batch_wait" if self._holding_open else ""
            await self._enqueue_pending_batches(
                force_all=True, release_reason=reason, hold_reason=hold_reason
            )
            return
        if opened:
            self._holding_open = True
            self._arm_timeout()
        else:
            reason = "partner_arrived" if self._holding_open else "closed_immediate"
            hold_reason = "fragment_looks_open" if self._holding_open else ""
            await self._enqueue_pending_batches(
                force_all=True, release_reason=reason, hold_reason=hold_reason
            )

    async def drain(self, timeout: float = 180.0) -> bool:
        """Flush pending, pace releases quickly, wait until empty."""
        await self._enqueue_pending_batches(
            force_all=True,
            release_reason="drain",
            hold_reason="fragment_looks_open" if self._holding_open else "",
        )
        deadline = asyncio.get_running_loop().time() + timeout
        idle_ticks = 0
        self._pacer.set_fast_drain(True)
        try:
            while asyncio.get_running_loop().time() < deadline:
                recombine_idle = (
                    len(self._pending) == 0
                    and self._work_queue.qsize() == 0
                    and self._recombine_in_flight == 0
                )
                if recombine_idle and self._pacer.pending_count() == 0:
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
            self._pacer.set_fast_drain(False)

    async def _recombine_worker(self) -> None:
        while self._running:
            try:
                batch = await asyncio.wait_for(self._work_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            self._recombine_in_flight += 1
            try:
                result = await recombine_for_output(
                    batch,
                    context=self._context,
                    sermon_mode=self._sermon_mode,
                    on_usage=self._on_usage,
                )
                if not result.text.strip():
                    continue
                ctx = self._context if self._sermon_mode else None
                await self._pacer.enqueue(
                    release_item_from_batch(
                        batch,
                        result,
                        context=ctx,
                    )
                )
            except Exception as e:
                logger.error("OutputComposer recombine worker error: %s", e)
                fallback = _fallback_join(batch)
                if fallback.strip():
                    ctx = self._context if self._sermon_mode else None
                    await self._pacer.enqueue(
                        release_item_from_batch(
                            batch,
                            RecombineResult(
                                text=_strip_incierto_markers(fallback).strip(),
                                joined_preview=fallback,
                            ),
                            context=ctx,
                            fallback=True,
                        )
                    )
            finally:
                self._recombine_in_flight -= 1
