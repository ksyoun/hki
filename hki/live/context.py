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

IMPORTANTE: El manuscrito es el sermón TAL COMO FUE ESCRITO por el pastor. La transcripción STT que
llegará después puede tener errores de reconocimiento de voz (homófonos, palabras similares mal
oídas). Tu contexto es la referencia que usarán las siguientes IAs para detectar y corregir esos
errores — no solo para traducir bien, sino para saber cuándo algo "no encaja" y corregirlo.

Reglas:
- terminology[].es debe alinearse con NVI para citas bíblicas
- terminology[] también debe incluir términos teológicos/técnicos clave del sermón con su traducción
  fijada (ej. términos médicos, palabras hebreas/griegas explicadas, conceptos doctrinales), para que
  no queden ambiguos o se traduzcan de forma distinta en cada fragmento
- bible_books: nombres de libros KO → ES (Mateo, Juan, ...)
- key_names: lista de nombres propios clave del sermón (personas bíblicas, lugares) con su forma
  correcta en coreano y español. Incluí también variantes fonéticas coreanas parecidas que un STT
  podría confundir entre sí (ej. 사라/사래/살아 — todas remiten a "Sara" en este sermón salvo que el
  contexto indique lo contrario)
- recurring_phrases: muletillas, fórmulas de apertura/cierre y patrones de dirección al público que
  el pastor usa con frecuencia (ej. "여러분", "아멘", "할렐루야", llamados a leer en voz alta),
  indicando su traducción habitual y si tienden a insertarse dentro de una frase o solo al inicio/final
- critical_sentences: 5-10 objetos {ko, es, note} de frases clave del manuscrito
  - ko: la frase tal cual en el manuscrito coreano
  - es: tu traducción de referencia al español rioplatense (ancla para la etapa de recombinación)
  - note: por qué es crítica (ej. "define el nombre Isaac", "cita directa de Sara")
  El campo es es el que usará la recombinación contra fragmentos ya traducidos — traducción fiel y
  natural, alineada con key_names y terminology (no literal palabra por palabra)
- sermon_summary: 3-5 frases
- outline: secciones del sermón
- style_notes: tono respetuoso congregacional (usted, hermanos); citas y referencias usan nombres NVI (Mateo 1:1)

Devuelve solo JSON (sin texto fuera del JSON):
{
  "sermon_summary": "...",
  "outline": ["..."],
  "terminology": [{"ko": "...", "es": "...", "note": ""}],
  "bible_books": [{"ko": "마태복음", "es": "Mateo"}],
  "key_names": [
    {"ko": "사라", "es": "Sara", "stt_variants": ["사래", "살아"], "note": ""}
  ],
  "recurring_phrases": [
    {"ko": "여러분", "es": "hermanos", "placement": "inicio|medio|final|cualquiera", "note": ""}
  ],
  "critical_sentences": [
    {
      "ko": "frase exacta del manuscrito en coreano...",
      "es": "traducción de referencia en español...",
      "note": "por qué es crítica"
    }
  ],
  "style_notes": "..."
}"""


ANCHOR_PRIORITY_RULES = (
    "Orden de prioridad ante conflicto entre fragmento y critical_sentence:\n"
    "1. Si el fragmento tiene sentido gramatical completo y coherente por sí mismo → variación "
    "legítima del sermón en vivo; NO reemplazar por la critical_sentence aunque diga algo distinto.\n"
    "2. Solo si el fragmento está roto, incompleto o incoherente → usar la critical_sentence "
    "correspondiente como ancla para reparar.\n"
    "Regla simple: la critical_sentence corrige gramática rota; nunca reemplaza contenido que ya "
    "tiene sentido propio."
)


def normalize_critical_sentences(raw: list | None) -> list[dict]:
    """Accept legacy string list or {ko, es, note} objects."""
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"ko": s, "es": "", "note": ""})
        elif isinstance(item, dict):
            ko = str(item.get("ko") or "").strip()
            es = str(item.get("es") or "").strip()
            note = str(item.get("note") or "").strip()
            if ko or es:
                out.append({"ko": ko, "es": es, "note": note})
    return out


def _format_critical_sentence_lines(critical: list[dict], *, for_recombine: bool) -> list[str]:
    lines: list[str] = []
    for item in critical[:10]:
        ko = item.get("ko", "")
        es = item.get("es", "")
        note = item.get("note", "")
        if for_recombine:
            if not es:
                continue
            note_txt = f" ({note})" if note else ""
            ko_txt = f" [ko: {ko}]" if ko else ""
            lines.append(f"  «{es}»{ko_txt}{note_txt}")
        else:
            if ko:
                es_txt = f" → ref: {es}" if es else ""
                note_txt = f" ({note})" if note else ""
                lines.append(f"  «{ko}»{es_txt}{note_txt}")
    return lines


def normalize_ko_stt(text: str, context: dict | None) -> str:
    """Replace key_names.stt_variants with canonical KO before translation."""
    if not context or not text:
        return text
    replacements: list[tuple[str, str]] = []
    for item in context.get("key_names") or []:
        canonical = str(item.get("ko") or "").strip()
        if not canonical:
            continue
        for variant in item.get("stt_variants") or []:
            v = str(variant).strip()
            if v and v != canonical:
                replacements.append((v, canonical))
    if not replacements:
        return text
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
    result = text
    for variant, canonical in replacements:
        result = result.replace(variant, canonical)
    return result


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
            config.CONTEXT_MODEL, 4000, reasoning="low", temperature=0.2
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
        "key_names": context.get("key_names") or [],
        "recurring_phrases": context.get("recurring_phrases") or [],
        "critical_sentences": normalize_critical_sentences(
            context.get("critical_sentences")
        ),
        "style_notes": context.get("style_notes", ""),
        "bible_references": context.get("bible_references") or [],
        "bible_es_source": context.get("bible_es_source", ""),
    }


def format_context_for_system(context: dict) -> str:
    if not context:
        return ""

    parts = [
        "Contexto del sermón (manuscrito = referencia; la transcripción STT puede errar homófonos):",
        ANCHOR_PRIORITY_RULES,
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

    key_names = context.get("key_names") or []
    if key_names:
        parts.append("Nombres clave (STT puede confundir variantes):")
        for item in key_names[:25]:
            ko = item.get("ko", "")
            es = item.get("es", "")
            variants = item.get("stt_variants") or []
            var_txt = ""
            if variants:
                var_txt = f" [STT: {', '.join(str(v) for v in variants[:6])}]"
            note = f" ({item.get('note')})" if item.get("note") else ""
            parts.append(f"  {ko} → {es}{var_txt}{note}")

    recurring = context.get("recurring_phrases") or []
    if recurring:
        parts.append("Frases recurrentes:")
        for item in recurring[:20]:
            ko = item.get("ko", "")
            es = item.get("es", "")
            placement = item.get("placement", "")
            place_txt = f" ({placement})" if placement else ""
            parts.append(f"  {ko} → {es}{place_txt}")

    critical = normalize_critical_sentences(context.get("critical_sentences"))
    if critical:
        parts.append(
            "Frases críticas del manuscrito (ancla — si STT de la misma idea es incoherente, "
            "reparar con este sentido al traducir; no reemplazar variaciones coherentes del vivo):"
        )
        parts.extend(_format_critical_sentence_lines(critical, for_recombine=False))

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


def format_context_for_recombine(
    context: dict,
    *,
    include_priority_rules: bool = True,
) -> str:
    """Minimal context for recombine: ES anchors, key names, style — no summary/NVI bodies."""
    if not context:
        return ""

    parts = [
        "Contexto para anclas (comparar fragmentos ES con critical_sentences.es):",
    ]
    if include_priority_rules:
        parts.append(ANCHOR_PRIORITY_RULES)

    key_names = context.get("key_names") or []
    if key_names:
        parts.append("Nombres clave:")
        for item in key_names[:25]:
            ko = item.get("ko", "")
            es = item.get("es", "")
            variants = item.get("stt_variants") or []
            var_txt = ""
            if variants:
                var_txt = f" [STT: {', '.join(str(v) for v in variants[:6])}]"
            note = f" ({item.get('note')})" if item.get("note") else ""
            parts.append(f"  {ko} → {es}{var_txt}{note}")

    critical = normalize_critical_sentences(context.get("critical_sentences"))
    crit_lines = _format_critical_sentence_lines(critical, for_recombine=True)
    if crit_lines:
        parts.append("Frases críticas (ancla ES — emparejar fragmentos traducidos):")
        parts.extend(crit_lines)

    style = context.get("style_notes")
    if style:
        parts.append(f"Notas de tono: {style}")

    return "\n".join(parts)


def format_context_for_sentence(context: dict) -> str:
    """Full sermon context + KO anchor block for sentence hold/release + translation."""
    if not context:
        return ""

    parts: list[str] = []
    system_block = format_context_for_system(context)
    if system_block.strip():
        parts.append(system_block)

    anchor_block = format_context_for_recombine(
        context, include_priority_rules=False
    )
    if anchor_block.strip():
        parts.append(anchor_block)

    return "\n\n".join(parts)


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
        "key_names": llm_ctx.get("key_names") or [],
        "recurring_phrases": llm_ctx.get("recurring_phrases") or [],
        "critical_sentences": normalize_critical_sentences(
            llm_ctx.get("critical_sentences")
        ),
        "style_notes": llm_ctx.get("style_notes", ""),
    }

    passage_display = format_passage_display(bible_text, bible_es_nvi)
    return context, passage_display, warnings
