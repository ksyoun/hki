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
FINAL_HISTORY_LINES = 7
CONTEXT_MODEL = os.getenv("HKI_CONTEXT_MODEL", "gpt-4o")

# Bible API (Midvash — Spanish NVI slug is nvies)
BIBLE_API_BASE = os.getenv("HKI_BIBLE_API_BASE", "https://api.midvash.com/v1")
BIBLE_VERSION = os.getenv("HKI_BIBLE_VERSION", "nvies")

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
TTS_PREP_BATCH_SIZE = max(1, min(3, int(os.getenv("HKI_TTS_PREP_BATCH_SIZE", "2"))))
TTS_PREP_TIMEOUT_MS = int(os.getenv("HKI_TTS_PREP_TIMEOUT_MS", "2500"))
TTS_PREP_MODEL = os.getenv("HKI_TTS_PREP_MODEL", "") or None
TTS_PLAYBACK_SPEED_THRESHOLD = int(os.getenv("HKI_TTS_PLAYBACK_SPEED_THRESHOLD", "3"))
TTS_PLAYBACK_SPEED_MAX = float(os.getenv("HKI_TTS_PLAYBACK_SPEED_MAX", "1.15"))

# Minimum /captions connections before transcription runs (operator excluded)
MIN_AUDIENCE_COUNT = int(os.getenv("HKI_MIN_AUDIENCE_COUNT", "1"))
TTS_SAMPLE_RATE = 24000

def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


TRANSLATION_LOG_PROMPTS = _env_bool("HKI_TRANSLATION_LOG_PROMPTS")
TTS_INSTRUCTIONS = os.getenv(
    "HKI_TTS_INSTRUCTIONS",
    "Hablá en español rioplatense argentino con voseo, entonación natural de Buenos Aires, "
    "pronunciación clara para iglesia.",
)
