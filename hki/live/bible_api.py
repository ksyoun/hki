"""Fetch Spanish Bible verses via Midvash API (NVI = nvies slug)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from hki import config

logger = logging.getLogger(__name__)


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


async def fetch_nvi_verses(ref: ParsedReference) -> list[dict]:
    """Return list of {ref, text} for one reference range."""
    version = config.BIBLE_VERSION
    base = config.BIBLE_API_BASE.rstrip("/")
    url = f"{base}/{version}/{ref.book_slug}/{ref.chapter}/{ref.verse_param}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning("Bible API failed for %s: %s", ref.ref_label, e)
        raise

    data = payload.get("data") or {}
    verses = data.get("verses") or []
    if not verses and data.get("text"):
        verses = [data["text"]]

    if not verses:
        raise ValueError(f"No verses returned for {ref.ref_label}")

    results: list[dict] = []
    start = ref.verse_start
    for i, text in enumerate(verses):
        verse_num = start + i
        label = f"{ref.book_es} {ref.chapter}:{verse_num}"
        results.append({"ref": label, "text": text.strip()})
    return results


async def fetch_all_nvi(references: list[ParsedReference]) -> list[dict]:
    import asyncio

    if not references:
        return []

    batches = await asyncio.gather(
        *[fetch_nvi_verses(r) for r in references],
        return_exceptions=True,
    )
    all_verses: list[dict] = []
    for ref, batch in zip(references, batches):
        if isinstance(batch, Exception):
            logger.warning("Skipping reference %s: %s", ref.ref_label, batch)
            continue
        all_verses.extend(batch)
    return all_verses
