"""Batch recombine translations then pace caption+TTS release."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
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
from hki.live.openai_client import chat_completion_extra, get_async_openai

logger = logging.getLogger(__name__)

OnRelease = Callable[["ReleaseItem"], Awaitable[None]]

RECOMBINE_SYSTEM = (
    """Eres editor de texto para subtítulos y TTS en iglesia argentina.
Recibes fragmentos YA TRADUCIDOS al español, junto con un contexto que incluye critical_sentences
(frases ancla del manuscrito en español) y key_names.
Tu trabajo es unirlos en un texto natural para leer en voz alta y mostrar como subtítulo.

Reglas estrictas:
- Usa ÚNICAMENTE las palabras e ideas de los fragmentos; NO inventes contenido nuevo
- NO agregues explicaciones, saludos, comentarios ni contexto que no esté en los fragmentos
- NO cambies el significado; no «mejores» el sermón
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


@dataclass
class RecombineResult:
    text: str
    repair_rejected: bool = False
    anchor_repair: bool = False
    flags: list[str] = field(default_factory=list)
    joined_preview: str = ""
    had_incierto: bool = False


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


def _fallback_join(items: list[FragmentItem]) -> str:
    return " ".join(i.es.strip() for i in items if i.es.strip())


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
                result = await recombine_for_output(
                    batch,
                    context=self._context,
                    sermon_mode=self._sermon_mode,
                )
                if not result.text.strip():
                    continue
                item_ids = [it.item_id for it in batch]
                await self._release_queue.put(
                    ReleaseItem(
                        batch_id=item_ids[0],
                        es=result.text.strip(),
                        item_ids=item_ids,
                        ko_summary=_ko_summary(batch),
                        recombine_flags=list(result.flags),
                        repair_rejected=result.repair_rejected,
                        anchor_repair=result.anchor_repair,
                        had_incierto=result.had_incierto,
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
                            es=_strip_incierto_markers(fallback).strip(),
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
                wait_ms = release_interval_ms(depth + 1)
                elapsed = time.monotonic() - self._last_release_mono
                wait_sec = max(0.0, wait_ms / 1000.0 - elapsed)

            if wait_sec > 0:
                await asyncio.sleep(wait_sec)

            self._release_in_flight += 1
            try:
                await self.on_release(item)
                self._last_release_mono = time.monotonic()
            except Exception as e:
                logger.error("OutputComposer release error: %s", e)
            finally:
                self._release_in_flight -= 1
