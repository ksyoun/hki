"""TTS prep buffer — batch flush, timeout, no-drop."""

import asyncio
from unittest.mock import AsyncMock, patch

from hki.live.tts_prep import PrepItem, TTSPrepBuffer, _fallback_join, oralize_for_speech


def test_fallback_join():
    items = [PrepItem("a", "uno"), PrepItem("b", "dos")]
    assert _fallback_join(items) == "uno dos"


def test_oralize_single_item_skips_llm():
    assert asyncio.run(oralize_for_speech([PrepItem("1", "solo")])) == "solo"


def test_buffer_flush_at_batch_size():
    async def scenario():
        batches: list[tuple[str, str, list[str]]] = []

        async def on_batch(batch_id: str, text: str, item_ids: list[str]) -> None:
            batches.append((batch_id, text, item_ids))

        buf = TTSPrepBuffer(on_batch)
        buf._batch_size = 2
        buf._timeout_sec = 10.0
        worker = asyncio.create_task(buf.run())

        with patch(
            "hki.live.tts_prep.oralize_for_speech",
            new_callable=AsyncMock,
            return_value="polished",
        ):
            await buf.add("id1", "uno")
            await asyncio.sleep(0.05)
            assert not batches
            await buf.add("id2", "dos")
            await buf.drain(timeout=2.0)

        buf.stop_sync()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        assert len(batches) == 1
        assert batches[0][0] == "id1"
        assert batches[0][1] == "polished"
        assert batches[0][2] == ["id1", "id2"]

    asyncio.run(scenario())


def test_buffer_timeout_flush():
    async def scenario():
        batches: list[list[str]] = []

        async def on_batch(batch_id: str, text: str, item_ids: list[str]) -> None:
            batches.append(item_ids)

        buf = TTSPrepBuffer(on_batch)
        buf._batch_size = 3
        buf._timeout_sec = 0.1
        worker = asyncio.create_task(buf.run())

        with patch(
            "hki.live.tts_prep.oralize_for_speech",
            new_callable=AsyncMock,
            return_value="solo polish",
        ):
            await buf.add("solo", "una frase")
            await buf.drain(timeout=2.0)

        buf.stop_sync()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        assert batches == [["solo"]]

    asyncio.run(scenario())


def test_prep_no_drop_all_items_flushed():
    async def scenario():
        batches: list[list[str]] = []

        async def on_batch(batch_id: str, text: str, item_ids: list[str]) -> None:
            batches.append(list(item_ids))

        buf = TTSPrepBuffer(on_batch)
        buf._batch_size = 2
        buf._timeout_sec = 10.0
        worker = asyncio.create_task(buf.run())

        with patch(
            "hki.live.tts_prep.oralize_for_speech",
            new_callable=AsyncMock,
            return_value="batch",
        ):
            for i in range(5):
                await buf.add(f"id{i}", f"text{i}")
            await buf.drain(timeout=3.0)

        buf.stop_sync()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        all_ids: list[str] = []
        for batch_ids in batches:
            all_ids.extend(batch_ids)
        assert sorted(all_ids) == [f"id{i}" for i in range(5)]

    asyncio.run(scenario())
