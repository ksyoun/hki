"""Translate caption-row Korean to Spanish (operator draft)."""

from __future__ import annotations

import logging

from hki import config
from hki.live.context import _resolve_nvi_verses
from hki.live.lyrics import parse_json_object
from hki.live.openai_client import chat_completion_extra, get_async_openai
from hki.v2.rundown import MAX_TEXT, _clip

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
        if slide.get("cue") not in ("caption", "sermon"):
            warnings.append(f"Hoja {idx + 1} no es subtítulo")
            continue
        ko = str(slide.get("ko") or slide.get("label") or "").strip()
        try:
            slide["es"] = await translate_ko_line(
                ko,
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


STUB_KO = frozenset(
    {
        "주일예배안내",
        "인트로찬양",
        "찬양1",
        "찬양2",
        "찬양3",
        "찬양4",
        "찬양5",
        "예배안내",
        "사도신경",
        "대표기도",
        "설교",
    }
)


def should_convert_caption(slide: dict) -> bool:
    if slide.get("cue") != "caption":
        return False
    append_ko = str(slide.get("append_ko") or "").strip()
    body = body_without_append(str(slide.get("ko") or ""), append_ko)
    if not body:
        return False
    if body in STUB_KO:
        return False
    label = str(slide.get("label") or "").strip()
    if label and body == label:
        return False
    return True


def should_convert_sermon(slide: dict) -> bool:
    if slide.get("cue") != "sermon":
        return False
    body = str(slide.get("ko") or slide.get("label") or "").strip()
    if not body or body in STUB_KO:
        return False
    return True


def body_without_append(text: str, append: str) -> str:
    text = (text or "").strip()
    append = (append or "").strip()
    if append and text.endswith(append):
        return text[: len(text) - len(append)].rstrip()
    return text


def join_body_append(body: str, append: str) -> str:
    body = (body or "").strip()
    append = (append or "").strip()
    if not append:
        return body
    if not body:
        return append
    return f"{body}\n{append}"


def format_nvi_caption(verses: list[dict]) -> str:
    lines: list[str] = []
    for verse in verses:
        if not isinstance(verse, dict):
            continue
        line = f"{verse.get('ref', '')} {verse.get('text', '')}".strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


async def fill_nvi_indices(slides: list[dict], indices: list[int]) -> tuple[list[int], list[str]]:
    """Fill caption `es` from Midvash NVI, then append liturgy 응송 (Amén only on 응송)."""
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
        append_ko = str(slide.get("append_ko") or "").strip()
        append_es = str(slide.get("append_es") or "").strip()
        body = body_without_append(str(slide.get("ko") or ""), append_ko)
        if not body:
            warnings.append(f"Hoja {idx + 1}: escriba el texto bíblico")
            continue
        try:
            _refs, verses, api_warnings = await _resolve_nvi_verses(body)
        except ValueError as e:
            warnings.append(f"Hoja {idx + 1}: {e}")
            continue
        except Exception as e:
            logger.exception("v2 caption NVI failed index=%s", idx)
            warnings.append(f"Hoja {idx + 1}: {e}")
            continue
        nvi = format_nvi_caption(verses or [])
        if not nvi:
            extra = "; ".join(api_warnings) if api_warnings else "sin versículos NVI"
            warnings.append(f"Hoja {idx + 1}: {extra}")
            continue
        slide["ko"] = _clip(join_body_append(body, append_ko), MAX_TEXT)
        slide["es"] = _clip(join_body_append(nvi, append_es), MAX_TEXT)
        updated.append(idx)
        for msg in api_warnings:
            warnings.append(f"Hoja {idx + 1}: {msg}")
    return updated, warnings


async def convertir_captions(
    slides: list[dict],
    *,
    context: dict | None = None,
    passage_display: dict | None = None,
) -> tuple[list[int], list[str]]:
    """Fill every caption that has Korean: NVI if liturgy append, else LLM ES."""
    nvi_idx: list[int] = []
    es_idx: list[int] = []
    for i, slide in enumerate(slides):
        if should_convert_caption(slide):
            if str(slide.get("append_ko") or "").strip() or str(slide.get("append_es") or "").strip():
                nvi_idx.append(i)
            else:
                es_idx.append(i)
        elif should_convert_sermon(slide):
            es_idx.append(i)
    if not nvi_idx and not es_idx:
        return [], ["No hay hojas en coreano para convertir"]
    updated: list[int] = []
    warnings: list[str] = []
    if nvi_idx:
        u, w = await fill_nvi_indices(slides, nvi_idx)
        updated.extend(u)
        warnings.extend(w)
    if es_idx:
        u, w = await translate_indices(
            slides,
            es_idx,
            context=context,
            passage_display=passage_display,
        )
        updated.extend(u)
        warnings.extend(w)
    return updated, warnings
