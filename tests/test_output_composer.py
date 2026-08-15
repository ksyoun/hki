"""OutputComposer — batch recombine, adaptive release pacing."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from hki.live.output_composer import (
    FragmentItem,
    OutputComposer,
    RecombineResult,
    _fallback_join,
    _matches_critical_sentence_ko,
    recombine_for_output,
    release_interval_ms,
)
from hki.live.release_pacer import ReleaseItem


@contextmanager
def patch_release_config(**kwargs):
    with patch("hki.live.output_composer.config") as cfg_oc, patch(
        "hki.live.release_pacer.config"
    ) as cfg_rp:
        for key, value in kwargs.items():
            setattr(cfg_oc, key, value)
            setattr(cfg_rp, key, value)
        yield cfg_oc


def test_fallback_join():
    items = [
        FragmentItem("a", "ko1", "uno"),
        FragmentItem("b", "ko2", "dos"),
    ]
    assert _fallback_join(items) == "uno dos"


def test_recombine_single_item_skips_llm():
    result = asyncio.run(
        recombine_for_output([FragmentItem("1", "한", "solo")])
    )
    assert result.text == "solo"
    assert not result.repair_rejected


def test_recombine_single_item_strips_incierto():
    async def scenario():
        with patch(
            "hki.live.output_composer.get_async_openai"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            mock_response = AsyncMock()
            mock_response.choices = [
                AsyncMock(
                    message=AsyncMock(
                        content=json.dumps({"text": "solo", "flags": []})
                    )
                )
            ]
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            result = await recombine_for_output(
                [FragmentItem("1", "한", "solo [INCIERTO]")]
            )
        assert result.text == "solo"

    asyncio.run(scenario())


def test_matches_critical_sentence_ko():
    ctx = {
        "key_names": [
            {"ko": "사라", "es": "Sara", "stt_variants": ["사래"]},
            {"ko": "아브라함", "es": "Abraham"},
        ],
        "critical_sentences": [
            {
                "ko": "아브라함이 사라를 보고 믿음이 없었다",
                "es": "Abraham vio a Sara y no tenía confianza",
                "note": "",
            }
        ],
    }
    assert _matches_critical_sentence_ko("아브라함이 사라를", ctx)
    assert not _matches_critical_sentence_ko("여러분 안녕하세요", ctx)


def test_recombine_accepts_anchor_repair_with_flags():
    async def scenario():
        with patch(
            "hki.live.output_composer.get_async_openai"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            polished = (
                "Abraham vio a Sara y no tenía confianza en la promesa de Dios."
            )
            mock_response = AsyncMock()
            mock_response.choices = [
                AsyncMock(
                    message=AsyncMock(
                        content=json.dumps(
                            {
                                "text": polished,
                                "flags": [
                                    "Sujeto repuesto via critical_sentence"
                                ],
                            }
                        )
                    )
                )
            ]
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            items = [
                FragmentItem("a", "하나", "vio a X y no tiene confianza [INCIERTO]"),
                FragmentItem("b", "둘", "en la promesa."),
            ]
            result = await recombine_for_output(items)
        assert result.text == polished
        assert not result.repair_rejected

    asyncio.run(scenario())


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
        assert result.text == "uno dos"
        assert result.repair_rejected

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

        async def on_release(item: ReleaseItem) -> None:
            releases.append((item.batch_id, item.es, item.item_ids))

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._timeout_sec = 10.0
        with patch_release_config(
            OUTPUT_BATCH_SIZE=2,
            OUTPUT_TIMEOUT_MS=10000,
            OUTPUT_RELEASE_BASE_MS=50,
            OUTPUT_RELEASE_MIN_MS=20,
            OUTPUT_PREP_MODEL=None,
            FINAL_MODEL="gpt-4o-mini",
        ):
            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value=RecombineResult(text="polished"),
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

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item.item_ids)

        buf = OutputComposer(on_release)
        buf._batch_size = 3
        buf._timeout_sec = 0.1

        with patch_release_config(
            OUTPUT_RELEASE_BASE_MS=30,
            OUTPUT_RELEASE_MIN_MS=10,
            OUTPUT_PREP_MODEL=None,
            FINAL_MODEL="gpt-4o-mini",
        ):
            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value=RecombineResult(text="solo polish"),
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

        async def on_release(item: ReleaseItem) -> None:
            releases.append(list(item.item_ids))

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._timeout_sec = 10.0

        with patch_release_config(
            OUTPUT_RELEASE_BASE_MS=20,
            OUTPUT_RELEASE_MIN_MS=10,
            OUTPUT_PREP_MODEL=None,
            FINAL_MODEL="gpt-4o-mini",
        ):
            worker = asyncio.create_task(buf.run())
            with patch(
                "hki.live.output_composer.recombine_for_output",
                new_callable=AsyncMock,
                return_value=RecombineResult(text="batch"),
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
    assert release_interval_ms(1, 1500, 700) > release_interval_ms(4, 1500, 700)


def test_pacer_releases_spaced_when_many_ready():
    async def scenario():
        times: list[float] = []

        async def on_release(item: ReleaseItem) -> None:
            times.append(time.monotonic())

        async def fake_recombine(items, **kwargs):
            return RecombineResult(text=items[0].es)

        buf = OutputComposer(on_release)
        buf._batch_size = 1
        buf._timeout_sec = 10.0

        with patch_release_config(
            OUTPUT_RELEASE_BASE_MS=200,
            OUTPUT_RELEASE_MIN_MS=150,
            OUTPUT_PREP_MODEL=None,
            FINAL_MODEL="gpt-4o-mini",
        ):
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
