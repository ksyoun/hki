"""KoSentenceTranslator V2: buffer, debounce, detach, mapping."""

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


def _is_recombine(kwargs) -> bool:
    sys = kwargs["messages"][0]["content"]
    return "fragment_indexes" in sys


def _recombine_user(kwargs) -> str:
    return kwargs["messages"][1]["content"]


def _translate_ko(kwargs) -> str:
    user = kwargs["messages"][1]["content"]
    marker = "Fuente KO (delimitada):\n"
    rest = user.split(marker, 1)[1]
    return rest.split("\n\n", 1)[0].strip()


async def _stop_worker(translator, worker):
    translator.stop_sync()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


def _patch_cfg(cfg, cfg_rp, *, max_pending=6, pause_ms=80, max_buffer_ms=60000):
    cfg.FINAL_MODEL = "gpt-4o-mini"
    cfg.FINAL_TEMPERATURE = 0.1
    cfg.RECOMBINE_TEMPERATURE = 0.05
    cfg.FINAL_HISTORY_LINES = 7
    cfg.SENTENCE_MAX_PENDING = max_pending
    cfg.SENTENCE_RELEASE_PAUSE_MS = pause_ms
    cfg.SENTENCE_MAX_BUFFER_MS = max_buffer_ms
    cfg.OUTPUT_RELEASE_MIN_MS = 10
    cfg.OUTPUT_RELEASE_BASE_MS = 20
    cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
    cfg_rp.OUTPUT_RELEASE_BASE_MS = 20


async def _wait_until(pred, timeout=2.5):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met")


def test_fragment_buffers_without_llm():
    async def scenario():
        calls = []

        async def fake_create(**kwargs):
            calls.append(kwargs)
            return _mock_openai_response({"es": "x"})

        translator = KoSentenceTranslator(on_release=AsyncMock())
        translator._release_pause_sec = 0.4
        with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.chat.completions.create = fake_create
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "안녕하세요")
            await asyncio.sleep(0.12)
            assert calls == []
            assert translator.upstream_pending_count() >= 1
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_debounce_after_last_completed_recombines_once():
    async def scenario():
        releases: list[ReleaseItem] = []
        traces: list[dict] = []
        kinds: list[str] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=60)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    kinds.append("recombine")
                    return _mock_openai_response(
                        {
                            "units": [
                                {
                                    "text": "오늘 우리가 온유에 대해서 생각해 보려고 합니다.",
                                    "fragment_indexes": [0, 1, 2],
                                }
                            ]
                        }
                    )
                kinds.append("translate")
                return _mock_openai_response({"es": "Hoy reflexionamos."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(
                on_release=on_release, on_trace=traces.append
            )
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "오늘 우리가")
            await translator.on_transcript_completed("b", "온유에 대해서")
            await translator.on_transcript_completed("c", "생각해 보려고 합니다.")
            await _wait_until(lambda: len(releases) == 1)
            assert kinds.count("recombine") == 1
            assert kinds.count("translate") == 1
            assert releases[0].release_reason == "vad_release"
            assert traces[0]["fragment_count"] == 3
            assert traces[0]["latency_last_fragment_to_caption"] >= 0
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_speech_started_aborts_release_before_detach():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        translator._pending.append(
            translator._pending[0]
            if False
            else __import__(
                "hki.live.ko_sentence_translator", fromlist=["PendingFragment"]
            ).PendingFragment("a", "안녕하세요")
        )
        translator._speech_active = True
        await translator._try_release("vad_release", force=False)
        assert releases == []
        assert len(translator._pending) == 1

    asyncio.run(scenario())


def test_empty_pending_does_not_release():
    async def scenario():
        translator = KoSentenceTranslator(on_release=AsyncMock())
        await translator._try_release("vad_release", force=False)
        assert translator._pending == []

    asyncio.run(scenario())


def test_detach_keeps_later_fragment_in_next_pending():
    async def scenario():
        releases: list[ReleaseItem] = []
        recombine_started = asyncio.Event()
        allow_recombine = asyncio.Event()
        seen_users: list[str] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=40)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    seen_users.append(_recombine_user(kwargs))
                    recombine_started.set()
                    await allow_recombine.wait()
                    return _mock_openai_response(
                        {
                            "units": [
                                {
                                    "text": "오늘 우리가 온유에 대해서 생각해 보려고 합니다.",
                                    "fragment_indexes": [0, 1, 2],
                                }
                            ]
                        }
                    )
                return _mock_openai_response({"es": "Hoy."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "오늘 우리가")
            await translator.on_transcript_completed("b", "온유에 대해서")
            await translator.on_transcript_completed("c", "생각해 보려고 합니다.")
            await _wait_until(recombine_started.is_set)
            translator._release_pause_sec = 5.0
            await translator.on_transcript_completed("d", "하나님께서")
            await _wait_until(
                lambda: [f.ko for f in translator._pending] == ["하나님께서"]
            )
            allow_recombine.set()
            await _wait_until(lambda: translator._in_flight == 0)
            assert "하나님께서" not in seen_users[0]
            assert [f.ko for f in translator._pending] == ["하나님께서"]
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_max_pending_detaches_and_next_stays():
    async def scenario():
        releases: list[ReleaseItem] = []
        recombine_started = asyncio.Event()
        allow_recombine = asyncio.Event()

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, max_pending=3, pause_ms=5000)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    recombine_started.set()
                    await allow_recombine.wait()
                    return _mock_openai_response(
                        {
                            "units": [
                                {
                                    "text": "하나 둘 셋",
                                    "fragment_indexes": [0, 1, 2],
                                }
                            ]
                        }
                    )
                return _mock_openai_response({"es": "Uno."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "하나")
            await translator.on_transcript_completed("b", "둘")
            await translator.on_transcript_completed("c", "셋")
            await _wait_until(recombine_started.is_set)
            translator._release_pause_sec = 5.0
            await translator.on_transcript_completed("d", "넷")
            await _wait_until(lambda: [f.ko for f in translator._pending] == ["넷"])
            allow_recombine.set()
            await _wait_until(lambda: len(releases) == 1)
            assert releases[0].release_reason == "max_pending"
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_max_duration_releases_even_if_speech_active():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=5000, max_buffer_ms=40)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    return _mock_openai_response(
                        {"units": [{"text": "안녕하세요", "fragment_indexes": [0]}]}
                    )
                return _mock_openai_response({"es": "Hola."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "안녕하세요")
            await asyncio.sleep(0.02)
            translator.on_speech_started()
            await _wait_until(lambda: len(releases) == 1)
            assert releases[0].release_reason == "max_duration"
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_mapping_overlap_falls_back_to_join():
    async def scenario():
        released_ko: list[str] = []

        async def on_release(item: ReleaseItem) -> None:
            released_ko.append(item.ko_corrected)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=40)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    return _mock_openai_response(
                        {
                            "units": [
                                {"text": "하나 둘", "fragment_indexes": [0, 1]},
                                {"text": "둘 셋", "fragment_indexes": [1, 2]},
                            ]
                        }
                    )
                return _mock_openai_response({"es": "Ok."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "하나")
            await translator.on_transcript_completed("b", "둘")
            await translator.on_transcript_completed("c", "셋")
            await _wait_until(lambda: len(released_ko) == 1)
            assert released_ko[0] == "하나 둘 셋"
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_two_units_translate_twice():
    async def scenario():
        translations: list[str] = []
        translate_n = 0

        async def on_release(item: ReleaseItem) -> None:
            translations.append(item.es)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=40)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                nonlocal translate_n
                if _is_recombine(kwargs):
                    return _mock_openai_response(
                        {
                            "units": [
                                {"text": "첫 문장입니다.", "fragment_indexes": [0]},
                                {"text": "둘째 문장입니다.", "fragment_indexes": [1]},
                            ]
                        }
                    )
                translate_n += 1
                ko = _translate_ko(kwargs)
                return _mock_openai_response({"es": f"ES:{ko}"})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "첫 문장입니다.")
            await translator.on_transcript_completed("b", "둘째 문장입니다.")
            await _wait_until(lambda: len(translations) == 2)
            assert translate_n == 2
            assert translations[0].startswith("ES:")
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_drain_force_releases():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=5000)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    return _mock_openai_response(
                        {"units": [{"text": "안녕하세요", "fragment_indexes": [0]}]}
                    )
                return _mock_openai_response({"es": "Hola."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(on_release=on_release)
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "안녕하세요")
            await asyncio.sleep(0.05)
            assert releases == []
            await translator.drain(timeout=2.0)
            assert any(r.release_reason == "drain" for r in releases)
            await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_awaiting_transcript_blocks_vad_release():
    async def scenario():
        translator = KoSentenceTranslator(on_release=AsyncMock())
        from hki.live.ko_sentence_translator import PendingFragment

        translator._pending.append(PendingFragment("a", "오늘 우리가"))
        translator._awaiting_transcript = True
        await translator._try_release("vad_release", force=False)
        assert len(translator._pending) == 1

    asyncio.run(scenario())


def test_manuscript_copy_from_recombine_is_rejected():
    async def scenario():
        releases: list[ReleaseItem] = []
        translate_sources: list[str] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        manuscript = (
            "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다 "
            "그리고 순종합시다"
        )
        invented = "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다"

        with (
            patch("hki.live.ko_sentence_translator.config") as cfg,
            patch("hki.live.release_pacer.config") as cfg_rp,
            patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get,
        ):
            _patch_cfg(cfg, cfg_rp, pause_ms=40)
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                if _is_recombine(kwargs):
                    return _mock_openai_response(
                        {
                            "units": [
                                {
                                    "text": invented,
                                    "fragment_indexes": [0],
                                }
                            ]
                        }
                    )
                translate_sources.append(_translate_ko(kwargs))
                return _mock_openai_response({"es": "Hoy."})

            mock_client.chat.completions.create = fake_create
            translator = KoSentenceTranslator(
                on_release=on_release,
                manuscript=manuscript,
            )
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "오늘 우리가")
            await _wait_until(lambda: len(releases) == 1)
            assert translate_sources == ["오늘 우리가"]
            assert releases[0].repair_rejected is True
            assert releases[0].ko_corrected == "오늘 우리가"
            await _stop_worker(translator, worker)

    asyncio.run(scenario())
