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
    RECOMBINE_SYSTEM,
    RecombineResult,
    _fallback_join,
    _matches_critical_sentence_ko,
    fragment_looks_open,
    fragment_looks_open_es,
    recombine_for_output,
    release_interval_ms,
    release_item_from_batch,
)
from hki.live.ko_endings import fragment_looks_open_ko
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


def test_classic_recombine_injects_es_anchors_not_ko_tidy():
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
                        content=json.dumps({"text": "uno dos", "flags": []})
                    )
                )
            ]
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            ctx = {
                "sermon_summary": "No debe aparecer",
                "bible_es_nvi": [{"ref": "Mateo 1:1", "text": "Libro secreto"}],
                "key_names": [{"ko": "사라", "es": "Sara"}],
                "critical_sentences": [
                    {
                        "ko": "핵심",
                        "es": "Abraham confió en Dios",
                        "note": "test",
                    }
                ],
                "style_notes": "usted",
            }
            items = [
                FragmentItem("a", "하나", "uno"),
                FragmentItem("b", "둘", "dos"),
            ]
            await recombine_for_output(items, context=ctx, sermon_mode=True)
            system = mock_client.chat.completions.create.await_args.kwargs[
                "messages"
            ][0]["content"]
        assert "Abraham confió en Dios" in system
        assert "사라 → Sara" in system
        assert "critical_sentences" in system or "ancla ES" in system
        assert "설교 용어 참고" not in system
        assert "Libro secreto" not in system
        assert "No debe aparecer" not in system
        assert "YA TRADUCIDOS" in system

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


def test_release_item_from_batch_stt_repair():
    batch = [FragmentItem("a", "사래가 왔다", "Sara vino")]
    result = RecombineResult(text="Sara vino.", joined_preview="Sara vino")
    ctx = {
        "key_names": [
            {"ko": "사라", "es": "Sara", "stt_variants": ["사래"]},
        ]
    }
    item = release_item_from_batch(batch, result, context=ctx)
    assert item.ko_summary == "사래가 왔다"
    assert item.ko_corrected == "사라가 왔다"
    assert item.stt_repair is True
    assert item.release_reason == "closed_immediate"
    assert item.used_llm_recombine is False
    assert item.recombine_llm_ms == 0
    assert item.hold_ms == 0
    assert item.joined_preview == "Sara vino"


def test_release_item_from_batch_recombine_reason():
    batch = [
        FragmentItem("a", "하나", "uno"),
        FragmentItem("b", "둘", "dos"),
    ]
    result = RecombineResult(
        text="uno y dos",
        used_llm=True,
        joined_preview="uno dos",
    )
    item = release_item_from_batch(batch, result)
    assert item.release_reason == "closed_immediate"
    assert item.used_llm_recombine is True
    assert item.item_ids == ["a", "b"]
    assert item.es == "uno y dos"


def test_release_item_from_batch_fallback_reason():
    batch = [FragmentItem("a", "한", "uno")]
    result = RecombineResult(text="uno", joined_preview="uno")
    item = release_item_from_batch(batch, result, fallback=True)
    assert item.release_reason == "recombine_fallback"


def test_fragment_looks_open_short_incomplete_ko():
    assert fragment_looks_open("하나님은", "Dios") is True
    assert fragment_looks_open("오늘 우리가", "Hoy nosotros") is True
    assert fragment_looks_open("온유에 대해서", "acerca de la mansedumbre") is True


def test_fragment_looks_open_clear_complete_ko():
    assert fragment_looks_open("그렇습니다.", "Así es.") is False
    assert fragment_looks_open("여러분.", "Hermanos.") is False
    assert fragment_looks_open("아멘.", "Amén.") is False


def test_fragment_looks_open_ko_ellipsis_beats_es():
    assert fragment_looks_open("그래서...", "Por eso fue.") is True


def test_fragment_looks_open_ko_vs_es_split():
    assert fragment_looks_open_ko("그렇습니다.") is False
    assert fragment_looks_open_ko("있습니까") is False
    assert fragment_looks_open_ko("갈망하는") is True
    assert fragment_looks_open_ko("그래서...") is True
    assert fragment_looks_open_es("Así es.") is False
    assert fragment_looks_open_es("Pero nosotros…") is True
    assert fragment_looks_open_es("lo dijo, pero") is True
    assert fragment_looks_open("그렇습니다.", "Pero nosotros…") is False
    assert fragment_looks_open("오늘 우리가 깊이 생각해 보는 것은", "Algo cerrado.") is True


def test_recombine_prompt_joins_open_fragments():
    assert "puntos suspensivos" in RECOMBINE_SYSTEM
    assert "OBLIGATORIAMENTE" in RECOMBINE_SYSTEM


async def _stop_composer(buf, worker):
    buf.stop_sync()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


def test_closed_fragment_flushes_immediately():
    async def scenario():
        releases: list[list[str]] = []
        items: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item.item_ids)
            items.append(item)

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._incomplete_timeout_sec = 10.0
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
                return_value=RecombineResult(text="Así es."),
            ):
                await buf.add("solo", "그렇습니다.", "Así es.")
                await asyncio.sleep(0.25)
                assert releases == [["solo"]]
                assert items[0].hold_ms == 0
                assert items[0].used_llm_recombine is False
                assert items[0].release_reason == "closed_immediate"
                await buf.drain(timeout=2.0)

            await _stop_composer(buf, worker)

    asyncio.run(scenario())


def test_open_fragment_holds_until_partner():
    async def scenario():
        releases: list[list[str]] = []
        items: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item.item_ids)
            items.append(item)

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._incomplete_timeout_sec = 10.0
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
                return_value=RecombineResult(text="joined"),
            ):
                await buf.add("a", "오늘 우리가", "Hoy nosotros")
                await asyncio.sleep(0.2)
                assert releases == []
                await buf.add("b", "생각합니다.", "pensamos.")
                await buf.drain(timeout=3.0)

            await _stop_composer(buf, worker)

        assert releases == [["a", "b"]]
        assert items[0].release_reason == "partner_arrived"

    asyncio.run(scenario())


def test_open_pending_then_closed_recombines_immediately():
    async def scenario():
        releases: list[list[str]] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item.item_ids)

        buf = OutputComposer(on_release)
        buf._batch_size = 2
        buf._incomplete_timeout_sec = 10.0
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
                return_value=RecombineResult(text="joined"),
            ):
                await buf.add("open", "오늘 우리가", "Hoy nosotros")
                await asyncio.sleep(0.05)
                await buf.add("closed", "그렇습니다.", "Así es.")
                await buf.drain(timeout=3.0)

            await _stop_composer(buf, worker)

        assert releases == [["open", "closed"]]

    asyncio.run(scenario())
