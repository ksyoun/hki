"""TranscriptionClient session payload VAD args and reconnect."""

import asyncio
import json
from unittest.mock import AsyncMock

from hki.live.transcribe import (
    TranscriptionClient,
    _CLEAR_BUFFER,
    _SEND_QUEUE_MAX,
    _VAD_MODELS,
)
from hki import config


def test_session_payload_uses_constructor_vad_args():
    client = TranscriptionClient(
        on_delta=AsyncMock(),
        on_completed=AsyncMock(),
        silence_duration_ms=250,
        prefix_padding_ms=300,
    )
    payload = client._session_update_payload()
    td = payload["session"]["audio"]["input"]["turn_detection"]
    if config.TRANSCRIPTION_MODEL in _VAD_MODELS:
        assert td["type"] == "server_vad"
        assert td["silence_duration_ms"] == 250
        assert td["prefix_padding_ms"] == 300
    else:
        assert td is None


def test_session_payload_defaults_to_classic_vad():
    client = TranscriptionClient(
        on_delta=AsyncMock(),
        on_completed=AsyncMock(),
    )
    payload = client._session_update_payload()
    td = payload["session"]["audio"]["input"]["turn_detection"]
    if config.TRANSCRIPTION_MODEL in _VAD_MODELS:
        assert td["silence_duration_ms"] == config.VAD_SILENCE_DURATION_MS
        assert td["prefix_padding_ms"] == config.VAD_PREFIX_PADDING_MS


def _client() -> TranscriptionClient:
    return TranscriptionClient(on_delta=AsyncMock(), on_completed=AsyncMock())


def test_send_audio_disconnected_drops_pcm_and_wakes_reconnect():
    async def scenario():
        client = _client()
        client._want_run = True
        await client.send_audio(b"stale")
        assert client._reconnect_needed.is_set()
        assert client._send_queue.empty()

    asyncio.run(scenario())


def test_send_audio_queues_when_session_up():
    async def scenario():
        client = _client()
        client._want_run = True
        client._running = True
        client._ws = object()
        await client.send_audio(b"live")
        assert client._send_queue.get_nowait() == b"live"
        assert not client._reconnect_needed.is_set()

    asyncio.run(scenario())


def test_stop_before_run_exits_immediately():
    async def scenario():
        client = _client()
        client.stop()
        await asyncio.wait_for(client.run(), timeout=1)

    asyncio.run(scenario())


def test_run_reconnects_only_after_audio_demand():
    async def scenario():
        client = _client()
        n_connect = 0
        first_down = asyncio.Event()

        async def fake_connect():
            nonlocal n_connect
            n_connect += 1
            client._ws = object()
            client._running = True

        async def fake_pump():
            if n_connect == 1:
                first_down.set()
                return
            client.stop()

        async def fake_disconnect():
            client._running = False
            client._ws = None
            client._drop_queued_audio()

        client.connect = fake_connect
        client._pump = fake_pump
        client._disconnect = fake_disconnect

        task = asyncio.create_task(client.run())
        await asyncio.wait_for(first_down.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert n_connect == 1
        await client.send_audio(b"wake")
        await asyncio.wait_for(task, timeout=1)
        assert n_connect == 2

    asyncio.run(scenario())


def test_run_stays_down_until_audio_if_ws_drops():
    async def scenario():
        client = _client()
        n_connect = 0
        first_down = asyncio.Event()

        async def fake_connect():
            nonlocal n_connect
            n_connect += 1
            client._ws = object()
            client._running = True

        async def fake_pump():
            first_down.set()

        async def fake_disconnect():
            client._running = False
            client._ws = None

        client.connect = fake_connect
        client._pump = fake_pump
        client._disconnect = fake_disconnect

        task = asyncio.create_task(client.run())
        await asyncio.wait_for(first_down.wait(), timeout=1)
        await asyncio.sleep(0.08)
        assert n_connect == 1
        client.stop()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_connect_failure_retries_without_waiting_for_audio():
    async def scenario():
        client = _client()
        n_connect = 0

        async def fake_connect():
            nonlocal n_connect
            n_connect += 1
            if n_connect == 1:
                raise ConnectionError("down")
            client._ws = object()
            client._running = True

        async def fake_pump():
            client.stop()

        async def fake_backoff(_delay):
            return client._want_run

        async def fake_disconnect():
            client._running = False
            client._ws = None

        client.connect = fake_connect
        client._pump = fake_pump
        client._reconnect_backoff = fake_backoff
        client._disconnect = fake_disconnect

        await asyncio.wait_for(client.run(), timeout=1)
        assert n_connect == 2

    asyncio.run(scenario())


def test_hold_input_queues_clear_and_drops_pcm():
    async def scenario():
        client = _client()
        client._want_run = True
        client._running = True
        client._ws = object()
        await client.send_audio(b"stale")
        await client.hold_input()
        assert client._send_queue.get_nowait() is _CLEAR_BUFFER
        assert client._send_queue.empty()

    asyncio.run(scenario())


def test_hold_input_down_wakes_reconnect():
    async def scenario():
        client = _client()
        client._want_run = True
        await client.hold_input()
        assert client._reconnect_needed.is_set()
        assert client._send_queue.empty()

    asyncio.run(scenario())


def test_wake_if_down_sets_reconnect():
    client = _client()
    client._want_run = True
    client.wake_if_down()
    assert client._reconnect_needed.is_set()


def test_wake_if_down_noop_when_up():
    client = _client()
    client._want_run = True
    client._running = True
    client._ws = object()
    client.wake_if_down()
    assert not client._reconnect_needed.is_set()


def test_api_error_closes_websocket():
    async def scenario():
        client = _client()
        client._running = True
        client._ws = AsyncMock()
        await client._handle_event(
            {"type": "error", "error": {"message": "buffer too large"}}
        )
        client._ws.close.assert_awaited()
        assert client._running is False
        assert client._reconnect_needed.is_set()

    asyncio.run(scenario())


def test_send_queue_overflow_kills_session():
    async def scenario():
        client = _client()
        client._want_run = True
        client._running = True
        client._ws = AsyncMock()
        for _ in range(_SEND_QUEUE_MAX):
            await client.send_audio(b"x")
        await client.send_audio(b"overflow")
        client._ws.close.assert_awaited()
        assert client._running is False
        assert client._send_queue.empty()

    asyncio.run(scenario())


def test_audio_sender_sends_clear_marker():
    async def scenario():
        client = _client()
        client._running = True
        sent: list[dict] = []

        class _Ws:
            async def send(self, data):
                sent.append(json.loads(data))
                client._running = False

        client._ws = _Ws()
        client._send_queue.put_nowait(_CLEAR_BUFFER)
        await client._audio_sender()
        assert sent == [{"type": "input_audio_buffer.clear"}]

    asyncio.run(scenario())
