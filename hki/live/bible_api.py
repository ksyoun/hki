"""Fetch Spanish Bible verses via Midvash API (NVI = nvies slug)."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

import httpx

from hki import config

logger = logging.getLogger(__name__)

MAX_FETCH_ROUNDS = 3


class BibleFetchErrorKind(enum.Enum):
    TRANSIENT = "transient"
    REFERENCE = "reference"
    FATAL = "fatal"


@dataclass
class ParsedReference:
    book_ko: str
    book_slug: str
    book_es: str
    chapter: int
    verse_start: int
    verse_end: int

    @property
    def ref_label(self) -> str:
        if self.verse_end != self.verse_start:
            return f"{self.book_es} {self.chapter}:{self.verse_start}-{self.verse_end}"
        return f"{self.book_es} {self.chapter}:{self.verse_start}"

    @property
    def verse_param(self) -> str:
        if self.verse_end != self.verse_start:
            return f"{self.verse_start}-{self.verse_end}"
        return str(self.verse_start)

    @property
    def key(self) -> tuple[str, int, int, int]:
        return (self.book_slug, self.chapter, self.verse_start, self.verse_end)


@dataclass
class BibleFetchFailure:
    ref: ParsedReference
    kind: BibleFetchErrorKind
    detail: str
    url: str


@dataclass
class FetchOutcome:
    success: bool = False
    verses: list[dict] | None = None
    failure: BibleFetchFailure | None = None


def _api_url(ref: ParsedReference) -> str:
    version = config.BIBLE_VERSION
    base = config.BIBLE_API_BASE.rstrip("/")
    return f"{base}/{version}/{ref.book_slug}/{ref.chapter}/{ref.verse_param}"


def classify_fetch_error(
    ref: ParsedReference, url: str, exc: BaseException
) -> BibleFetchFailure:
    if isinstance(exc, httpx.TimeoutException):
        return BibleFetchFailure(ref, BibleFetchErrorKind.TRANSIENT, "timeout", url)
    if isinstance(exc, httpx.ConnectError):
        return BibleFetchFailure(
            ref, BibleFetchErrorKind.TRANSIENT, "error de conexión", url
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (502, 503, 504, 429):
            return BibleFetchFailure(
                ref, BibleFetchErrorKind.TRANSIENT, f"HTTP {code}", url
            )
        if code in (401, 403):
            return BibleFetchFailure(ref, BibleFetchErrorKind.FATAL, f"HTTP {code}", url)
        if code in (404, 400):
            return BibleFetchFailure(
                ref, BibleFetchErrorKind.REFERENCE, f"HTTP {code}", url
            )
        if code >= 500:
            return BibleFetchFailure(
                ref, BibleFetchErrorKind.TRANSIENT, f"HTTP {code}", url
            )
        return BibleFetchFailure(
            ref, BibleFetchErrorKind.REFERENCE, f"HTTP {code}", url
        )
    if isinstance(exc, ValueError):
        return BibleFetchFailure(ref, BibleFetchErrorKind.REFERENCE, str(exc), url)
    return BibleFetchFailure(ref, BibleFetchErrorKind.TRANSIENT, str(exc), url)


async def try_fetch_nvi_verses(ref: ParsedReference) -> FetchOutcome:
    """Single API attempt with classified failure (no internal retry loop)."""
    url = _api_url(ref)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning("Bible API failed for %s: %s", ref.ref_label, e)
        return FetchOutcome(failure=classify_fetch_error(ref, url, e))

    data = payload.get("data") or {}
    verses = data.get("verses") or []
    if not verses and data.get("text"):
        verses = [data["text"]]

    if not verses:
        failure = BibleFetchFailure(
            ref,
            BibleFetchErrorKind.REFERENCE,
            "sin versículos en la respuesta",
            url,
        )
        return FetchOutcome(failure=failure)

    results: list[dict] = []
    start = ref.verse_start
    for i, text in enumerate(verses):
        verse_num = start + i
        label = f"{ref.book_es} {ref.chapter}:{verse_num}"
        results.append({"ref": label, "text": text.strip()})
    return FetchOutcome(success=True, verses=results)
