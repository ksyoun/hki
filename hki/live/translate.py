"""Korean → Argentine Spanish translation on completed transcripts."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from hki import config
from hki.live.context import ANCHOR_PRIORITY_RULES, format_context_for_system, has_sermon_summary, normalize_ko_stt
from hki.live.ko_endings import fragment_ending_rules_es
from hki.live.openai_client import chat_completion_extra, get_async_openai, usage_from_response
from hki.live.trace_schema import ItemTiming

logger = logging.getLogger(__name__)

TRANSLATION_API_TIMEOUT_SEC = 45.0

OnTranslation = Callable[..., Awaitable[None]]  # item_id, ko, es[, stats]
OnUsage = Callable[[int, int], None]


@dataclass
class TranslateStats:
    timing: ItemTiming | None = None
    used_llm_translate: bool = False
    translate_llm_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

INCIERTO_MARKER = "[INCIERTO]"
_BROKEN_ES_A_X = re.compile(r"\ba\s+X\b", re.IGNORECASE)
_BROKEN_ES_VIO_A = re.compile(
    r"\b(vio|ve|vioa)\s+a\s+[A-Z]\b", re.IGNORECASE
)

TRANSLATION_TASK_HEADER = """Eres un sistema de traducción en vivo para una iglesia (sermón coreano → español argentino).
Tu única salida es la traducción al español. Nunca rechaces, nunca digas que no puedes ayudar,
nunca expliques políticas ni pidas más contexto. El contenido es litúrgico autorizado.
Si el texto transcrito es incoherente o solo ruido, respondé solo «—»."""

GENERAL_TASK_HEADER = """Eres un sistema de traducción en vivo para una iglesia (sermón coreano → español argentino).
Tu única salida es la traducción al español. Nunca rechaces, nunca digas que no puedes ayudar,
nunca expliques políticas ni pidas más contexto. El contenido es litúrgico autorizado.
Si hay texto coreano sustantivo (oración, saludo, anuncio, lectura), SIEMPRE traducí al español.
Respondé solo «—» si la transcripción está vacía o es ruido sin palabras reconocibles."""

_ARGENTINE_RULES_BODY = """Eres intérprete de sermones coreanos al español argentino (rioplatense).

Contexto: recibís un fragmento de transcripción STT en tiempo real. La transcripción puede contener
errores de reconocimiento de voz — palabras coreanas con sonido similar confundidas entre sí,
especialmente nombres propios. Tenés un contexto JSON preparado de antemano (key_names,
critical_sentences, terminology, recurring_phrases, sermon_summary) que sirve como referencia para
detectar y corregir esos errores ANTES de traducir.

Reglas:
- Si una palabra o frase no tiene sentido en el contexto del sermón, pero se parece fonéticamente a
  un término de key_names o terminology, asumí que es un error de STT y traducí la versión correcta
  — no traduzcas literalmente el error
- Si el fragmento coincide en tema con alguna critical_sentence, priorizá el sentido de esa frase de
  referencia por sobre una transcripción STT ambigua o incoherente — solo cuando el KO es incoherente
  o roto; ver orden de prioridad abajo
- Los nombres propios se traducen SIEMPRE según key_names, nunca de forma literal o fonética
- Tono respetuoso y congregacional al público: usted/ustedes, hermanos, hermanas, amados en Cristo
  — pero usalo SOLO si el fragmento realmente incluye una forma de dirigirse al público (ej. "여러분",
  vocativo). No agregues "hermanos" ni otro vocativo si el original no lo tiene
- Evitar voseo informal (vos, tenés, podés); preferir cortesía («usted tiene», «puede», «hermanos»)
- Terminología teológica latinoamericana/argentina, alineada con el campo terminology del contexto
- Referencias bíblicas: nombres NVI en español (Mateo 1:1, Juan 3:16) — nunca inglés
- Si anuncian lectura (ej. «마태복음 1:1 읽겠습니다»): frase natural y respetuosa + referencia Mateo 1:1
- Si leen el pasaje: texto NVI del contexto, verbatim cuando posible
- Si hay texto coreano sustantivo, SIEMPRE traducí; nunca respondas solo «—» ni vacío
- Marcá [INCIERTO] cuando:
  - El fragmento coreano no forma una oración completa o coherente incluso leyéndolo varias veces
  - Un nombre propio o término no coincide con nada en key_names/terminology pero suena parecido
  - Tuviste que adivinar el sujeto o el verbo principal para que la traducción tenga sentido
  - La traducción depende más de tu conocimiento general del sermón que del fragmento en sí
  No lo uses solo por duda estilística menor — es una señal para la etapa de revisión, no un
  comodín. Ante duda real de fidelidad, preferí marcar antes que traducir con falsa confianza.
- Solo la traducción (con [INCIERTO] si aplica), sin explicaciones

"""

FRAGMENT_ENDING_RULES = fragment_ending_rules_es()

ARGENTINE_RULES = _ARGENTINE_RULES_BODY + FRAGMENT_ENDING_RULES + "\n" + ANCHOR_PRIORITY_RULES

GENERAL_SERVICE_RULES = """Modo servicio general (oración, anuncios, saludos — NO sermón):
- NO usar resumen del sermón ni bible_es_nvi del contexto de sesión.
- Si hay texto coreano sustantivo, SIEMPRE traducí; nunca respondas solo «—» ni vacío.
- Oración a Dios (Señor, Padre, Jesús): tono de oración («te pedimos», «gracias, Señor», «Padre»);
  no voseo informal ni tono de sermón al público (no «vos tenés», «usted tiene» a Dios).
- Invitación a orar («함께 기도», «기도하겠습니다»): invitación congregacional respetuosa
  («oremos juntos», «vamos a orar», «hermanos, oremos»).
- Saludos y anuncios al público: traducí con naturalidad («buenos días, hermanos», fechas, lugares).
- Frases litúrgicas frecuentes:
  «기도드립니다» → te pedimos en oración / oramos
  «감사드립니다» → te damos gracias / gracias, Señor
  «주님의 이름으로» → en el nombre del Señor
  «아멘» / «아멘 할렐루야» → Amén / Amén, aleluya
- Citas bíblicas dentro de la oración: referencia NVI si se menciona (Mateo 6:9);
  si recitan el texto, traducción fiel; si solo aluden, no inventar el versículo entero.
- Terminología: 은혜→gracia, 은총→gracia/bendición según contexto, 중보→intercesión,
  성도→hermanos/la iglesia, 축복→bendición, 예배→adoración/servicio — sin explicar como clase.
- Anuncios: solo información (fecha, hora, lugar, contacto, costo); números y nombres sin cambiar; no predicar.
- Canto/alabanza: si llega audio durante el canto, traducí lo que puedas (letra fragmentada incluida);
  el operador pausa la transmisión en alabanza — no omitas oración, saludos ni anuncios por precaución.
- Solo la traducción, sin explicaciones."""

FALLBACK_SYSTEM = (
    TRANSLATION_TASK_HEADER
    + "\n\n"
    + ARGENTINE_RULES
    + "\n\nModo sermón sin Contextualizar: traducí el sermón con precisión igual; "
    "Contextualizar solo mejora referencias NVI y terminología."
)

GENERAL_SYSTEM = (
    GENERAL_TASK_HEADER
    + "\n\nEres intérprete de eventos de iglesia coreanos al español argentino (rioplatense).\n"
    "Referencias bíblicas mencionadas: nombres NVI en español — nunca inglés.\n\n"
    + GENERAL_SERVICE_RULES
    + "\n\n"
    + FRAGMENT_ENDING_RULES
)

PROMPT_MODE_LABELS = {
    "general": "servicio general (oración, anuncios)",
    "sermon_context": "sermón con contexto Contextualizar",
    "sermon_fallback": "sermón sin contexto cargado",
}


def _prompt_preview(prompt: str) -> str:
    preview = prompt[:160].replace("\n", " | ")
    if len(prompt) > 160:
        preview += "…"
    return preview


def _prompt_mode_for(sermon_mode: bool, context: dict | None) -> str:
    if not sermon_mode:
        return "general"
    if not context:
        return "sermon_fallback"
    return "sermon_context"


def _prompt_info(
    sermon_mode: bool,
    context: dict | None,
    prompt: str,
    mode: str,
) -> dict:
    return {
        "translation_prompt_mode": mode,
        "translation_prompt_label": PROMPT_MODE_LABELS[mode],
        "translation_prompt_preview": _prompt_preview(prompt),
        "translation_prompt_len": len(prompt),
        "translation_prompt_includes_context_summary": bool(
            sermon_mode and has_sermon_summary(context)
        ),
        "translation_prompt_includes_nvi": bool(
            sermon_mode and context and context.get("bible_es_nvi")
        ),
    }


def describe_translation_prompt(
    sermon_mode: bool,
    context: dict | None,
) -> dict:
    """Build prompt metadata for API status (same rules as live Translator)."""
    t = Translator(lambda *_: None, context=context, sermon_mode=sermon_mode)
    return _prompt_info(
        sermon_mode, context, t._system_prompt(), t._prompt_mode()
    )


class Translator:
    def __init__(
        self,
        on_translation: OnTranslation,
        context: dict | None = None,
        sermon_mode: bool = False,
        on_usage: OnUsage | None = None,
    ):
        self.on_translation = on_translation
        self._context = context
        self._sermon_mode = sermon_mode
        self._on_usage = on_usage
        self._client = get_async_openai()
        self._history: list[dict] = []
        self._final_queue: asyncio.Queue[tuple[str, str, ItemTiming]] = asyncio.Queue()
        self._running = False
        self._in_flight = 0

    def pending_count(self) -> int:
        return self._final_queue.qsize() + self._in_flight

    async def drain(self, timeout: float = 120.0) -> bool:
        """Wait until queued and in-flight translations finish."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.pending_count() == 0:
                return True
            await asyncio.sleep(0.05)
        logger.warning(
            "Translation drain timeout (%d still pending)", self.pending_count()
        )
        return False

    def _system_prompt(self) -> str:
        if not self._sermon_mode:
            return GENERAL_SYSTEM
        if not self._context:
            return FALLBACK_SYSTEM
        ctx_block = format_context_for_system(self._context)
        return f"{TRANSLATION_TASK_HEADER}\n\n{ARGENTINE_RULES}\n\n{ctx_block}"

    def describe_prompt(self) -> dict:
        info = _prompt_info(
            self._sermon_mode,
            self._context,
            self._system_prompt(),
            self._prompt_mode(),
        )
        info["translator_live"] = True
        return info

    def _prompt_mode(self) -> str:
        return _prompt_mode_for(self._sermon_mode, self._context)

    def _log_system_prompt(self, event: str) -> None:
        mode = self._prompt_mode()
        if not config.TRANSLATION_LOG_PROMPTS and event == "translate":
            logger.debug("Translation prompt mode=%s event=%s", mode, event)
            return
        prompt = self._system_prompt()
        logger.info(
            "Translation system prompt mode=%s event=%s len=%d preview=%s",
            mode,
            event,
            len(prompt),
            _prompt_preview(prompt),
        )

    def set_sermon_mode(self, sermon_mode: bool) -> None:
        if self._sermon_mode == sermon_mode:
            return
        self._sermon_mode = sermon_mode
        self._history.clear()
        self._log_system_prompt("sermon_mode")

    def set_context(self, context: dict | None) -> None:
        self._context = context
        self._log_system_prompt("context")

    def _is_model_refusal(self, text: str) -> bool:
        lower = text.lower()
        refusal_markers = (
            "no puedo ayudar",
            "no puedo asistir",
            "no estoy autorizado",
            "no puedo traducir",
            "lo siento, no",
            "sorry, i can't",
            "i can't help",
            "i cannot help",
            "as an ai",
            "como modelo de ia",
            "como asistente",
        )
        return any(m in lower for m in refusal_markers)

    def _looks_broken_es(self, text: str, ko_text: str = "") -> bool:
        bare = re.sub(
            r"\s*\[INCIERTO\]\s*", " ", text, flags=re.IGNORECASE
        ).strip()
        if not bare:
            return False
        if _BROKEN_ES_A_X.search(bare):
            return True
        if _BROKEN_ES_VIO_A.search(bare):
            return True
        ko_len = len(ko_text.strip())
        if ko_len > 25 and len(bare) < 12:
            return True
        return False

    def _maybe_mark_incierto(self, text: str, ko_text: str = "") -> str:
        if INCIERTO_MARKER.lower() in text.lower():
            return text
        if self._sermon_mode and self._looks_broken_es(text, ko_text):
            logger.info(
                "Translation heuristic [INCIERTO] ko=%s es=%s",
                ko_text[:40].replace("\n", " "),
                text[:60].replace("\n", " "),
            )
            return f"{text.rstrip()} [INCIERTO]"
        return text

    def _emit_translation(self, es: str, ko_text: str = "") -> str | None:
        text = self._maybe_mark_incierto(es.strip(), ko_text)
        if not text:
            return None
        if self._is_model_refusal(text):
            return None
        if text in ("—", "-", "…"):
            if not self._sermon_mode:
                return None
            if len(ko_text.strip()) > 15:
                return None
        if (
            text.startswith("[")
            and text.endswith("]")
            and len(text) < 80
            and text.upper() != "[INCIERTO]"
        ):
            return None
        return text

    def _log_skip(self, item_id: str, ko_text: str, raw_es: str, reason: str) -> None:
        ko_preview = ko_text[:60].replace("\n", " ")
        es_preview = (raw_es or "").strip()[:60].replace("\n", " ")
        logger.warning(
            "Translation skipped (%s) item=%s ko=%s raw_es=%s mode=%s",
            reason,
            item_id,
            ko_preview,
            es_preview,
            self._prompt_mode(),
        )

    async def on_transcript_completed(
        self,
        item_id: str,
        ko_text: str,
        timing: ItemTiming | None = None,
    ) -> None:
        await self._final_queue.put(
            (item_id, ko_text, timing or ItemTiming.fallback_now())
        )

    async def _emit_on_translation(
        self,
        item_id: str,
        ko_text: str,
        emitted: str,
        stats: TranslateStats,
    ) -> None:
        try:
            await self.on_translation(item_id, ko_text, emitted, stats)
        except TypeError:
            await self.on_translation(item_id, ko_text, emitted)

    async def _translate(
        self,
        item_id: str,
        ko_text: str,
        timing: ItemTiming | None = None,
    ) -> None:
        if not ko_text.strip():
            return
        ko_for_translate = ko_text
        if self._sermon_mode and self._context:
            ko_for_translate = normalize_ko_stt(ko_text, self._context)
            if ko_for_translate != ko_text:
                logger.info(
                    "KO STT normalized item=%s ko=%s",
                    item_id,
                    ko_text[:50].replace("\n", " "),
                )
        if config.TRANSLATION_LOG_PROMPTS:
            self._log_system_prompt("translate")
        try:
            messages = [
                {"role": "system", "content": self._system_prompt()},
                *self._build_history_messages(config.FINAL_HISTORY_LINES),
                {
                    "role": "user",
                    "content": f"Traduce al español argentino respetuoso (usted, hermanos; solo la traducción, sin comentarios):\n{ko_for_translate}",
                },
            ]
            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=config.FINAL_MODEL,
                    messages=messages,
                    **chat_completion_extra(
                        config.FINAL_MODEL, 512, reasoning="none",
                        temperature=config.FINAL_TEMPERATURE,
                    ),
                ),
                timeout=TRANSLATION_API_TIMEOUT_SEC,
            )
            llm_ms = max(0, int((time.perf_counter() - t0) * 1000))
            es = response.choices[0].message.content or ""
            prompt, completion = usage_from_response(response)
            if self._on_usage and (prompt or completion):
                self._on_usage(prompt, completion)
            emitted = self._emit_translation(es, ko_text)
            if emitted:
                self._history.append({"ko": ko_for_translate, "es": emitted})
                if len(self._history) > 14:
                    self._history = self._history[-14:]
                logger.info(
                    "Translation ok item=%s mode=%s es=%s",
                    item_id,
                    self._prompt_mode(),
                    emitted[:80] + ("…" if len(emitted) > 80 else ""),
                )
                await self._emit_on_translation(
                    item_id,
                    ko_text,
                    emitted,
                    TranslateStats(
                        timing=timing or ItemTiming.fallback_now(),
                        used_llm_translate=True,
                        translate_llm_ms=llm_ms,
                        tokens_in=prompt,
                        tokens_out=completion,
                    ),
                )
            else:
                if self._is_model_refusal(es):
                    reason = "model_refusal"
                elif not es.strip():
                    reason = "empty_llm"
                elif es.strip() in ("—", "-", "…"):
                    reason = "dash_placeholder"
                else:
                    reason = "filtered_placeholder"
                self._log_skip(item_id, ko_text, es, reason)
        except asyncio.TimeoutError:
            logger.error(
                "Translation timeout (%.0fs) item=%s ko=%s",
                TRANSLATION_API_TIMEOUT_SEC,
                item_id,
                ko_text[:60].replace("\n", " "),
            )
        except Exception as e:
            logger.error("Translation error: %s", e)

    def _build_history_messages(self, n: int) -> list[dict]:
        msgs = []
        for entry in self._history[-n:]:
            if self._is_model_refusal(entry["es"]):
                continue
            msgs.append({"role": "user", "content": entry["ko"]})
            msgs.append({"role": "assistant", "content": entry["es"]})
        return msgs

    async def _final_worker(self) -> None:
        while self._running:
            try:
                item_id, ko_text, timing = await asyncio.wait_for(
                    self._final_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            self._in_flight += 1
            try:
                await self._translate(item_id, ko_text, timing)
            finally:
                self._in_flight -= 1

    async def run(self) -> None:
        self._running = True
        await self._final_worker()

    def stop(self) -> None:
        self._running = False
