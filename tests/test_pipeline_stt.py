"""Dual STT wiring: operator KO stays on classic; oración uses its own session."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, patch

from hki.live.pipeline import LivePipeline
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


def test_spawn_dual_stt_uses_sentence_vad_and_splits_callbacks():
    captured: list[dict] = []

    class _CapturingClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    pipe = _streaming_pipeline()
    with (
        patch("hki.live.pipeline.TranscriptionClient", _CapturingClient),
        patch("hki.live.pipeline.config.PIPELINE_LEGACY_ENABLED", True),
        patch("hki.live.pipeline.config.PIPELINE_SENTENCE_ENABLED", True),
        patch("hki.live.pipeline.config.TTS_ENABLED", False),
        patch("hki.live.pipeline.config.SENTENCE_VAD_SILENCE_DURATION_MS", 250),
        patch("hki.live.pipeline.config.SENTENCE_VAD_PREFIX_PADDING_MS", 300),
        patch("hki.live.pipeline.config.live_pipeline_is_sentence", return_value=False),
        patch("hki.live.pipeline.config.translation_pipeline_status", return_value="both"),
    ):
        pipe._spawn_clients()

    assert len(captured) == 2
    classic, sentence = captured
    assert classic.get("silence_duration_ms") is None
    assert classic.get("on_speech_started").__func__ is LivePipeline._on_classic_speech_started
    assert sentence["silence_duration_ms"] == 250
    assert sentence["prefix_padding_ms"] == 300
    assert sentence["on_speech_started"].__func__ is LivePipeline._on_speech_started
    assert sentence["on_completed"].__func__ is LivePipeline._on_sentence_stt_completed
    assert classic["on_completed"].__func__ is LivePipeline._on_transcript_completed


def test_spawn_sentence_only_operator_uses_sentence_stt():
    captured: list[dict] = []

    class _CapturingClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    pipe = _streaming_pipeline()
    with (
        patch("hki.live.pipeline.TranscriptionClient", _CapturingClient),
        patch("hki.live.pipeline.config.PIPELINE_LEGACY_ENABLED", False),
        patch("hki.live.pipeline.config.PIPELINE_SENTENCE_ENABLED", True),
        patch("hki.live.pipeline.config.TTS_ENABLED", False),
        patch("hki.live.pipeline.config.SENTENCE_VAD_SILENCE_DURATION_MS", 250),
        patch("hki.live.pipeline.config.SENTENCE_VAD_PREFIX_PADDING_MS", 300),
        patch("hki.live.pipeline.config.live_pipeline_is_sentence", return_value=True),
        patch(
            "hki.live.pipeline.config.translation_pipeline_status",
            return_value="sentence",
        ),
    ):
        pipe._spawn_clients()

    assert len(captured) == 1
    assert pipe._transcriber is None
    assert captured[0]["on_completed"].__func__ is LivePipeline._on_sentence_operator_completed
    assert captured[0]["silence_duration_ms"] == 250


def test_sentence_stt_not_broadcast_or_logged_when_dual():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._sentence_translator = AsyncMock()
        await pipe._on_sentence_stt_completed("abc", "안녕하세요")
        assert pipe.session.transcript_log == []
        assert not any(m.get("type") == "transcript" for m in pipe.broadcaster.messages)
        pipe._sentence_translator.on_transcript_completed.assert_awaited_once_with(
            "s-abc", "안녕하세요", timing=ANY
        )
        assert pipe.session.sentence_fragments_received == 1

    asyncio.run(scenario())


def test_classic_stt_does_not_feed_sentence_translator():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._translator = AsyncMock()
        pipe._sentence_translator = AsyncMock()
        await pipe._on_transcript_completed("id1", "안녕하세요")
        pipe._translator.on_transcript_completed.assert_awaited_once_with(
            "id1", "안녕하세요", timing=ANY
        )
        pipe._sentence_translator.on_transcript_completed.assert_not_called()
        assert pipe.session.transcript_log == ["안녕하세요"]
        assert any(
            m.get("type") == "transcript" and m.get("final") is True
            for m in pipe.broadcaster.messages
        )

    asyncio.run(scenario())


def test_pcm_fans_out_to_both_transcribers():
    async def scenario():
        pipe = _streaming_pipeline()
        pipe._transcriber = AsyncMock()
        pipe._sentence_transcriber = AsyncMock()
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
        pipe._sentence_transcriber.send_audio.assert_awaited()

    asyncio.run(scenario())
