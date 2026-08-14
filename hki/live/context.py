"""Build translation context: Bible refs, NVI fetch, gpt-4o summary."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from hki import config
from hki.live.bible_api import (
    BibleFetchErrorKind,
    BibleFetchFailure,
    MAX_FETCH_ROUNDS,
    ParsedReference,
    try_fetch_nvi_verses,
)
from hki.live.openai_client import chat_completion_extra, get_async_openai

logger = logging.getLogger(__name__)

EXTRACT_REFS_PROMPT = """Analiza el texto bíblico en coreano y extrae referencias para la API Midvash (versión nvies, español NVI).

Devuelve JSON: {"references": [{"book_ko": "마태복음", "book_slug": "mateo", "book_es": "Mateo", "chapter": 1, "verse_start": 1, "verse_end": 1}]}

Reglas para book_slug (minúsculas, español, sin espacios):
- Génesis→genesis, Éxodo→exodo, Mateo→mateo, Marcos→marcos, Lucas→lucas, Juan→juan
- Hechos→hechos, Romanos→romanos, 1 Corintios→1corintios, 2 Corintios→2corintios
- NUNCA inglés: no romans, matthew, john, genesis en inglés si difiere
- Salmos→salmos, Isaías→isaias, Jeremías→jeremias, Apocalipsis→apocalipsis

book_es: nombre NVI en español (Mateo, Juan, Romanos, ...)
verse_end = verse_start si es un solo versículo
Si hay varios rangos en el texto, lista cada uno
Si no hay referencia explícita, inferir del contenido del pasaje"""

EXTRACT_REFS_RETRY_PROMPT = """
CORRECCIÓN REQUERIDA: la API rechazó referencias anteriores (404 o sin versículos).
Reanaliza el texto coreano y devuelve referencias CORREGIDAS.

- Revisa book_slug (español Midvash: mateo, romanos, juan — no romans, matthew)
- Revisa chapter y verse_start/verse_end según el texto coreano
- No repitas exactamente las referencias fallidas si los números o slug eran incorrectos
- URL válida: /nvies/{book_slug}/{chapter}/{verse} o rango como 28-30"""

BUILD_CONTEXT_PROMPT = """Eres preparador de contexto para intérprete de sermones coreanos al español argentino (rioplatense).
Con el texto bíblico coreano, versículos NVI en español y el manuscrito del sermón, genera contexto JSON.

Reglas:
- terminology[].es debe alinearse con NVI para citas bíblicas
- bible_books: nombres de libros KO → ES (Mateo, Juan, ...)
- sermon_summary: 3-5 frases
- outline: secciones del sermón
- style_notes: tono respetuoso congregacional (usted, hermanos); citas y referencias usan nombres NVI (Mateo 1:1)

Devuelve JSON:
{
  "sermon_summary": "...",
  "outline": ["..."],
  "terminology": [{"ko": "...", "es": "...", "note": ""}],
  "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
  "style_notes": "..."
}"""


async def extract_references(
    bible_text: str,
    api_failures: list[BibleFetchFailure] | None = None,
    attempt: int = 0,
) -> list[ParsedReference]:
    system = EXTRACT_REFS_PROMPT
    if api_failures:
        system += EXTRACT_REFS_RETRY_PROMPT
        failure_lines = "\n".join(
            (
                f"- {f.ref.ref_label}: slug={f.ref.book_slug!r} "
                f"cap={f.ref.chapter} vers={f.ref.verse_start}-{f.ref.verse_end} "
                f"→ {f.detail} | URL: {f.url}"
            )
            for f in api_failures
        )
        user_content = (
            f"Texto bíblico (coreano):\n{bible_text.strip()}\n\n"
            f"Referencias que fallaron en la API (corregir):\n{failure_lines}"
        )
    else:
        user_content = bible_text.strip()

    temperature = min(0.25, 0.1 + attempt * 0.05)
    client = get_async_openai()
    response = await client.chat.completions.create(
        model=config.CONTEXT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        **chat_completion_extra(
            config.CONTEXT_MODEL,
            1500,
            reasoning="low",
            temperature=temperature,
        ),
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


async def _resolve_nvi_verses(
    bible_text: str,
) -> tuple[list[ParsedReference], list[dict], list[str]]:
    """
    Step 1 + Step 2 with up to MAX_FETCH_ROUNDS rounds.
    Reference errors → re-extract with API failure feedback.
    Transient errors → retry same reference on the next round.
    """
    import asyncio

    warnings: list[str] = []
    references = await extract_references(bible_text, api_failures=None, attempt=0)
    if not references:
        return [], [], warnings

    all_verses: list[dict] = []
    fetched_keys: set[tuple[str, int, int, int]] = set()

    for round_num in range(MAX_FETCH_ROUNDS):
        pending = [r for r in references if r.key not in fetched_keys]
        if not pending:
            break

        outcomes = await asyncio.gather(*[try_fetch_nvi_verses(r) for r in pending])

        reference_failures: list[BibleFetchFailure] = []
        had_transient = False

        for ref, outcome in zip(pending, outcomes):
            if outcome.success and outcome.verses:
                all_verses.extend(outcome.verses)
                fetched_keys.add(ref.key)
                continue
            if not outcome.failure:
                continue
            failure = outcome.failure
            if failure.kind == BibleFetchErrorKind.FATAL:
                raise ValueError(
                    f"API Biblia no autorizada ({failure.detail}). "
                    "Revisá HKI_BIBLE_API_BASE / acceso a Midvash."
                )
            elif failure.kind == BibleFetchErrorKind.REFERENCE:
                reference_failures.append(failure)
            else:
                had_transient = True

        is_last_round = round_num >= MAX_FETCH_ROUNDS - 1

        if reference_failures and not is_last_round:
            logger.info(
                "Re-extracting references after %d API reference errors (round %d)",
                len(reference_failures),
                round_num + 1,
            )
            references = await extract_references(
                bible_text,
                api_failures=reference_failures,
                attempt=round_num + 1,
            )
            if not references:
                warnings.append(
                    "Reextracción sin referencias — se intentará fallback de modelo"
                )
            if had_transient:
                await asyncio.sleep(0.4 * (round_num + 1))
            continue

        if had_transient and not is_last_round:
            await asyncio.sleep(0.4 * (round_num + 1))
            continue

        for failure in reference_failures:
            warnings.append(
                f"Referencia no encontrada en API: {failure.ref.ref_label} ({failure.detail})"
            )
        if had_transient:
            still_pending = [r for r in references if r.key not in fetched_keys]
            for ref in still_pending:
                warnings.append(
                    f"API Biblia falló tras {MAX_FETCH_ROUNDS} intentos: {ref.ref_label}"
                )
        break

    return references, all_verses, warnings


async def _llm_bible_fallback(bible_text_ko: str) -> list[dict]:
    client = get_async_openai()
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
        **chat_completion_extra(config.CONTEXT_MODEL, 2000, reasoning="low"),
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return data.get("verses") or []


async def _build_context_llm(
    bible_text_ko: str,
    bible_es_nvi: list[dict],
    manuscript: str,
) -> dict:
    client = get_async_openai()
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
        **chat_completion_extra(
            config.CONTEXT_MODEL, 2500, reasoning="low", temperature=0.2
        ),
    )
    return json.loads(response.choices[0].message.content or "{}")


def format_passage_display(bible_text_ko: str, bible_es_nvi: list[dict]) -> dict:
    ko = bible_text_ko.strip()
    nvi_lines = [f"{v.get('ref', '')} {v.get('text', '')}".strip() for v in bible_es_nvi]
    nvi = "\n".join(nvi_lines).strip()
    return {"ko": ko, "nvi": nvi}


def format_context_display(context: dict | None) -> dict | None:
    """UI payload for Resumen del contexto (no full NVI verse text)."""
    if not context:
        return None
    return {
        "sermon_summary": context.get("sermon_summary", ""),
        "outline": context.get("outline") or [],
        "terminology": context.get("terminology") or [],
        "bible_books": context.get("bible_books") or [],
        "style_notes": context.get("style_notes", ""),
        "bible_references": context.get("bible_references") or [],
        "bible_es_source": context.get("bible_es_source", ""),
    }


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
            "Al anunciar lectura: tono respetuoso + referencia. Al leer: texto NVI verbatim:"
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
    Run Contextualizar pipeline. Returns (context, passage_display, warnings).
    """
    warnings: list[str] = []
    bible_text = bible_text.strip()
    manuscript = manuscript.strip()

    if not bible_text:
        raise ValueError("El texto bíblico es obligatorio")

    ref_labels: list[str] = []
    bible_es_source = "bible_api"
    bible_es_nvi: list[dict] = []

    references, bible_es_nvi, api_warnings = await _resolve_nvi_verses(bible_text)
    warnings.extend(api_warnings)

    if not references:
        warnings.append(
            "No se encontraron referencias bíblicas — versículos generados por modelo"
        )
        bible_es_source = "llm_fallback"
        if not bible_es_nvi:
            bible_es_nvi = await _llm_bible_fallback(bible_text)
    else:
        ref_labels = [r.ref_label for r in references]
        if bible_es_nvi and api_warnings:
            bible_es_source = "bible_api_partial"
        if not bible_es_nvi:
            warnings.append("API Biblia incompleta — versículos generados por modelo")
            bible_es_source = "llm_fallback"
            bible_es_nvi = await _llm_bible_fallback(bible_text)

    if not bible_es_nvi:
        raise ValueError("No se pudieron obtener versículos en español")

    llm_ctx = await _build_context_llm(bible_text, bible_es_nvi, manuscript)
    context = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
