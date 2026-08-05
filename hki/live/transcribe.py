"""OpenAI gpt-realtime-whisper WebSocket transcription client."""

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

    async def connect(self) -> None:
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(
            config.REALTIME_WS_URL,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )
        self._running = True

        session_config = {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": config.TARGET_SAMPLE_RATE},
                        "transcription": {
                            "model": config.TRANSCRIPTION_MODEL,
                            "language": "ko",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "silence_duration_ms": config.VAD_SILENCE_DURATION_MS,
                            "prefix_padding_ms": config.VAD_PREFIX_PADDING_MS,
                        },
                    }
                },
            },
        }
        await self._ws.send(json.dumps(session_config))
        logger.info("Transcription session connected")

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
