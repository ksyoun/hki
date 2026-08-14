"""Load audio files and replay as PCM for pipeline testing."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

from hki import config

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 50 * 1024 * 1024


def load_audio_file(path: Path) -> tuple[bytes, float]:
    """Load audio file as 24 kHz mono PCM16. Returns (pcm_bytes, duration_sec)."""
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("El archivo es demasiado grande (máx. 50 MB).")

    suffix = path.suffix.lower()
    if suffix == ".wav":
        sr, samples = wavfile.read(str(path))
    elif shutil.which("ffmpeg"):
        samples, sr = _decode_with_ffmpeg(path)
    else:
        raise ValueError(
            "Solo se admite WAV. Para MP3/M4A instale ffmpeg."
        )

    mono = _to_mono_int16(samples, sr)
    duration = len(mono) / config.TARGET_SAMPLE_RATE
    return mono.tobytes(), duration


def _decode_with_ffmpeg(path: Path) -> tuple[np.ndarray, int]:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(config.TARGET_SAMPLE_RATE),
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")[-300:]
        raise ValueError(f"Error al convertir audio: {err}")
    samples = np.frombuffer(proc.stdout, dtype=np.int16)
    return samples, config.TARGET_SAMPLE_RATE


def _to_mono_int16(samples: np.ndarray, sr: int) -> np.ndarray:
    if samples.ndim > 1:
        samples = samples[:, 0]

    if samples.dtype == np.int16:
        data = samples.astype(np.float32)
    elif np.issubdtype(samples.dtype, np.floating):
        data = np.clip(samples.astype(np.float32), -1.0, 1.0) * 32767.0
    else:
        peak = float(np.max(np.abs(samples))) or 1.0
        data = samples.astype(np.float32) / peak * 32767.0

    if sr != config.TARGET_SAMPLE_RATE:
        num = int(len(data) * config.TARGET_SAMPLE_RATE / sr)
        data = signal.resample(data, num)

    return np.clip(data, -32768, 32767).astype(np.int16)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
    samples = np.clip(samples * gain, -1.0, 1.0)
    return (samples * 32767).astype(np.int16).tobytes()
