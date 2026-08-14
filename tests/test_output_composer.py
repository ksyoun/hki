"""OutputComposer — batch recombine, adaptive release pacing."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

from hki.live.output_composer import (
    FragmentItem,
    OutputComposer,
    _fallback_join,
    recombine_for_output,
    release_interval_ms,
)


def test_fallback_join():
    items = [
        FragmentItem("a", "ko1", "uno"),
        FragmentItem("b", "ko2", "dos"),
    ]
    assert _fallback_join(items) == "uno dos"


def test_recombine_single_item_skips_llm():
    assert (
        asyncio.run(recombine_for_output([FragmentItem("1", "한", "solo")]))
        == "solo"
    )


def test_recombine_rejects_unfaithful_llm_output():
    async def scenario():
        with patch(
            "hki.live.output_composer.get_async_openai"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            invented = (
                "Esto es un sermón completamente inventado con muchas palabras "
                "que no estaban en los fragmentos originales."
            )
            mock_response = AsyncMock()
            mock_response.choices = [
                AsyncMock(
                    message=AsyncMock(content=json.dumps({"text": invented}))
                )
            ]
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            items = [
                FragmentItem("a", "하나", "uno"),
                FragmentItem("b", "둘", "dos"),
            ]
            result = await recombine_for_output(items)
        assert result == "uno dos"

    asyncio.run(scenario())


def test_release_interval_adapts_to_depth():
    base, floor = 1500, 700
    idle = release_interval_ms(1, base_ms=base, min_ms=floor)
    busy = release_interval_ms(4, base_ms=base, min_ms=floor)
    flooded = release_interval_ms(20, base_ms=base, min_ms=floor)
    assert idle == 1500
    assert busy < idle
    assert busy >= floor
    assert flooded == floor


def test_composer_flush_at_batch_size():
    async def scenario():
        releases: list[tuple[str, str, list[str]]] = []

        async def on_release(
            batch_id: str, es: str, item_ids: list[str], ko_summary: str
        ) -> None:
            releases.append((batch_id, es, item_ids))

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._timeout_sec = 10.0
        # Speed up pacer for tests
        with patch("hki.live.output_composer.config") as cfg:
            cfg.OUTPUT_BATCH_SIZE = 2
            cfg.OUTPUT_TIMEOUT_MS = 10000
            cfg.OUTPUT_RELEASE_BASE_MS = 50
            cfg.OUTPUT_RELEASE_MIN_MS = 20
            cfg.OUTPUT_PREP_MODEL = None
            cfg.FINAL_MODEL = "gpt-4o-mini"

            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value="polished",
            ):
                await buf.add("id1", "한", "uno")
                await asyncio.sleep(0.05)
                assert not releases
                await buf.add("id2", "둘", "dos")
                await buf.drain(timeout=3.0)

            buf.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        assert len(releases) == 1
        assert releases[0][0] == "id1"
        assert releases[0][1] == "polished"
        assert releases[0][2] == ["id1", "id2"]

    asyncio.run(scenario())


def test_composer_timeout_flush():
    async def scenario():
        releases: list[list[str]] = []

        async def on_release(
            batch_id: str, es: str, item_ids: list[str], ko_summary: str
        ) -> None:
            releases.append(item_ids)

        buf = OutputComposer(on_release)
        buf._batch_size = 3
        buf._timeout_sec = 0.1

        with patch("hki.live.output_composer.config") as cfg:
            cfg.OUTPUT_RELEASE_BASE_MS = 30
            cfg.OUTPUT_RELEASE_MIN_MS = 10
            cfg.OUTPUT_PREP_MODEL = None
            cfg.FINAL_MODEL = "gpt-4o-mini"

            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value="solo polish",
            ):
                await buf.add("solo", "한", "una frase")
                await buf.drain(timeout=3.0)

            buf.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        assert releases == [["solo"]]

    asyncio.run(scenario())


def test_composer_no_drop_all_items_flushed():
    async def scenario():
        releases: list[list[str]] = []

        async def on_release(
            batch_id: str, es: str, item_ids: list[str], ko_summary: str
        ) -> None:
            releases.append(list(item_ids))

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._timeout_sec = 10.0

        with patch("hki.live.output_composer.config") as cfg:
            cfg.OUTPUT_RELEASE_BASE_MS = 20
            cfg.OUTPUT_RELEASE_MIN_MS = 10
            cfg.OUTPUT_PREP_MODEL = None
            cfg.FINAL_MODEL = "gpt-4o-mini"

            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value="batch",
            ):
                for i in range(5):
                    await buf.add(f"id{i}", f"ko{i}", f"text{i}")
                await buf.drain(timeout=5.0)

            buf.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        all_ids: list[str] = []
        for batch_ids in releases:
            all_ids.extend(batch_ids)
        assert sorted(all_ids) == [f"id{i}" for i in range(5)]

    asyncio.run(scenario())


def test_pacer_slows_when_queue_shallow():
    """Depth-1 interval longer than depth-4 (catch-up)."""
    assert release_interval_ms(1, 1500, 700) > release_interval_ms(4, 1500, 700)


def test_pacer_releases_spaced_when_many_ready():
    async def scenario():
        times: list[float] = []

        async def on_release(
            batch_id: str, es: str, item_ids: list[str], ko_summary: str
        ) -> None:
            times.append(time.monotonic())

        async def fake_recombine(items, **kwargs):
            return items[0].es

        buf = OutputComposer(on_release)
        buf._batch_size = 1
        buf._timeout_sec = 10.0

        with patch("hki.live.output_composer.config") as cfg:
            cfg.OUTPUT_RELEASE_BASE_MS = 200
            cfg.OUTPUT_RELEASE_MIN_MS = 150
            cfg.OUTPUT_PREP_MODEL = None
            cfg.FINAL_MODEL = "gpt-4o-mini"

            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                side_effect=fake_recombine,
            ):
                for i in range(3):
                    await buf.add(f"id{i}", f"ko{i}", f"es{i}")
                ok = await buf.drain(timeout=8.0)
                assert ok, f"drain failed pending={buf.pending_count()}"

            buf.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        assert len(times) == 3
        gaps = [times[i + 1] - times[i] for i in range(2)]
        assert max(gaps) >= 0.1

    asyncio.run(scenario())
