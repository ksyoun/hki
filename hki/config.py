import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


# Server
HOST = os.getenv("HKI_HOST", "0.0.0.0")
PORT = int(os.getenv("HKI_PORT", "8765"))
HTTP_GUIDE_PORT = int(os.getenv("HKI_HTTP_GUIDE_PORT", str(PORT + 1)))

_CERT_DIR = BASE_DIR / "data" / "certs"
_DEFAULT_CERT = _CERT_DIR / "hki.crt"
_DEFAULT_KEY = _CERT_DIR / "hki.key"


def _resolve_ssl_paths() -> tuple[str | None, str | None]:
    cert = os.getenv("HKI_SSL_CERTFILE", "").strip()
    key = os.getenv("HKI_SSL_KEYFILE", "").strip()
    auto = _env_bool("HKI_HTTPS") or (_DEFAULT_CERT.exists() and _DEFAULT_KEY.exists())
    if auto and not cert and _DEFAULT_CERT.exists():
        cert = str(_DEFAULT_CERT)
    if auto and not key and _DEFAULT_KEY.exists():
        key = str(_DEFAULT_KEY)
    if cert and not Path(cert).is_file():
        cert = ""
    if key and not Path(key).is_file():
        key = ""
    if cert and key:
        return cert, key
    return None, None


SSL_CERTFILE, SSL_KEYFILE = _resolve_ssl_paths()


def is_https() -> bool:
    return bool(SSL_CERTFILE and SSL_KEYFILE)


def public_scheme() -> str:
    return "https" if is_https() else "http"


def public_base_url(host: str, port: int | None = None) -> str:
    p = port if port is not None else PORT
    return f"{public_scheme()}://{host}:{p}"


def captions_public_url(host: str) -> str:
    return f"{public_base_url(host)}/captions"


def audience_join_url(host: str) -> str:
    """QR / share link — HTTP guide when main server uses HTTPS."""
    if is_https():
        q = f"?p={PORT}" if PORT != 8765 else ""
        return f"http://{host}:{HTTP_GUIDE_PORT}/join{q}"
    return f"http://{host}:{PORT}/join"

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TRANSCRIPTION_MODEL = os.getenv("HKI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
FINAL_MODEL = os.getenv("HKI_FINAL_MODEL", "gpt-4o-mini")

# Audio
TARGET_SAMPLE_RATE = 24000
GAIN_DEFAULT = 1.0
GAIN_MIN = 0.1
GAIN_MAX = 4.0
LEVEL_METER_INTERVAL_MS = int(os.getenv("HKI_LEVEL_METER_INTERVAL_MS", "50"))
LEVEL_PEAK_HOLD_MS = int(os.getenv("HKI_LEVEL_PEAK_HOLD_MS", "450"))
AUDIO_CHUNK_MS = max(10, int(os.getenv("HKI_AUDIO_CHUNK_MS", "20")))

# Translation
FINAL_HISTORY_LINES = 7
FINAL_TEMPERATURE = float(os.getenv("HKI_FINAL_TEMPERATURE", "0.1"))
RECOMBINE_TEMPERATURE = float(os.getenv("HKI_RECOMBINE_TEMPERATURE", "0.05"))
CONTEXT_MODEL = os.getenv("HKI_CONTEXT_MODEL", "gpt-4o")

# Bible API (Midvash — Spanish NVI slug is nvies)
BIBLE_API_BASE = os.getenv("HKI_BIBLE_API_BASE", "https://api.midvash.com/v1")
BIBLE_VERSION = os.getenv("HKI_BIBLE_VERSION", "nvies")

# VAD (classic / operator KO)
VAD_SILENCE_DURATION_MS = int(os.getenv("HKI_VAD_SILENCE_DURATION_MS", "600"))
VAD_PREFIX_PADDING_MS = int(os.getenv("HKI_VAD_PREFIX_PADDING_MS", "300"))
# Oración STT — shorter silence; experimental range 200–300
SENTENCE_VAD_SILENCE_DURATION_MS = int(
    os.getenv("HKI_SENTENCE_VAD_SILENCE_DURATION_MS", "250")
)
SENTENCE_VAD_PREFIX_PADDING_MS = int(
    os.getenv("HKI_SENTENCE_VAD_PREFIX_PADDING_MS", "300")
)

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
# Unified output composer (caption + TTS). HKI_TTS_PREP_* kept as aliases.
OUTPUT_BATCH_SIZE = max(
    1,
    min(
        3,
        int(
            os.getenv("HKI_OUTPUT_BATCH_SIZE")
            or os.getenv("HKI_TTS_PREP_BATCH_SIZE", "2")
        ),
    ),
)
OUTPUT_TIMEOUT_MS = int(
    os.getenv("HKI_OUTPUT_TIMEOUT_MS")
    or os.getenv("HKI_TTS_PREP_TIMEOUT_MS", "2500")
)
OUTPUT_PREP_MODEL = (
    os.getenv("HKI_OUTPUT_PREP_MODEL")
    or os.getenv("HKI_TTS_PREP_MODEL", "")
    or None
)
OUTPUT_RELEASE_BASE_MS = int(os.getenv("HKI_OUTPUT_RELEASE_BASE_MS", "1500"))
OUTPUT_RELEASE_MIN_MS = int(os.getenv("HKI_OUTPUT_RELEASE_MIN_MS", "700"))
CAPTION_MAX_LINES = max(3, int(os.getenv("HKI_CAPTION_MAX_LINES", "8")))
OUTPUT_ALWAYS_RECOMBINE = _env_bool("HKI_OUTPUT_ALWAYS_RECOMBINE")
# Closed fragments flush immediately. Open fragments wait this long for a partner.
OUTPUT_INCOMPLETE_TIMEOUT_MS = int(
    os.getenv("HKI_OUTPUT_INCOMPLETE_TIMEOUT_MS", "4500")
)
# Back-compat aliases
TTS_PREP_BATCH_SIZE = OUTPUT_BATCH_SIZE
TTS_PREP_TIMEOUT_MS = OUTPUT_TIMEOUT_MS
TTS_PREP_MODEL = OUTPUT_PREP_MODEL
TTS_PLAYBACK_SPEED_THRESHOLD = int(os.getenv("HKI_TTS_PLAYBACK_SPEED_THRESHOLD", "3"))
TTS_PLAYBACK_SPEED_MAX = float(os.getenv("HKI_TTS_PLAYBACK_SPEED_MAX", "1.15"))

# Minimum /captions connections before transcription runs (operator excluded)
MIN_AUDIENCE_COUNT = int(os.getenv("HKI_MIN_AUDIENCE_COUNT", "1"))
TTS_SAMPLE_RATE = 24000

# Utterance debounce after last transcription.completed — not a sentence detector.
SENTENCE_RELEASE_PAUSE_MS = int(os.getenv("HKI_SENTENCE_RELEASE_PAUSE_MS", "400"))
SENTENCE_MAX_BUFFER_MS = int(os.getenv("HKI_SENTENCE_MAX_BUFFER_MS", "8000"))
SENTENCE_MAX_PENDING = max(2, int(os.getenv("HKI_SENTENCE_MAX_PENDING", "6")))
# Open last KO fragment/unit waits this long (separate from classic incomplete).
SENTENCE_INCOMPLETE_TIMEOUT_MS = int(
    os.getenv("HKI_SENTENCE_INCOMPLETE_TIMEOUT_MS", "4500")
)
# Dual A/B: both default ON so live captions come from legacy and sentence is compared in /log
_PIPELINE_LEGACY = _env_bool("HKI_PIPELINE_LEGACY", "true")
_PIPELINE_SENTENCE = _env_bool("HKI_PIPELINE_SENTENCE", "true")
if not _PIPELINE_LEGACY and not _PIPELINE_SENTENCE:
    _PIPELINE_LEGACY = True
PIPELINE_LEGACY_ENABLED = _PIPELINE_LEGACY
PIPELINE_SENTENCE_ENABLED = _PIPELINE_SENTENCE


def live_pipeline_is_sentence() -> bool:
    """Captions/TTS come from sentence only when legacy is off."""
    return PIPELINE_SENTENCE_ENABLED and not PIPELINE_LEGACY_ENABLED


def translation_pipeline_status() -> str:
    if PIPELINE_LEGACY_ENABLED and PIPELINE_SENTENCE_ENABLED:
        return "both"
    if PIPELINE_SENTENCE_ENABLED:
        return "sentence"
    return "legacy"


TRANSLATION_LOG_PROMPTS = _env_bool("HKI_TRANSLATION_LOG_PROMPTS")
AUTO_SERMON_ON = _env_bool("HKI_AUTO_SERMON_ON")
TTS_INSTRUCTIONS = os.getenv(
    "HKI_TTS_INSTRUCTIONS",
    "Lee en voz alta ÚNICAMENTE el texto proporcionado. "
    "No agregues, omitas ni cambies palabras. "
    "Solo entonación natural y pronunciación clara.",
)
