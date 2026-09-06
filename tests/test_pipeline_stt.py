"""Classic STT wiring: single Realtime session for operator KO + translate."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from hki.live.pipeline import LivePipeline, legacy_trace_from_item
from hki.live.release_pacer import ReleaseItem
from hki.live.session import LiveSession, SessionState


class _FakeBroadcaster:
    audience_count = 1
    speaker_subscribers = 0

    def __init__(self):
        self.messages: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.messages.append(msg)


def _streaming_pipeline() -> LivePipeline:
    session = LiveSession()
    session.state = SessionState.STREAMING
    return LivePipeline(session, _FakeBroadcaster())


def test_spawn_classic_stt_only():
    captured: list[dict] = []

    class _CapturingClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    pipe = _streaming_pipeline()
    with (
        patch("hki.live.pipeline.TranscriptionClient", _CapturingClient),
        patch("hki.live.pipeline.config.TTS_ENABLED", False),
    ):
        pipe._spawn_clients()

    assert len(captured) == 1
    classic = captured[0]
    assert classic.get("silence_duration_ms") is None
    assert classic.get("on_speech_started").__func__ is LivePipeline._on_classic_speech_started
    assert classic["on_completed"].__func__ is LivePipeline._on_transcript_completed
    assert pipe._translator is not None
    assert pipe._output_composer is not None


def test_classic_stt_feeds_translator_and_logs_ko():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._translator = AsyncMock()
        await pipe._on_transcript_completed("id1", "안녕하세요")
        pipe._translator.on_transcript_completed.assert_awaited_once_with(
            "id1", "안녕하세요", timing=ANY
        )
        assert pipe.session.transcript_log == ["안녕하세요"]
        assert any(
            m.get("type") == "transcript" and m.get("final") is True
            for m in pipe.broadcaster.messages
        )

    asyncio.run(scenario())


def test_pcm_fans_out_to_classic_transcriber():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._transcriber = AsyncMock()
        await pipe._audio_queue.put(b"pcm")
        task = asyncio.create_task(pipe._audio_forwarder())
        try:
            await asyncio.sleep(0.08)
        finally:
            pipe.session.state = SessionState.IDLE
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        pipe._transcriber.send_audio.assert_awaited()

    asyncio.run(scenario())


def _cancel_forwarder(pipe, task):
    pipe.session.state = SessionState.IDLE
    task.cancel()


def test_audio_forwarder_survives_pause_sends_silence_then_live_pcm():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._transcriber = AsyncMock()
        task = asyncio.create_task(pipe._audio_forwarder())
        try:
            pipe.session.pause()
            await pipe._audio_queue.put(b"live-during-pause")
            await asyncio.sleep(0.09)
            assert not task.done()
            sent = [c.args[0] for c in pipe._transcriber.send_audio.await_args_list]
            assert b"live-during-pause" not in sent
            silence = b"\x00" * pipe._stt_chunk_bytes()
            assert silence in sent
            pipe._transcriber.send_audio.reset_mock()
            pipe.session.resume()
            await pipe._audio_queue.put(b"after-resume")
            await asyncio.sleep(0.09)
            sent = [c.args[0] for c in pipe._transcriber.send_audio.await_args_list]
            assert b"after-resume" in sent
        finally:
            _cancel_forwarder(pipe, task)
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_release_without_speakers_patches_tts_skipped():
    async def scenario():
        pipe = _streaming_pipeline()
        item = ReleaseItem(
            batch_id="b1",
            es="Hola",
            item_ids=["i1"],
            ko_summary="안녕",
        )
        idx = pipe.session.add_legacy_trace(legacy_trace_from_item(item))
        await pipe._publish_live_release(item, idx)
        row = pipe.session.legacy_traces[idx]
        assert row["speed_trigger_reason"] == "tts_skipped"
        assert row["tts_speed_applied"] == 0.0
        assert row["tts_queue_len_at_enqueue"] == 0

    asyncio.run(scenario())


def test_tts_audio_patches_trace_and_broadcasts_playback_rate():
    async def scenario():
        pipe = _streaming_pipeline()
        idx = pipe.session.add_legacy_trace(
            {"action": "release", "translation": "Hola"}
        )
        pipe._tts_trace_by_batch["b1"] = idx
        pipe._tts = MagicMock()
        pipe._tts.queued_count.return_value = 4
        pipe._tts.pending_count.return_value = 4
        pcm = b"\x00\x00" * 24000
        await pipe._on_tts_audio("b1", "Hola", pcm)
        row = pipe.session.legacy_traces[idx]
        assert row["tts_queue_len_at_enqueue"] == 4
        assert row["tts_speed_applied"] == 1.1
        assert row["speed_trigger_reason"] == "queue<=6"
        assert row["tts_audio_duration_ms"] == 909
        msg = [m for m in pipe.broadcaster.messages if m.get("type") == "tts"][-1]
        assert msg["playback_rate"] == 1.1

    asyncio.run(scenario())


def test_tts_fail_patches_error_reason():
    async def scenario():
        pipe = _streaming_pipeline()
        idx = pipe.session.add_legacy_trace({"action": "release"})
        pipe._tts_trace_by_batch["b1"] = idx
        await pipe._on_tts_fail("b1")
        assert pipe.session.legacy_traces[idx]["speed_trigger_reason"] == "tts_error"

    asyncio.run(scenario())
