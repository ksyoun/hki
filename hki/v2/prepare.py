"""Translate caption-row Korean to Spanish (operator draft)."""

from __future__ import annotations

import logging

from hki import config
from hki.live.lyrics import parse_json_object
from hki.live.openai_client import chat_completion_extra, get_async_openai

logger = logging.getLogger(__name__)

TRANSLATE_PROMPT = """Eres asistente de un operador de culto bilingüe (coreano → español rioplatense).
El operador te da el texto coreano de UNA diapositiva de subtítulo (letra, anuncio o versículo).

Devuelve SOLO JSON: {"es": "texto para el subtítulo, 1-4 renglones"}

Reglas:
- Español rioplatense, listo para proyectar/subtítulo. Sin comillas ni preámbulo.
- Si el texto parece letra congregacional conocida, preferí la letra oficial en español (no una traducción literal rara).
- Si parece versículo y te dan texto NVI de referencia, usá ese NVI cuando coincida; no parafrasees la Biblia.
- Si no estás seguro, traducir literal y breve. No inventes estrofas enteras.
"""


def _nvi_blob(context: dict | None, passage_display: dict | None) -> str:
    parts: list[str] = []
    if isinstance(passage_display, dict):
        nvi = str(passage_display.get("nvi") or passage_display.get("es") or "").strip()
        if nvi:
            parts.append(nvi)
    if isinstance(context, dict):
        verses = context.get("bible_es_nvi") or []
        if isinstance(verses, list):
            for v in verses:
                if not isinstance(v, dict):
                    continue
                line = f"{v.get('ref', '')}: {v.get('text', '')}".strip(": ").strip()
                if line:
                    parts.append(line)
    return "\n".join(parts)[:4000]


async def translate_ko_line(
    ko: str,
    *,
    context: dict | None = None,
    passage_display: dict | None = None,
) -> str:
    ko = (ko or "").strip()
    if not ko:
        raise ValueError("Escriba el texto coreano de la hoja")
    if not config.OPENAI_API_KEY:
        raise ValueError("Falta OPENAI_API_KEY para traducir")

    client = get_async_openai()
    nvi = _nvi_blob(context, passage_display)
    user = "Texto coreano:\n" + ko
    if nvi:
        user += "\n\nNVI de referencia (si aplica):\n" + nvi
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {"role": "system", "content": TRANSLATE_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        **chat_completion_extra(
            config.CONTEXT_MODEL, 800, reasoning="low", temperature=0.2
        ),
    )
    raw = response.choices[0].message.content or "{}"
    data = parse_json_object(raw)
    es = str(data.get("es") or "").strip()
    if not es:
        raise ValueError("La traducción volvió vacía")
    return es


async def translate_indices(
    slides: list[dict],
    indices: list[int],
    *,
    context: dict | None = None,
    passage_display: dict | None = None,
) -> tuple[list[int], list[str]]:
    warnings: list[str] = []
    updated: list[int] = []
    for idx in indices:
        if idx < 0 or idx >= len(slides):
            warnings.append(f"Índice {idx} fuera de rango")
            continue
        slide = slides[idx]
        if slide.get("cue") != "caption":
            warnings.append(f"Hoja {idx + 1} no es subtítulo")
            continue
        try:
            slide["es"] = await translate_ko_line(
                str(slide.get("ko") or ""),
                context=context,
                passage_display=passage_display,
            )
            updated.append(idx)
        except ValueError as e:
            warnings.append(f"Hoja {idx + 1}: {e}")
        except Exception as e:
            logger.exception("v2 caption translate failed index=%s", idx)
            warnings.append(f"Hoja {idx + 1}: {e}")
    return updated, warnings
