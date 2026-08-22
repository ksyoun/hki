"""KoSentenceTranslator hold/release, dual LLM, through_index invariants."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from hki.live.ko_sentence_translator import KoSentenceTranslator, _flags_has_incierto
from hki.live.release_pacer import ReleaseItem


def _mock_openai_response(data: dict):
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content=json.dumps(data)))
    ]
    return mock_response


def _is_understand(kwargs) -> bool:
    sys = kwargs["messages"][0]["content"]
    return "through_index" in sys


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


def _patch_cfg(cfg, cfg_rp, *, max_pending=6, hold_ms=60000):
    cfg.FINAL_MODEL = "gpt-4o-mini"
    cfg.FINAL_TEMPERATURE = 0.1
    cfg.FINAL_HISTORY_LINES = 7
    cfg.SENTENCE_MAX_PENDING = max_pending
    cfg.SENTENCE_HOLD_TIMEOUT_MS = hold_ms
    cfg_rp.OUTPUT_RELEASE_MIN_MS = 10
    cfg_rp.OUTPUT_RELEASE_BASE_MS = 20


def test_hold_keeps_pending():
    async def scenario():
        releases: list[ReleaseItem] = []
        traces: list[dict] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(
            on_release=on_release, on_trace=traces.append
        )
        with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            async def fake_create(**kwargs):
                assert _is_understand(kwargs)
                return _mock_openai_response(
                    {"action": "hold", "through_index": 0, "ko_corrected": ""}
                )

            mock_client.chat.completions.create = fake_create
            worker = asyncio.create_task(translator.run())
            await translator.on_transcript_completed("a", "안녕하세요")
            await asyncio.sleep(0.25)
            assert len(releases) == 0
            assert translator.upstream_pending_count() >= 1
            assert traces and traces[0]["action"] == "hold"
            assert traces[0]["original_stt"] == "안녕하세요"
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
        traces: list[dict] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(
            on_release=on_release, on_trace=traces.append
        )
        understand_n = 0

        async def fake_create(**kwargs):
            nonlocal understand_n
            if _is_understand(kwargs):
                understand_n += 1
                if understand_n == 1:
                    return _mock_openai_response(
                        {"action": "hold", "through_index": 0, "ko_corrected": ""}
                    )
                return _mock_openai_response(
                    {
                        "action": "release",
                        "through_index": 2,
                        "ko_corrected": "안녕 하세요",
                    }
                )
            return _mock_openai_response({"es": "Buenos días, hermanos."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕")
                await translator.on_transcript_completed("b", "하세요")
                await asyncio.sleep(0.6)
            assert len(releases) == 1
            assert releases[0].es == "Buenos días, hermanos."
            assert releases[0].item_ids == ["a", "b"]
            assert translator.upstream_pending_count() == 0
            released = [t for t in traces if t.get("translation")]
            assert released and released[0]["through_index"] == 2
            assert released[0]["original_stt"] == "안녕 하세요"
            assert released[0]["release_reason"] == "sentence_complete"
            translator.stop_sync()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_through_index_gt_n_holds():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            if _is_understand(kwargs):
                return _mock_openai_response(
                    {"action": "release", "through_index": 4, "ko_corrected": "x"}
                )
            return _mock_openai_response({"es": "No debe salir."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "하나")
                await translator.on_transcript_completed("b", "둘")
                await translator.on_transcript_completed("c", "셋")
                await asyncio.sleep(0.5)
                assert releases == []
                assert translator.upstream_pending_count() >= 3
                translator.stop_sync()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_force_drain_uses_n_not_llm_index():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            user = kwargs["messages"][1]["content"]
            if _is_understand(kwargs):
                if "백엔드 강제 방출" in user:
                    return _mock_openai_response(
                        {
                            "action": "release",
                            "through_index": 1,
                            "ko_corrected": "하나",
                        }
                    )
                return _mock_openai_response(
                    {"action": "hold", "through_index": 0, "ko_corrected": ""}
                )
            return _mock_openai_response({"es": "Uno y dos."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "하나")
                await translator.on_transcript_completed("b", "둘")
                await asyncio.sleep(0.3)
                await translator.drain(timeout=3.0)
                assert len(releases) == 1
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
            _patch_cfg(cfg, cfg_rp, max_pending=2)
            translator = KoSentenceTranslator(on_release=on_release)

            async def fake_create(**kwargs):
                if _is_understand(kwargs):
                    return _mock_openai_response(
                        {"action": "hold", "through_index": 0, "ko_corrected": ""}
                    )
                return _mock_openai_response({"es": "Línea completa."})

            with patch(
                "hki.live.ko_sentence_translator.get_async_openai"
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "uno")
                await translator.on_transcript_completed("b", "dos")
                await asyncio.sleep(0.6)
                assert len(releases) == 1
                assert releases[0].item_ids == ["a", "b"]
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
            if _is_understand(kwargs):
                return _mock_openai_response(
                    {"action": "hold", "through_index": 0, "ko_corrected": ""}
                )
            return _mock_openai_response({"es": "Buenos días."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
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
            _patch_cfg(cfg, cfg_rp)
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


def test_flags_has_incierto():
    assert _flags_has_incierto(["incierto"]) is True
    assert _flags_has_incierto(["INCIERTO"]) is True
    assert _flags_has_incierto([]) is False
    assert _flags_has_incierto(None) is False
    assert _flags_has_incierto("incierto") is True


def test_translate_flags_incierto_sets_had_incierto_without_marker_in_es():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            if _is_understand(kwargs):
                return _mock_openai_response(
                    {
                        "action": "release",
                        "through_index": 1,
                        "ko_corrected": "안녕하세요",
                    }
                )
            return _mock_openai_response(
                {"es": "Buenos días. [INCIERTO]", "flags": ["incierto"]}
            )

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕하세요")
                await asyncio.sleep(0.4)
                assert len(releases) == 1
                assert releases[0].had_incierto is True
                assert "[INCIERTO]" not in releases[0].es
                assert releases[0].es == "Buenos días."
                await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_translate_without_flags_is_success():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            if _is_understand(kwargs):
                return _mock_openai_response(
                    {
                        "action": "release",
                        "through_index": 1,
                        "ko_corrected": "안녕하세요",
                    }
                )
            return _mock_openai_response({"es": "Buenos días."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕하세요")
                await asyncio.sleep(0.4)
                assert len(releases) == 1
                assert releases[0].had_incierto is False
                assert releases[0].es == "Buenos días."
                await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_translate_failure_does_not_drop_fragments():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)

        async def fake_create(**kwargs):
            if _is_understand(kwargs):
                return _mock_openai_response(
                    {
                        "action": "release",
                        "through_index": 1,
                        "ko_corrected": "안녕하세요",
                    }
                )
            return _mock_openai_response({"es": ""})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "안녕하세요")
                await asyncio.sleep(0.35)
                assert releases == []
                assert translator.upstream_pending_count() >= 1
                await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_partial_prefix_k2_leaves_f3_and_next_translate_is_f3_only():
    async def scenario():
        releases: list[ReleaseItem] = []
        translate_kos: list[str] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        understand_n = 0

        async def fake_create(**kwargs):
            nonlocal understand_n
            if _is_understand(kwargs):
                understand_n += 1
                if understand_n <= 2:
                    return _mock_openai_response(
                        {"action": "hold", "through_index": 0, "ko_corrected": ""}
                    )
                if understand_n == 3:
                    return _mock_openai_response(
                        {
                            "action": "release",
                            "through_index": 2,
                            "ko_corrected": "오늘 우리가 하나님께",
                        }
                    )
                return _mock_openai_response(
                    {
                        "action": "release",
                        "through_index": 1,
                        "ko_corrected": "이유를 생각해 봅시다",
                    }
                )
            translate_kos.append(_translate_ko(kwargs))
            if len(translate_kos) == 1:
                return _mock_openai_response({"es": "Hoy nosotros a Dios."})
            return _mock_openai_response({"es": "Pensemos las razones."})

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "오늘 우리가")
                await translator.on_transcript_completed("b", "하나님께")
                await translator.on_transcript_completed("c", "이유를 생각해 봅시다")
                await asyncio.sleep(0.8)
                assert translate_kos == [
                    "오늘 우리가 하나님께",
                    "이유를 생각해 봅시다",
                ]
                assert [r.item_ids for r in releases] == [["a", "b"], ["c"]]
                assert translator.upstream_pending_count() == 0
                await _stop_worker(translator, worker)

    asyncio.run(scenario())


def test_drain_failed_translate_emits_translation_failed_once():
    async def scenario():
        releases: list[ReleaseItem] = []
        traces: list[dict] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(
            on_release=on_release, on_trace=traces.append
        )

        async def fake_create(**kwargs):
            raise RuntimeError("llm down")

        with patch("hki.live.ko_sentence_translator.config") as cfg, patch(
            "hki.live.release_pacer.config"
        ) as cfg_rp:
            _patch_cfg(cfg, cfg_rp)
            with patch("hki.live.ko_sentence_translator.get_async_openai") as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.chat.completions.create = fake_create
                worker = asyncio.create_task(translator.run())
                await translator.on_transcript_completed("a", "오늘 우리가")
                await translator.on_transcript_completed("b", "이유를 생각해 봅시다")
                await asyncio.sleep(0.25)
                await translator.drain(timeout=3.0)
                failed = [
                    t for t in traces if t.get("release_reason") == "translation_failed"
                ]
                assert releases == []
                assert translator.upstream_pending_count() == 0
                assert len(failed) == 1
                assert failed[0]["action"] == "hold"
                assert failed[0]["translation"] == ""
                assert failed[0]["ko_corrected"] == ""
                assert failed[0]["original_stt"] == (
                    "오늘 우리가 이유를 생각해 봅시다"
                )
                await _stop_worker(translator, worker)
                failed_after = [
                    t for t in traces if t.get("release_reason") == "translation_failed"
                ]
                assert len(failed_after) == 1

    asyncio.run(scenario())


def test_drain_flushes_pacer_leftover_es():
    async def scenario():
        releases: list[ReleaseItem] = []

        async def on_release(item: ReleaseItem) -> None:
            releases.append(item)

        translator = KoSentenceTranslator(on_release=on_release)
        await translator._pacer.enqueue(
            ReleaseItem(
                batch_id="a",
                es="Ya traducido.",
                item_ids=["a"],
                ko_summary="안녕하세요",
            )
        )
        await translator.drain(timeout=1.0)
        assert len(releases) == 1
        assert releases[0].es == "Ya traducido."
        assert translator._pacer.release_queue_depth() == 0

    asyncio.run(scenario())
