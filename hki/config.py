import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Server
HOST = os.getenv("HKI_HOST", "0.0.0.0")
PORT = int(os.getenv("HKI_PORT", "8765"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TRANSCRIPTION_MODEL = os.getenv("HKI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
FINAL_MODEL = os.getenv("HKI_FINAL_MODEL", "gpt-4o-mini")

# Audio
TARGET_SAMPLE_RATE = 24000
GAIN_DEFAULT = 1.0
GAIN_MIN = 0.1
GAIN_MAX = 4.0
LEVEL_METER_INTERVAL_MS = 100
AUDIO_CHUNK_MS = 100

# Translation
FINAL_HISTORY_LINES = 5

# VAD
VAD_SILENCE_DURATION_MS = int(os.getenv("HKI_VAD_SILENCE_DURATION_MS", "600"))
VAD_PREFIX_PADDING_MS = int(os.getenv("HKI_VAD_PREFIX_PADDING_MS", "300"))

# Realtime API — transcription sessions must NOT include ?model= in the URL
REALTIME_WS_URL = os.getenv(
    "HKI_REALTIME_WS_URL",
    "wss://api.openai.com/v1/realtime?intent=transcription",
)

# TTS
TTS_ENABLED = os.getenv("HKI_TTS_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TTS_MODEL = os.getenv("HKI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("HKI_TTS_VOICE", "onyx")

# Minimum /captions connections before transcription runs (operator excluded)
MIN_AUDIENCE_COUNT = int(os.getenv("HKI_MIN_AUDIENCE_COUNT", "1"))
TTS_SAMPLE_RATE = 24000
TTS_INSTRUCTIONS = os.getenv(
    "HKI_TTS_INSTRUCTIONS",
    "Hablá en español rioplatense argentino con voseo, entonación natural de Buenos Aires, "
    "pronunciación clara para iglesia.",
)
