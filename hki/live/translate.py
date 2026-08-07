"""Korean → Argentine Spanish translation on completed transcripts."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from hki import config
from hki.live.context import format_context_for_system
from hki.live.openai_client import get_async_openai

logger = logging.getLogger(__name__)

OnTranslation = Callable[[str, str, str], Awaitable[None]]  # item_id, ko, es

ARGENTINE_RULES = """Eres intérprete de sermones coreanos al español argentino (rioplatense).
Reglas:
- Usá voseo (vos, tenés, podés) en el sermón
- Terminología teológica latinoamericana/argentina
- Referencias bíblicas: nombres NVI en español (Mateo 1:1, Juan 3:16) — nunca inglés
- Si anuncian lectura (ej. «마태복음 1:1 읽겠습니다»): frase natural con voseo + referencia Mateo 1:1
- Si leen el pasaje: texto NVI del contexto, verbatim cuando posible
- Solo la traducción, sin explicaciones"""

GENERAL_SERVICE_RULES = """Modo servicio general (oración, anuncios, saludos — NO sermón):
- NO usar resumen del sermón ni bible_es_nvi del contexto de sesión.
- Oración a Dios (Señor, Padre, Jesús): tono de oración («te pedimos», «gracias, Señor», «Padre»);
  no voseo de sermón al público (no «vos tenés», «podés» a Dios).
- Invitación a orar («함께 기도», «기도하겠습니다»): voseo («oramos juntos», «vamos a orar»).
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
- Canto/alabanza: la transcripción suele ser letra fragmentada, repetición o texto incoherente;
  en ese caso respondé cadena vacía (sin subtítulo). No inventar letra ni sermón.
- Solo la traducción, sin explicaciones."""

FALLBACK_SYSTEM = (
    ARGENTINE_RULES
    + "\n\nNo hay contexto del sermón cargado. Traducí con precisión al español argentino."
)

GENERAL_SYSTEM = (
    "Eres intérprete de eventos de iglesia coreanos al español argentino (rioplatense).\n"
    "Referencias bíblicas mencionadas: nombres NVI en español — nunca inglés.\n\n"
    + GENERAL_SERVICE_RULES
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
            sermon_mode and context and context.get("sermon_summary")
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
    ):
        self.on_translation = on_translation
        self._context = context
        self._sermon_mode = sermon_mode
        self._client = get_async_openai()
        self._history: list[dict] = []
        self._final_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
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
        return f"{ARGENTINE_RULES}\n\n{ctx_block}"

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

    def _emit_translation(self, es: str) -> str | None:
        text = es.strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            return None
        return text

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        await self._final_queue.put((item_id, ko_text))

    async def _translate(self, item_id: str, ko_text: str) -> None:
        if not ko_text.strip():
            return
        if config.TRANSLATION_LOG_PROMPTS:
            self._log_system_prompt("translate")
        try:
            messages = [
                {"role": "system", "content": self._system_prompt()},
                *self._build_history_messages(config.FINAL_HISTORY_LINES),
                {"role": "user", "content": ko_text},
            ]
            response = await self._client.chat.completions.create(
                model=config.FINAL_MODEL,
                messages=messages,
                max_tokens=512,
                temperature=0.1,
            )
            es = response.choices[0].message.content or ""
            emitted = self._emit_translation(es)
            if emitted:
                self._history.append({"ko": ko_text, "es": emitted})
                if len(self._history) > 14:
                    self._history = self._history[-14:]
                await self.on_translation(item_id, ko_text, emitted)
        except Exception as e:
            logger.error("Translation error: %s", e)

    def _build_history_messages(self, n: int) -> list[dict]:
        msgs = []
        for entry in self._history[-n:]:
            msgs.append({"role": "user", "content": entry["ko"]})
            msgs.append({"role": "assistant", "content": entry["es"]})
        return msgs

    async def _final_worker(self) -> None:
        while self._running:
            try:
                item_id, ko_text = await asyncio.wait_for(
                    self._final_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            self._in_flight += 1
            try:
                await self._translate(item_id, ko_text)
            finally:
                self._in_flight -= 1

    async def run(self) -> None:
        self._running = True
        await self._final_worker()

    def stop(self) -> None:
        self._running = False
