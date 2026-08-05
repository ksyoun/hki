"""2-tier translation: draft (fast) + final (context-aware)."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from hki import config

logger = logging.getLogger(__name__)

OnTranslation = Callable[[str, str, str, str], Awaitable[None]]  # item_id, ko, es, tier

DRAFT_PROMPT = """Translate Korean to Argentine Spanish (voseo).
Output only the translation, nothing else."""

FINAL_PROMPT_TEMPLATE = """당신은 한국어 설교를 아르헨티나 스페인어로 번역하는 통역사입니다.
규칙:
- voseo 사용 (vos, tenés, podés)
- 라틴아메리카/아르헨티나 신학 용어
- 제공된 성경 본문의 스페인어 표현과 용어 일치
- 설교 원고의 고유명사·성경 구절 표기 통일
- 번역만 출력 (설명 금지)

성경 본문:
{bible_text}

설교 원고:
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
        self._draft_tasks: dict[str, asyncio.Task] = {}
        self._draft_timers: dict[str, asyncio.Task] = {}
        self._history: list[dict] = []
        self._final_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._running = False

    def update_context(self, bible_text: str, manuscript: str) -> None:
        self.bible_text = bible_text
        self.manuscript = manuscript

    async def on_transcript_delta(self, item_id: str, ko_text: str) -> None:
        """Debounce draft translation on partial transcript."""
        if not config.DRAFT_ENABLED:
            return

        if item_id in self._draft_timers:
            self._draft_timers[item_id].cancel()

        async def _debounced():
            await asyncio.sleep(config.DRAFT_DEBOUNCE_MS / 1000)
            await self._translate_draft(item_id, ko_text)

        self._draft_timers[item_id] = asyncio.create_task(_debounced())

    async def on_transcript_completed(self, item_id: str, ko_text: str) -> None:
        """Queue final translation on completed transcript."""
        if item_id in self._draft_timers:
            self._draft_timers[item_id].cancel()
            del self._draft_timers[item_id]
        if item_id in self._draft_tasks:
            self._draft_tasks[item_id].cancel()
            del self._draft_tasks[item_id]

        await self._final_queue.put((item_id, ko_text))

    async def _translate_draft(self, item_id: str, ko_text: str) -> None:
        if not ko_text.strip():
            return

        if item_id in self._draft_tasks:
            self._draft_tasks[item_id].cancel()

        async def _run():
            try:
                history_msgs = self._build_history_messages(config.DRAFT_HISTORY_LINES)
                messages = [
                    {"role": "system", "content": DRAFT_PROMPT},
                    *history_msgs,
                    {"role": "user", "content": ko_text},
                ]
                response = await self._client.chat.completions.create(
                    model=config.DRAFT_MODEL,
                    messages=messages,
                    max_tokens=256,
                    temperature=0.3,
                )
                es = response.choices[0].message.content or ""
                if es.strip():
                    await self.on_translation(item_id, ko_text, es.strip(), "draft")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Draft translation error: %s", e)

        self._draft_tasks[item_id] = asyncio.create_task(_run())

    async def _translate_final(self, item_id: str, ko_text: str) -> None:
        if not ko_text.strip():
            return
        try:
            system = FINAL_PROMPT_TEMPLATE.format(
                bible_text=self.bible_text or "(없음)",
                manuscript=self.manuscript or "(없음)",
            )
            history_msgs = self._build_history_messages(config.FINAL_HISTORY_LINES)
            messages = [
                {"role": "system", "content": system},
                *history_msgs,
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
            logger.error("Final translation error: %s", e)

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
                await self._translate_final(item_id, ko_text)
            except asyncio.TimeoutError:
                continue

    async def run(self) -> None:
        self._running = True
        await self._final_worker()

    def stop(self) -> None:
        self._running = False
        for task in list(self._draft_tasks.values()):
            task.cancel()
        for task in list(self._draft_timers.values()):
            task.cancel()
        self._draft_tasks.clear()
        self._draft_timers.clear()
