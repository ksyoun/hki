"""Audio capture, resampling, level metering."""

from __future__ import annotations

import base64
import logging
import threading
import time
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


def default_input_device() -> AudioDevice:
    """OS default input device (sounddevice / PortAudio)."""
    try:
        info = sd.query_devices(kind="input")
    except sd.PortAudioError as e:
        raise ValueError("No hay dispositivo de entrada predeterminado en el sistema") from e
    if int(info["max_input_channels"]) <= 0:
        raise ValueError("El dispositivo predeterminado no tiene entrada de audio")
    return AudioDevice(
        index=int(info["index"]),
        name=info["name"],
        channels=int(info["max_input_channels"]),
        sample_rate=float(info["default_samplerate"]),
    )


def find_scarlett() -> AudioDevice | None:
    """Optional diagnostic helper — not used for capture device selection."""
    scarletts = [d for d in list_devices() if "scarlett" in d.name.lower()]
    if not scarletts:
        return None
    return max(scarletts, key=lambda d: (d.channels, d.sample_rate))


def pick_input_mono(indata: np.ndarray) -> np.ndarray:
    """Pick the louder channel — Scarlett often has signal on ch2 when ch1 is wired wrong."""
    if indata.ndim == 1:
        return indata.astype(np.float32, copy=False)
    data = indata.astype(np.float32, copy=False)
    if data.shape[1] == 1:
        return data[:, 0]
    best = 0
    best_rms = -1.0
    for ch in range(data.shape[1]):
        r = float(np.sqrt(np.mean(data[:, ch] ** 2)))
        if r > best_rms:
            best_rms = r
            best = ch
    return data[:, best]


def is_valid_input_device(index: int) -> bool:
    if index < 0:
        return False
    try:
        info = sd.query_devices(index)
        return int(info["max_input_channels"]) > 0
    except Exception:
        return False


def resolve_input_device(device_index: int | None) -> AudioDevice:
    """Explicit index if valid, otherwise the OS default input device."""
    if device_index is not None and is_valid_input_device(device_index):
        info = sd.query_devices(device_index)
        return AudioDevice(
            index=device_index,
            name=info["name"],
            channels=int(info["max_input_channels"]),
            sample_rate=float(info["default_samplerate"]),
        )
    return default_input_device()


def rms_db(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms < 1e-10:
        return -60.0
    return 20.0 * np.log10(rms)


def peak_db(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-10:
        return -60.0
    return 20.0 * np.log10(peak)


class AudioCapture:
    """Captures system default (or explicit) input, resamples to 24kHz PCM16."""

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
        self._last_status_log_at = 0.0
        self._last_status_logged: str | None = None
        self._native_rate = 48000.0
        self._capture_channels = 1
        self.device_name: str = ""
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
        mono = pick_input_mono(indata)

        with self._lock:
            gain = self.gain

        mono = np.clip(mono * gain, -1.0, 1.0)

        if self.on_level:
            self.on_level(
                {
                    "rms_db": rms_db(mono),
                    "peak_db": peak_db(mono),
                    "clipping": bool(np.any(np.abs(mono) >= 0.99)),
                }
            )

        resampled = self._resample(mono, self._native_rate)
        pcm16 = (resampled * 32767).astype(np.int16)
        return pcm16.tobytes()

    def _log_input_status(self, status) -> None:
        text = str(status)
        now = time.monotonic()
        if text == self._last_status_logged and now - self._last_status_log_at < 5.0:
            return
        self._last_status_logged = text
        self._last_status_log_at = now
        logger.warning("Audio input status: %s", status)

    def _callback(self, indata, frames, time_info, status):
        if status:
            self._log_input_status(status)
        if not self._running:
            return
        try:
            pcm = self._process_chunk(indata)
        except Exception:
            logger.exception("Audio process_chunk failed")
            return
        if self.on_pcm:
            self.on_pcm(pcm)

    def start(self) -> None:
        if self._running:
            return

        resolved = resolve_input_device(self.device_index)
        self.device_index = resolved.index
        self.device_name = resolved.name
        self._native_rate = resolved.sample_rate
        self._capture_channels = min(2, max(1, resolved.channels))

        blocksize = max(1, int(self._native_rate * config.AUDIO_CHUNK_MS / 1000))
        try:
            sd.check_input_settings(
                device=self.device_index,
                channels=self._capture_channels,
                samplerate=self._native_rate,
                dtype="float32",
            )
        except Exception as e:
            logger.warning("Input settings check: %s — trying 1 channel", e)
            self._capture_channels = 1

        self._stream = sd.InputStream(
            device=self.device_index,
            channels=self._capture_channels,
            samplerate=self._native_rate,
            blocksize=blocksize,
            dtype="float32",
            latency="low",
            callback=self._callback,
        )
        self._running = True
        self._stream.start()
        logger.info(
            "Audio capture started: %s (index=%s) rate=%.0f ch=%d block=%d gain=%.2f",
            self.device_name,
            self.device_index,
            self._native_rate,
            self._capture_channels,
            blocksize,
            self.gain,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("Audio capture stopped")


def pcm_to_base64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")
