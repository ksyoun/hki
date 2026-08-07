"""TTS client queue — serial synth, no playback sleep."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from hki.live.tts import TTSClient


def test_tts_drain_after_synth():
    async def scenario():
        received: list[str] = []

        async def on_audio(item_id: str, text: str, pcm: bytes) -> None:
            received.append(item_id)

        mock_response = MagicMock()
        mock_response.content = b"\x00\x00" * 200

        mock_audio = MagicMock()
        mock_audio.speech.create = AsyncMock(return_value=mock_response)
        mock_openai = MagicMock()
        mock_openai.audio = mock_audio

        with patch("hki.live.tts.get_async_openai", return_value=mock_openai):
            client = TTSClient(on_audio)
            worker = asyncio.create_task(client.run())
            await client.speak("a", "hello")
            drained = await client.drain(timeout=2.0)
            client.stop()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        assert drained is True
        assert received == ["a"]

    asyncio.run(scenario())


def test_tts_synth_no_long_playback_sleep():
    async def scenario():
        sleep_durations: list[float] = []
        real_sleep = asyncio.sleep

        async def tracked_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            await real_sleep(0)

        async def on_audio(item_id: str, text: str, pcm: bytes) -> None:
            pass

        mock_response = MagicMock()
        mock_response.content = b"\x00\x00" * 200

        mock_audio = MagicMock()
        mock_audio.speech.create = AsyncMock(return_value=mock_response)
        mock_openai = MagicMock()
        mock_openai.audio = mock_audio

        with (
            patch("hki.live.tts.get_async_openai", return_value=mock_openai),
            patch("hki.live.tts.asyncio.sleep", tracked_sleep),
        ):
            client = TTSClient(on_audio)
            worker = asyncio.create_task(client.run())
            await client.speak("x", "short phrase")
            await client.drain(timeout=2.0)
            client.stop()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        assert 0.15 in sleep_durations
        assert not any(d > 1.0 for d in sleep_durations)

    asyncio.run(scenario())
