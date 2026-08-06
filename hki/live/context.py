"""Build translation context: Bible refs, NVI fetch, gpt-4o summary."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from openai import AsyncOpenAI

from hki import config
from hki.live.bible_api import ParsedReference, fetch_all_nvi

logger = logging.getLogger(__name__)

EXTRACT_REFS_PROMPT = """Analiza el texto bíblico en coreano y extrae referencias para API.
Devuelve JSON: {"references": [{"book_ko": "마태복음", "book_slug": "mateo", "book_es": "Mateo", "chapter": 1, "verse_start": 1, "verse_end": 1}]}
- book_slug: slug para API Midvash (mateo, juan, romanos, genesis, ...)
- book_es: nombre español NVI (Mateo, Juan, Romanos, ...)
- verse_end igual a verse_start si es un solo versículo
- Si hay varios rangos, lista cada uno
- Si no hay referencia clara, inferir del contenido"""

BUILD_CONTEXT_PROMPT = """Eres preparador de contexto para intérprete de sermones coreanos al español argentino (rioplatense).
Con el texto bíblico coreano, versículos NVI en español y el manuscrito del sermón, genera contexto JSON.

Reglas:
- terminology[].es debe alinearse con NVI para citas bíblicas
- bible_books: nombres de libros KO → ES (Mateo, Juan, ...)
- sermon_summary: 3-5 frases
- outline: secciones del sermón
- style_notes: incluir voseo para sermón; citas y referencias usan nombres NVI (Mateo 1:1)

Devuelve JSON:
{
  "sermon_summary": "...",
  "outline": ["..."],
  "terminology": [{"ko": "...", "es": "...", "note": ""}],
  "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
  "style_notes": "..."
}"""


async def extract_references(bible_text: str) -> list[ParsedReference]:
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {"role": "system", "content": EXTRACT_REFS_PROMPT},
            {"role": "user", "content": bible_text.strip()},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1500,
    )
    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    refs: list[ParsedReference] = []
    for item in data.get("references") or []:
        try:
            refs.append(
                ParsedReference(
                    book_ko=str(item.get("book_ko", "")),
                    book_slug=str(item.get("book_slug", "")).lower().strip(),
                    book_es=str(item.get("book_es", "")).strip(),
                    chapter=int(item["chapter"]),
                    verse_start=int(item["verse_start"]),
                    verse_end=int(item.get("verse_end", item["verse_start"])),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Bad reference entry %s: %s", item, e)
    return refs


async def _llm_bible_fallback(bible_text_ko: str) -> list[dict]:
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Traduce el pasaje bíblico coreano al español NVI. "
                    "JSON: {\"verses\": [{\"ref\": \"Mateo 1:1\", \"text\": \"...\"}]}"
                ),
            },
            {"role": "user", "content": bible_text_ko},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=2000,
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return data.get("verses") or []


async def _build_context_llm(
    bible_text_ko: str,
    bible_es_nvi: list[dict],
    manuscript: str,
) -> dict:
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    nvi_block = "\n".join(
        f"{v.get('ref', '')}: {v.get('text', '')}" for v in bible_es_nvi
    )
    user = (
        f"Texto bíblico (coreano):\n{bible_text_ko}\n\n"
        f"Versículos NVI:\n{nvi_block}\n\n"
        f"Manuscrito del sermón:\n{manuscript}"
    )
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {"role": "system", "content": BUILD_CONTEXT_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=2500,
    )
    return json.loads(response.choices[0].message.content or "{}")


def format_passage_display(bible_text_ko: str, bible_es_nvi: list[dict]) -> dict:
    ko = bible_text_ko.strip()
    nvi_lines = [f"{v.get('ref', '')} {v.get('text', '')}".strip() for v in bible_es_nvi]
    nvi = "\n".join(nvi_lines).strip()
    return {"ko": ko, "nvi": nvi}


def format_context_for_system(context: dict) -> str:
    if not context:
        return ""

    parts = [
        "Contexto del sermón (usar para coherencia; no repetir todo):",
    ]
    summary = context.get("sermon_summary")
    if summary:
        parts.append(f"Resumen: {summary}")

    outline = context.get("outline") or []
    if outline:
        parts.append("Esquema: " + "; ".join(outline))

    books = context.get("bible_books") or []
    if books:
        book_line = ", ".join(f"{b.get('ko')}→{b.get('es')}" for b in books)
        parts.append(f"Libros (NVI): {book_line}")

    terms = context.get("terminology") or []
    if terms:
        parts.append("Terminología:")
        for t in terms[:40]:
            note = f" ({t.get('note')})" if t.get("note") else ""
            parts.append(f"  {t.get('ko')} → {t.get('es')}{note}")

    nvi = context.get("bible_es_nvi") or []
    if nvi:
        parts.append(
            "Referencias NVI — usar nombres español (Mateo 1:1, Juan 3:16). "
            "Al anunciar lectura: voseo + referencia. Al leer: texto NVI verbatim:"
        )
        for v in nvi:
            parts.append(f"  {v.get('ref', '')}: {v.get('text', '')}")

    style = context.get("style_notes")
    if style:
        parts.append(f"Notas: {style}")

    return "\n".join(parts)


async def build_translation_context(
    bible_text: str,
    manuscript: str,
) -> tuple[dict, dict, list[str]]:
    """
    Run Guardar pipeline. Returns (context, passage_display, warnings).
    """
    warnings: list[str] = []
    bible_text = bible_text.strip()
    manuscript = manuscript.strip()

    if not bible_text:
        raise ValueError("El texto bíblico es obligatorio")

    references = await extract_references(bible_text)
    if not references:
        raise ValueError("No se encontraron referencias bíblicas en el texto")

    bible_es_source = "bible_api"
    bible_es_nvi: list[dict] = []
    try:
        bible_es_nvi = await fetch_all_nvi(references)
    except Exception as e:
        logger.warning("Bible API fetch failed: %s", e)
        warnings.append("API Biblia falló — versículos generados por modelo")
        bible_es_source = "llm_fallback"
        bible_es_nvi = await _llm_bible_fallback(bible_text)

    if not bible_es_nvi:
        bible_es_source = "llm_fallback"
        bible_es_nvi = await _llm_bible_fallback(bible_text)
        if not bible_es_nvi:
            raise ValueError("No se pudieron obtener versículos en español")

    llm_ctx = await _build_context_llm(bible_text, bible_es_nvi, manuscript)

    ref_labels = [r.ref_label for r in references]
    context = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bible_text_ko": bible_text,
        "bible_references": ref_labels,
        "bible_es_nvi": bible_es_nvi,
        "bible_es_source": bible_es_source,
        "sermon_summary": llm_ctx.get("sermon_summary", ""),
        "outline": llm_ctx.get("outline") or [],
        "terminology": llm_ctx.get("terminology") or [],
        "bible_books": llm_ctx.get("bible_books") or [],
        "style_notes": llm_ctx.get("style_notes", ""),
    }

    passage_display = format_passage_display(bible_text, bible_es_nvi)
    return context, passage_display, warnings
