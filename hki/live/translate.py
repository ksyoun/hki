"""Korean → Argentine Spanish translation on completed transcripts."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from hki import config

logger = logging.getLogger(__name__)

OnTranslation = Callable[[str, str, str, str], Awaitable[None]]  # item_id, ko, es, tier

ARGENTINE_RULES = """Eres intérprete de sermones coreanos al español argentino (rioplatense).
Reglas:
- Usá voseo (vos, tenés, podés)
- Terminología teológica latinoamericana/argentina
- Coincidí con el texto bíblico en español y los nombres del manuscrito
- Solo la traducción, sin explicaciones"""

PROMPT_TEMPLATE = """{argentine_rules}

Traducí con precisión al español argentino, manteniendo coherencia con el contexto.

Texto bíblico:
{bible_text}

Texto del sermón:
{manuscript}"""


class Translator:
    def __init__(
        self,
        on_translation: OnTranslation,
        bible_text: str = "",
        manuscript: str = "",
    ):
        self.on_translation = on_translation
        self.bible_text = bible_text
        self.manuscript = manuscript
        self._client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self._history: list[dict] = []
        self._final_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._running = False

    def _system_prompt(self) -> str:
        return PROMPT_TEMPLATE.format(
            argentine_rules=ARGENTINE_RULES,
            bible_text=self.bible_text or "(없음)",
            manuscript=self.manuscript or "(없음)",
        )

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
                temperature=0.2,
            )
            es = response.choices[0].message.content or ""
            if es.strip():
                self._history.append({"ko": ko_text, "es": es.strip()})
                if len(self._history) > 10:
                    self._history = self._history[-10:]
                await self.on_translation(item_id, ko_text, es.strip(), "final")
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
                await self._translate(item_id, ko_text)
            except asyncio.TimeoutError:
                continue

    async def run(self) -> None:
        self._running = True
        await self._final_worker()

    def stop(self) -> None:
        self._running = False
