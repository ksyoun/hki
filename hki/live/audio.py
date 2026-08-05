"""Scarlett audio capture, resampling, gain, and level metering."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd
from scipy import signal

from hki import config

logger = logging.getLogger(__name__)


@dataclass
class AudioDevice:
    index: int
    name: str
    channels: int
    sample_rate: float


def list_devices() -> list[AudioDevice]:
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append(
                AudioDevice(
                    index=i,
                    name=dev["name"],
                    channels=int(dev["max_input_channels"]),
                    sample_rate=float(dev["default_samplerate"]),
                )
            )
    return devices


def find_scarlett() -> AudioDevice | None:
    for dev in list_devices():
        if "scarlett" in dev.name.lower():
            return dev
    return None


def _rms_db(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms < 1e-10:
        return -60.0
    return 20.0 * np.log10(rms)


def _peak_db(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-10:
        return -60.0
    return 20.0 * np.log10(peak)


class AudioCapture:
    """Captures audio from Scarlett, applies gain, resamples to 24kHz PCM16."""

    def __init__(
        self,
        device_index: int | None = None,
        gain: float = config.GAIN_DEFAULT,
        on_pcm: Callable[[bytes], None] | None = None,
        on_level: Callable[[dict], None] | None = None,
    ):
        self.device_index = device_index
        self.gain = gain
        self.on_pcm = on_pcm
        self.on_level = on_level

        self._stream: sd.InputStream | None = None
        self._running = False
        self._native_rate = 48000.0
        self._lock = threading.Lock()

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self.gain = max(config.GAIN_MIN, min(config.GAIN_MAX, gain))

    def _resample(self, samples: np.ndarray, src_rate: float) -> np.ndarray:
        if src_rate == config.TARGET_SAMPLE_RATE:
            return samples
        num_out = int(len(samples) * config.TARGET_SAMPLE_RATE / src_rate)
        return signal.resample(samples, num_out)

    def _process_chunk(self, indata: np.ndarray) -> bytes:
        mono = indata[:, 0].astype(np.float32) if indata.ndim > 1 else indata.astype(np.float32)

        with self._lock:
            gain = self.gain

        if self.on_level:
            self.on_level(
                {
                    "rms_db": _rms_db(mono),
                    "peak_db": _peak_db(mono),
                    "clipping": bool(np.any(np.abs(mono) > 0.99)),
                }
            )

        mono = np.clip(mono * gain, -1.0, 1.0)
        resampled = self._resample(mono, self._native_rate)
        pcm16 = (resampled * 32767).astype(np.int16)
        return pcm16.tobytes()

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio status: %s", status)
        if not self._running:
            return
        pcm = self._process_chunk(indata)
        if self.on_pcm:
            self.on_pcm(pcm)

    def start(self) -> None:
        if self._running:
            return

        if self.device_index is not None:
            info = sd.query_devices(self.device_index)
            self._native_rate = float(info["default_samplerate"])
        else:
            scarlett = find_scarlett()
            if scarlett:
                self.device_index = scarlett.index
                self._native_rate = scarlett.sample_rate
            else:
                self._native_rate = 48000.0

        blocksize = int(self._native_rate * config.AUDIO_CHUNK_MS / 1000)
        self._stream = sd.InputStream(
            device=self.device_index,
            channels=1,
            samplerate=self._native_rate,
            blocksize=blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._running = True
        self._stream.start()
        logger.info(
            "Audio capture started: device=%s rate=%.0f gain=%.2f",
            self.device_index,
            self._native_rate,
            self.gain,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("Audio capture stopped")

    @property
    def is_running(self) -> bool:
        return self._running


def pcm_to_base64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")
