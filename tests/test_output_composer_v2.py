"""Classic v2 lookahead composer — does not change v1 OutputComposer."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from hki.live.output_composer import FragmentItem
from hki.live.output_composer_v2 import (
    V2_RECOMBINE_SYSTEM,
    OutputComposerV2,
    V2Decision,
    recombine_for_output_v2,
    should_wait_for_lookahead,
)
from hki.live.release_pacer import ReleaseItem


def test_v2_prompt_is_prefix_consume_not_v1_join():
    assert "consume=1 es un resultado NORMAL" in V2_RECOMBINE_SYSTEM
    assert "hold=true" in V2_RECOMBINE_SYSTEM
    assert "prefix" in V2_RECOMBINE_SYSTEM.lower()
    assert "unirlos en un texto natural" not in V2_RECOMBINE_SYSTEM
    assert '{"text"' not in V2_RECOMBINE_SYSTEM
    assert "Solo JSON: {\"text\"" not in V2_RECOMBINE_SYSTEM


def test_v2_recombine_injects_es_anchors_with_consume_contract():
    async def scenario():
        with patch(
            "hki.live.output_composer_v2.get_async_openai"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            mock_response = AsyncMock()
            mock_response.choices = [
                AsyncMock(
                    message=AsyncMock(
                        content=json.dumps(
                            {
                                "es": "uno dos",
                                "consume": 2,
                                "hold": False,
                                "flags": [],
                            }
                        )
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
            }
            items = [
                FragmentItem("a", "하나", "uno"),
                FragmentItem("b", "둘", "dos"),
            ]
            await recombine_for_output_v2(
                items, context=ctx, sermon_mode=True
            )
            system = mock_client.chat.completions.create.await_args.kwargs[
                "messages"
            ][0]["content"]
        assert "Abraham confió en Dios" in system
        assert "consume=1 es un resultado NORMAL" in system
        assert "unirlos en un texto natural" not in system
        assert "Libro secreto" not in system
        assert "No debe aparecer" not in system
        assert "설교 용어 참고" not in system

    asyncio.run(scenario())


def test_should_wait_short_incomplete_ko():
    assert should_wait_for_lookahead("하나님은", "Dios") is True
    assert should_wait_for_lookahead("오늘 우리가", "Hoy nosotros") is True
    assert should_wait_for_lookahead("온유에 대해서", "acerca de la mansedumbre") is True


def test_should_wait_clear_complete_ko():
    assert should_wait_for_lookahead("그렇습니다.", "Así es.") is False
    assert should_wait_for_lookahead("여러분.", "Hermanos.") is False
    assert should_wait_for_lookahead("아멘.", "Amén.") is False


def test_should_wait_ko_ellipsis_beats_es():
    assert should_wait_for_lookahead("그래서...", "Por eso fue.") is True


def _patch_grace(**kwargs):
    return patch.multiple(
        "hki.live.output_composer_v2.config",
        OUTPUT_V2_GRACE_COMPLETE_MS=kwargs.get("complete", 40),
        OUTPUT_V2_GRACE_INCOMPLETE_MS=kwargs.get("incomplete", 80),
        OUTPUT_V2_MAX_WINDOW=3,
    )


def test_grace_expired_releases_single():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
            ) as mock_rec:
                await composer.add("a", "하나님은", "Dios")
                await asyncio.sleep(0.2)
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert mock_rec.await_count == 0
        assert len(released) == 1
        assert released[0].item_ids == ["a"]
        assert released[0].release_reason == "grace_expired"
        assert released[0].released_as_single is True

    asyncio.run(scenario())


def test_b_within_grace_recombines_consume_2():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        async def fake_recombine(items, **kwargs):
            return V2Decision(
                es="Dios es amor",
                consume=2,
                used_llm=True,
                joined_preview="Dios amor",
            )

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                side_effect=fake_recombine,
            ):
                await composer.add("a", "하나님은", "Dios")
                await composer.add("b", "사랑이십니다.", "es amor.")
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert len(released) == 1
        assert released[0].item_ids == ["a", "b"]
        assert released[0].consume == 2
        assert released[0].release_reason == "recombine"
        assert released[0].es == "Dios es amor"

    asyncio.run(scenario())


def test_consume_1_is_normal_keeps_b():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        async def fake_recombine(items, **kwargs):
            if len(items) == 2:
                return V2Decision(es=items[0].es, consume=1, used_llm=True)
            return V2Decision(es=items[0].es, consume=1, used_llm=True)

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                side_effect=fake_recombine,
            ):
                await composer.add("a", "그렇습니다.", "Así es.")
                await composer.add("b", "그러므로", "Por eso")
                await asyncio.sleep(0.2)
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert [it.item_ids for it in released][0] == ["a"]
        assert released[0].consume == 1
        leftover_ids = [i for it in released for i in it.item_ids]
        assert leftover_ids == ["a", "b"] or leftover_ids[:1] == ["a"]
        assert "b" in leftover_ids

    asyncio.run(scenario())


def test_max_window_consume_2_leaves_c():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        async def fake_recombine(items, **kwargs):
            if len(items) >= 3:
                return V2Decision(
                    es=f"{items[0].es} {items[1].es}",
                    consume=2,
                    used_llm=True,
                )
            return V2Decision(es="", consume=0, hold=True, used_llm=True)

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                side_effect=fake_recombine,
            ):
                await composer.add("a", "하나", "uno")
                await composer.add("b", "둘", "dos")
                await composer.add("c", "셋", "tres")
                await asyncio.sleep(0.05)
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert released[0].item_ids == ["a", "b"]
        assert released[0].release_reason == "forced_max_window"
        rest = [i for it in released[1:] for i in it.item_ids]
        assert rest == ["c"]

    asyncio.run(scenario())


def test_overflow_d_does_not_keep_a():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        async def fake_recombine(items, **kwargs):
            n = len(items)
            consume = min(3, n)
            return V2Decision(
                es=" ".join(it.es for it in items[:consume]),
                consume=consume,
                used_llm=True,
            )

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                side_effect=fake_recombine,
            ):
                for i, es in enumerate(["uno", "dos", "tres", "cuatro"], start=1):
                    await composer.add(f"id{i}", f"k{i}", es)
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        all_ids = [i for it in released for i in it.item_ids]
        assert all_ids[0] == "id1"
        assert "id4" in all_ids
        assert all_ids.index("id1") < all_ids.index("id4")

    asyncio.run(scenario())


def test_grace_race_late_b_does_not_resurrect_a():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        composer = OutputComposerV2(on_release)
        with _patch_grace(incomplete=50, complete=40):
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                return_value=V2Decision(es="x", consume=2, used_llm=True),
            ):
                await composer.add("a", "하나님은", "Dios")
                await asyncio.sleep(0.12)
                await composer.add("b", "사랑이십니다", "es amor")
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert released[0].item_ids == ["a"]
        assert released[0].release_reason == "grace_expired"
        assert "a" not in released[1].item_ids

    asyncio.run(scenario())


def test_recombine_failure_releases_a_keeps_b():
    async def scenario():
        released: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            released.append(item)

        async def boom(*_a, **_k):
            raise RuntimeError("llm down")

        composer = OutputComposerV2(on_release)
        with _patch_grace():
            with patch(
                "hki.live.output_composer_v2.recombine_for_output_v2",
                new_callable=AsyncMock,
                side_effect=boom,
            ):
                await composer.add("a", "하나님은", "Dios")
                await composer.add("b", "사랑이십니다.", "es amor.")
                await composer.drain(timeout=2.0)
                composer.stop_sync()
        assert released[0].item_ids == ["a"]
        assert released[0].release_reason == "fallback"
        rest = [i for it in released for i in it.item_ids]
        assert rest[0] == "a"
        assert "b" in rest

    asyncio.run(scenario())


def test_session_log_separates_v2_keys():
    from hki.live.session import LiveSession

    session = LiveSession()
    session.add_legacy_translation("v1")
    session.add_legacy_v2_translation("v2")
    session.add_legacy_trace(
        {
            "action": "release",
            "release_reason": "passthrough",
            "translation": "v1",
        }
    )
    session.add_legacy_v2_trace(
        {
            "action": "release",
            "release_reason": "grace_expired",
            "translation": "v2",
        }
    )
    log = session.to_log()
    assert log["translations_legacy"] == ["v1"]
    assert log["translations_legacy_v2"] == ["v2"]
    assert log["legacy_release_stats"]["counts"]["passthrough"] == 1
    assert log["legacy_v2_release_stats"]["counts"]["grace_expired"] == 1
    assert "pipeline_legacy_v2_enabled" in log
