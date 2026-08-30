"""TranscriptionClient session payload VAD args."""

from unittest.mock import AsyncMock

from hki.live.transcribe import TranscriptionClient, _VAD_MODELS
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
