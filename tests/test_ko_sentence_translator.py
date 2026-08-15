"""KoSentenceTranslator hold/release behavior."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from hki.live.ko_sentence_translator import KoSentenceTranslator
from hki.live.release_pacer import ReleaseItem


def _mock_openai_response(data: dict):
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content=json.dumps(data)))
    ]
    return mock_response


def test_hold_keeps_pending():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(
                return_value=_mock_openai_response(
                    {"action": "hold", "through_index": 0, "es": ""}
                )
            )
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "안녕하세요")
            await asyncio.sleep(0.2)
            assert len(releases) == 0
            assert translator.upstream_pending_count() >= 1
            translator.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_release_through_index():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        responses = [
            {"action": "hold", "through_index": 0, "es": ""},
            {"action": "release", "through_index": 2, "es": "Buenos días, hermanos."},
        ]
        call_idx = 0

        async def fake_create(**kwargs):
            nonlocal call_idx
            data = responses[min(call_idx, len(responses) - 1)]
            call_idx += 1
            return _mock_openai_response(data)

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            cfg.FINAL_MODEL = "gpt-4o-mini"
            cfg.FINAL_TEMPERATURE = 0.1
            cfg.FINAL_HISTORY_LINES = 7
            cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
            cfg_rp.OUTPUT_RELEASE_BASE_MS = 20
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕")
                await translator.on_transcript_completed("b", "하세요")
                await asyncio.sleep(0.5)
            assert len(releases) == 1
            assert releases[0].es == "Buenos días, hermanos."
            assert releases[0].item_ids == ["a", "b"]
            assert translator.upstream_pending_count() == 0
            translator.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_max_pending_forces_evaluate():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            cfg.SENTENCE_MAX_PENDING = 2
            cfg.SENTENCE_HOLD_TIMEOUT_MS = 60000
            cfg.FINAL_MODEL = "gpt-4o-mini"
            cfg.FINAL_TEMPERATURE = 0.1
            cfg.FINAL_HISTORY_LINES = 7
            cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
            cfg_rp.OUTPUT_RELEASE_BASE_MS = 20
            translator = KoSentenceTranslator(on_release=on_release)
            call_idx = 0

            async def fake_create(**kwargs):
                nonlocal call_idx
                call_idx += 1
                if call_idx == 1:
                    data = {"action": "hold", "through_index": 0, "es": ""}
                else:
                    data = {
                        "action": "release",
                        "through_index": 2,
                        "es": "Línea completa.",
                    }
                return _mock_openai_response(data)

            with patch(
                "hki.live.ko_sentence_translator.get_async_openai"
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "uno")
                await translator.on_transcript_completed("b", "dos")
                await asyncio.sleep(0.5)
                assert len(releases) == 1
                translator.stop_sync()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_set_sermon_mode_changes_prompt_mode():
    async def on_release(item: ReleaseItem) -> None:
        pass

    translator = KoSentenceTranslator(on_release=on_release, sermon_mode=False)
    assert translator.describe_prompt()["translation_prompt_mode"] == "general"
    translator.set_sermon_mode(True)
    assert translator.describe_prompt()["translation_prompt_mode"] == "sermon_fallback"


def test_force_release_translates_instead_of_dropping():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        call_idx = 0

        async def fake_create(**kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                raise RuntimeError("llm down")
            return _mock_openai_response(
                {"action": "release", "through_index": 1, "es": "Buenos días."}
            )

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            cfg.FINAL_MODEL = "gpt-4o-mini"
            cfg.FINAL_TEMPERATURE = 0.1
            cfg.FINAL_HISTORY_LINES = 7
            cfg.SENTENCE_HOLD_TIMEOUT_MS = 60000
            cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
            cfg_rp.OUTPUT_RELEASE_BASE_MS = 20
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕하세요")
                await asyncio.sleep(0.2)
                assert translator.upstream_pending_count() >= 1
                assert releases == []
                await translator.drain(timeout=3.0)
                assert len(releases) == 1
                assert releases[0].es == "Buenos días."
                assert releases[0].item_ids == ["a"]
                assert translator.upstream_pending_count() == 0
                translator.stop_sync()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_force_release_keeps_pending_if_no_es():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            raise RuntimeError("llm down")

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            cfg.FINAL_MODEL = "gpt-4o-mini"
            cfg.FINAL_TEMPERATURE = 0.1
            cfg.FINAL_HISTORY_LINES = 7
            cfg.SENTENCE_HOLD_TIMEOUT_MS = 60000
            cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
            cfg_rp.OUTPUT_RELEASE_BASE_MS = 20
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕하세요")
                await asyncio.sleep(0.2)
                await translator._force_release_all()
                assert releases == []
                assert translator.upstream_pending_count() >= 1
                translator.stop_sync()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())
