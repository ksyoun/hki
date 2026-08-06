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

FALLBACK_SYSTEM = (
    ARGENTINE_RULES
    + "\n\nNo hay contexto del sermón cargado. Traducí con precisión al español argentino."
)


class Translator:
    def __init__(
        self,
        on_translation: OnTranslation,
        context: dict | None = None,
    ):
        self.on_translation = on_translation
        self._context = context
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
        if not self._context:
            return FALLBACK_SYSTEM
        ctx_block = format_context_for_system(self._context)
        return f"{ARGENTINE_RULES}\n\n{ctx_block}"

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        await self._final_queue.put((item_id, ko_text))

    async def _translate(self, item_id: str, ko_text: str) -> None:
        if not ko_text.strip():
            return
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
            if es.strip():
                self._history.append({"ko": ko_text, "es": es.strip()})
                if len(self._history) > 14:
                    self._history = self._history[-14:]
                await self.on_translation(item_id, ko_text, es.strip())
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

    def set_context(self, context: dict | None) -> None:
        self._context = context
