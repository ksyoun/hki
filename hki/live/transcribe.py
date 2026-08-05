"""OpenAI Realtime API transcription client (GA)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets

from hki import config
from hki.live.audio import pcm_to_base64

logger = logging.getLogger(__name__)

OnDelta = Callable[[str, str], Awaitable[None]]  # item_id, text
OnCompleted = Callable[[str, str], Awaitable[None]]  # item_id, text
OnError = Callable[[str], Awaitable[None]]

# Models that support server-side VAD in transcription sessions
_VAD_MODELS = frozenset(
    {
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
        "gpt-transcribe",
        "whisper-1",
    }
)


class TranscriptionClient:
    def __init__(
        self,
        on_delta: OnDelta,
        on_completed: OnCompleted,
        on_error: OnError | None = None,
    ):
        self.on_delta = on_delta
        self.on_completed = on_completed
        self.on_error = on_error
        self._ws = None
        self._running = False
        self._send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._item_buffers: dict[str, str] = {}

    def _transcription_config(self) -> dict:
        model = config.TRANSCRIPTION_MODEL
        if model == "gpt-live-transcribe":
            return {"model": model, "languages": ["ko"]}
        return {"model": model, "language": "ko"}

    def _turn_detection_config(self) -> dict | None:
        if config.TRANSCRIPTION_MODEL not in _VAD_MODELS:
            return None
        return {
            "type": "server_vad",
            "silence_duration_ms": config.VAD_SILENCE_DURATION_MS,
            "prefix_padding_ms": config.VAD_PREFIX_PADDING_MS,
        }

    def _session_update_payload(self) -> dict:
        audio_input: dict = {
            "format": {
                "type": "audio/pcm",
                "rate": config.TARGET_SAMPLE_RATE,
            },
            "transcription": self._transcription_config(),
        }
        turn_detection = self._turn_detection_config()
        audio_input["turn_detection"] = turn_detection

        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {"input": audio_input},
            },
        }

    async def connect(self) -> None:
        headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
        self._ws = await websockets.connect(
            config.REALTIME_WS_URL,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )
        self._running = True

        first = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=15))
        first_type = first.get("type")
        if first_type == "error":
            err = first.get("error", {})
            msg = err.get("message", str(first))
            raise RuntimeError(f"Realtime API error: {msg}")
        if first_type != "session.created":
            logger.warning("Unexpected first realtime event: %s", first_type)

        await self._ws.send(json.dumps(self._session_update_payload()))

        updated = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=15))
        if updated.get("type") == "error":
            err = updated.get("error", {})
            raise RuntimeError(err.get("message", str(updated)))
        if updated.get("type") != "session.updated":
            logger.warning("Unexpected session response: %s", updated.get("type"))

        vad = self._turn_detection_config()
        logger.info(
            "Transcription session connected (model=%s, vad=%s)",
            config.TRANSCRIPTION_MODEL,
            "server_vad" if vad else "off",
        )

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws and self._running:
            await self._send_queue.put(pcm)

    async def _audio_sender(self) -> None:
        while self._running:
            try:
                pcm = await asyncio.wait_for(self._send_queue.get(), timeout=0.5)
                event = {
                    "type": "input_audio_buffer.append",
                    "audio": pcm_to_base64(pcm),
                }
                await self._ws.send(json.dumps(event))
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Audio send error: %s", e)
                break

    async def _receive_loop(self) -> None:
        try:
            async for message in self._ws:
                await self._handle_event(json.loads(message))
        except websockets.ConnectionClosed as e:
            logger.warning("Transcription WS closed: %s", e)
            if self.on_error:
                await self.on_error(str(e))
        except Exception as e:
            logger.error("Transcription receive error: %s", e)
            if self.on_error:
                await self.on_error(str(e))

    async def _handle_event(self, event: dict) -> None:
        etype = event.get("type", "")

        if etype == "conversation.item.input_audio_transcription.delta":
            item_id = event.get("item_id", "")
            delta = event.get("delta", "")
            if item_id and delta:
                self._item_buffers[item_id] = self._item_buffers.get(item_id, "") + delta
                await self.on_delta(item_id, self._item_buffers[item_id])

        elif etype == "conversation.item.input_audio_transcription.completed":
            item_id = event.get("item_id", "")
            transcript = event.get("transcript", "")
            if item_id and transcript:
                self._item_buffers.pop(item_id, None)
                await self.on_completed(item_id, transcript)

        elif etype == "error":
            error_msg = event.get("error", {}).get("message", str(event))
            logger.error("Transcription API error: %s", error_msg)
            if self.on_error:
                await self.on_error(error_msg)

        elif etype in ("session.created", "session.updated"):
            logger.debug("Session event: %s", etype)

    async def run(self) -> None:
        await self.connect()
        sender = asyncio.create_task(self._audio_sender())
        receiver = asyncio.create_task(self._receive_loop())
        try:
            await asyncio.gather(sender, receiver)
        finally:
            await self.close()

    async def close(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
