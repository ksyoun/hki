"""Praise lyrics lookup (LLM draft) and caption-queue helpers.

Lyrics never go through TTS. Operator review/paste is required — the model
recalls well-known Spanish congregational text and can be wrong.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from hki import config
from hki.live.openai_client import chat_completion_extra, get_async_openai

logger = logging.getLogger(__name__)

NOTE = "♪"
MAX_SONGS = 8
MAX_SLIDES = 40
MAX_SLIDE_LEN = 500
MAX_LABEL_LEN = 80

LYRICS_LOOKUP_PROMPT = """Eres asistente de un operador de culto bilingüe (coreano → español rioplatense).
El operador te da una lista de canciones de alabanza (número de himnario coreano, título KO/ES/EN).

Devuelve SOLO JSON:
{
  "songs": [
    {
      "query": "texto original del operador",
      "label": "cómo mostrar el título al público (preferí el texto que escribió el operador, p.ej. 새찬송가 310장)",
      "title_es": "título en español si lo sabés, si no vacío",
      "confidence": "high|medium|low",
      "note": "aviso breve si hay duda o himnario no 1:1",
      "slides": ["estrofa o coro en español, 1-4 renglones por slide"]
    }
  ]
}

Reglas:
- Una entrada en songs por cada línea del operador, EN EL MISMO ORDEN.
- slides: letra congregacional en español, partida por estrofa/coro/puente. Cada slide será UN renglón de subtítulo.
- Si no estás seguro de la letra oficial, confidence=low, slides=[] (el operador pegará la letra). No inventes estrofas enteras con seguridad alta.
- No uses sitios web. No inventes números de himnario que no correspondan a la query.
- label debe servir para el chip y el subtítulo (el texto del operador, no lo traduzcas salvo que sea solo un título inglés muy conocido).
"""


def parse_song_queries(raw: str | list[str] | None) -> list[str]:
    if isinstance(raw, list):
        lines = [str(x).strip() for x in raw]
    else:
        lines = str(raw or "").splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s:
            out.append(s)
        if len(out) >= MAX_SONGS:
            break
    return out


def format_lyrics_caption(kind: str, text: str = "") -> str:
    """Audience caption text. mark = note only; others wrap inner text in notes."""
    kind = (kind or "").strip().lower()
    inner = (text or "").strip()
    if kind == "mark":
        return NOTE
    if kind == "fin":
        inner = "FIN"
    if not inner:
        return NOTE
    return f"{NOTE} {inner} {NOTE}"


def parse_json_object(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _normalize_slides(raw: Any) -> list[str]:
    slides: list[str] = []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("\r\n", "\n").split("\n\n")]
        raw_list = parts if len(parts) > 1 else raw.splitlines()
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raw_list = []
    for item in raw_list:
        if isinstance(item, dict):
            line = str(item.get("es") or item.get("text") or item.get("ko") or "").strip()
        else:
            line = str(item or "").strip()
        if not line:
            continue
        slides.append(_clip(line, MAX_SLIDE_LEN))
        if len(slides) >= MAX_SLIDES:
            break
    return slides


def normalize_song(raw: dict | None, fallback_query: str) -> dict:
    src = raw if isinstance(raw, dict) else {}
    query = _clip(str(src.get("query") or fallback_query or ""), MAX_LABEL_LEN)
    label = _clip(str(src.get("label") or query), MAX_LABEL_LEN) or query
    confidence = str(src.get("confidence") or "low").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    return {
        "query": query,
        "label": label,
        "title_es": _clip(str(src.get("title_es") or ""), MAX_LABEL_LEN),
        "confidence": confidence,
        "note": _clip(str(src.get("note") or ""), 240),
        "slides": _normalize_slides(src.get("slides")),
    }


def normalize_songs(raw: Any, queries: list[str]) -> list[dict]:
    if isinstance(raw, dict):
        raw_list = raw.get("songs")
        if not isinstance(raw_list, list):
            raw_list = []
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raw_list = []

    by_query: dict[str, dict] = {}
    unused: list[dict] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        q = str(item.get("query") or "").strip()
        if q and q not in by_query:
            by_query[q] = item
        else:
            unused.append(item)

    out: list[dict] = []
    unused_i = 0
    for i, query in enumerate(queries):
        src = by_query.get(query)
        if src is None and i < len(raw_list) and isinstance(raw_list[i], dict):
            src = raw_list[i]
        if src is None and unused_i < len(unused):
            src = unused[unused_i]
            unused_i += 1
        out.append(normalize_song(src, query))
    return out


def lyrics_chip_payload(songs: list[dict]) -> list[dict]:
    chips: list[dict] = []
    for song in songs:
        chips.append(
            {
                "label": song.get("label") or song.get("query") or "",
                "slide_count": len(song.get("slides") or []),
                "confidence": song.get("confidence") or "",
            }
        )
    return chips


def select_song(songs: list[dict], song_index: int) -> tuple[int, int, str]:
    if song_index < 0 or song_index >= len(songs):
        raise ValueError("Canción no válida")
    song = songs[song_index]
    label = str(song.get("label") or song.get("query") or "").strip()
    if not label:
        raise ValueError("Canción sin título")
    return song_index, -1, label


def next_verse(
    songs: list[dict], song_index: int | None, slide_index: int
) -> tuple[int, str] | None:
    if song_index is None or song_index < 0 or song_index >= len(songs):
        raise ValueError("Elija una canción")
    slides = songs[song_index].get("slides") or []
    nxt = slide_index + 1
    if nxt < 0 or nxt >= len(slides):
        return None
    return nxt, str(slides[nxt])


def prev_verse(
    songs: list[dict], song_index: int | None, slide_index: int
) -> tuple[int, str] | None:
    if song_index is None or song_index < 0 or song_index >= len(songs):
        raise ValueError("Elija una canción")
    slides = songs[song_index].get("slides") or []
    if not slides or slide_index < 0:
        return None
    prv = max(0, slide_index - 1)
    return prv, str(slides[prv])


async def lookup_lyrics(queries: list[str]) -> tuple[list[dict], list[str]]:
    """Return (songs aligned to queries, warnings)."""
    queries = parse_song_queries(queries)
    if not queries:
        raise ValueError("Escriba al menos una canción (una por línea)")
    if not config.OPENAI_API_KEY:
        raise ValueError("Falta OPENAI_API_KEY para buscar letras")

    warnings: list[str] = []
    client = get_async_openai()
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(queries))
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {"role": "system", "content": LYRICS_LOOKUP_PROMPT},
            {
                "role": "user",
                "content": "Canciones de hoy:\n" + numbered,
            },
        ],
        response_format={"type": "json_object"},
        **chat_completion_extra(
            config.CONTEXT_MODEL, 6000, reasoning="low", temperature=0.2
        ),
    )
    raw = response.choices[0].message.content or "{}"
    data = parse_json_object(raw)
    songs = normalize_songs(data, queries)
    for song in songs:
        if not song["slides"]:
            warnings.append(
                f"Sin letra para «{song['label']}» — péguela antes de transmitir"
            )
        elif song["confidence"] == "low":
            warnings.append(f"Revisar «{song['label']}» (confianza baja)")
    return songs, warnings
