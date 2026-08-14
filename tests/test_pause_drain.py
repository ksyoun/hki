"""Pause waits for translation queue drain before broadcasting paused."""

import asyncio

from hki.live.translate import Translator


def test_translator_drain_empty():
    translator = Translator(lambda item_id, ko, es: None)
    assert asyncio.run(translator.drain(timeout=1.0)) is True


def test_translator_drain_waits_for_in_flight():
    async def scenario():
        translator = Translator(lambda item_id, ko, es: None)
        translator._running = True
        translator._in_flight = 1

        async def finish():
            await asyncio.sleep(0.1)
            translator._in_flight = 0

        asyncio.create_task(finish())
        drained = await translator.drain(timeout=2.0)
        assert drained is True
        assert translator.pending_count() == 0

    asyncio.run(scenario())
