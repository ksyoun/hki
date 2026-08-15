"""Unified hold/release + KO→ES prompts for the sentence translation pipeline."""

from __future__ import annotations

from hki.live.context import ANCHOR_PRIORITY_RULES, format_context_for_sentence
from hki.live.translate import (
    ARGENTINE_RULES,
    FALLBACK_SYSTEM,
    GENERAL_SERVICE_RULES,
    GENERAL_TASK_HEADER,
    PROMPT_MODE_LABELS,
    TRANSLATION_TASK_HEADER,
    _prompt_mode_for,
    _prompt_preview,
)

# Translation (KO→ES, register, STT) + recombine faithfulness, rewritten for KO + hold/release.
# Do not paste RECOMBINE_SYSTEM: that prompt assumes fragments already in Spanish.
SENTENCE_FAITHFULNESS_RULES = (
    "Fidelidad al liberar (traduce los fragmentos KO; no edites un sermón ya en español):\n"
    "- Fuente: solo los fragmentos KO 1..through_index. El historial ES es continuidad de términos, "
    "no contenido extra a insertar.\n"
    "- Usa ÚNICAMENTE las ideas de esos fragmentos; NO inventes contenido nuevo\n"
    "- NO agregues explicaciones, saludos, vocativos (hermanos, amados) ni comentarios que no "
    "estén en el KO\n"
    "- NO cambies el significado; no «mejores» el sermón\n"
    "- sermon_summary, outline y notas de estilo NO completan huecos: sirven para nombres, "
    "terminología y tono, no para añadir cláusulas que el STT no dijo\n"
    "- bible_es_nvi: verbatim SOLO si el KO es claramente lectura del pasaje; si solo mencionan "
    "la referencia, traduce esa mención, no recites el versículo\n"
    "- Una sola línea natural para subtítulo y TTS\n"
    "- Puede unir fragmentos con conectores mínimos, quitar repeticiones obvias, puntuación oral\n"
    "- Mantener usted/ustedes; NO convertir a voseo\n"
    "- Referencias bíblicas NVI exactas (Mateo 1:1) cuando el KO las trae o es lectura\n"
    "- Si el KO está incoherente (sujeto/verbo faltante) Y coincide temáticamente con una "
    "critical_sentence: puede corregir gramática mínima (reponer sujeto, arreglar oración rota) "
    "para alinear con el ancla — SIN agregar ideas, ejemplos o datos que no estén en el KO ni en "
    "esa critical_sentence\n"
    "- Esa corrección es excepción limitada: si no hay ancla clara, NO adivines. Preferí hold; "
    "si debes liberar (timeout), traduce lo que hay y marca flags incierto — deja extraño antes "
    "que inventar\n"
)

# Shared KO completeness signals. JSON stays action hold|release + through_index (no extra status).
SENTENCE_COMPLETENESS_RULES = (
    "Completitud KO (action solo hold|release; no inventes otros status):\n"
    "RELEASE — hay predicado y cierre de enunciado, o una unidad corta completa:\n"
    "- Cierre verbal: 습니다/ㅂ니다, 요, 다, 라, 죠, 세요\n"
    "- Unidad corta: 아멘, 할렐루야, 안녕하세요, 감사합니다, 기도하겠습니다, "
    "읽어 드리겠습니다 / 읽겠습니다\n"
    "HOLD — el último fragmento no cierra; espera el siguiente:\n"
    "- Conectivas: 고, 서, 며, 면서, 는데, 니까, 도록, 려고, 러 (sin cierre después)\n"
    "- Solo sujeto/objeto + partícula (은/는/이/가/을/를/의/에) sin predicado\n"
    "- Cita a medias (…라고 / …하고 말씀 without the quoted content or the verb)\n"
    "- Adverbio o nexo solo (정말, 그러나, 그때, 그러므로)\n"
    "through_index (1-based) — el backend conserva lo no liberado:\n"
    "- Si 1..k ya cierran y k+1..N están incompletos: action=release, through_index=k, "
    "es=UNA línea solo de 1..k\n"
    "- Si nada cierra: action=hold, through_index=0, es vacío\n"
    "- Timeout: release de lo que hay, sin completar el pensamiento\n"
)

SENTENCE_RELEASE_GENERAL = (
    "Política hold/release (modo servicio general):\n"
    "- Fragmentos STT coreanos numerados (1..N) en orden\n"
    "- Saludo, una línea de oración, anuncio breve: release aunque sea 1 fragmento SI cierra "
    "(señales de completitud). Si termina en conectiva o partícula: hold\n"
    "- No completes la frase con conocimiento general ni con el historial\n"
)

SENTENCE_RELEASE_SERMON = (
    "Política hold/release (modo sermón):\n"
    "- Acumula hasta un cierre (습니다/다/요) o una cláusula con predicado usable\n"
    "- Mitad de frase: hold. No completes con sermon_summary ni con critical_sentences\n"
    "- Si KO coincide temáticamente con critical_sentences o key_names: corrige STT "
    "(homófonos, nombres) ANTES de traducir — eso no es inventar contenido\n"
    "- Lectura de pasaje: NVI verbatim (bible_es_nvi) solo cuando el KO es la lectura misma; "
    "un versículo anunciado+leído puede release como unidad\n"
    "- KO incompleto + ancla clara: gramática mínima en ES. Sin ancla: hold o flags incierto\n"
)

SENTENCE_OUTPUT_SCHEMA = (
    "Responde SOLO JSON válido con este esquema:\n"
    '{"action":"hold"|"release","through_index":0,"es":"","flags":[]}\n'
    "- hold: through_index=0, es vacío o ignorado\n"
    "- release: through_index=k (1..N, puede ser < N), es=UNA línea ES de fragmentos 1..k "
    "para subtítulo/TTS. Los fragmentos k+1..N quedan pendientes\n"
    "- flags opcional: anchor_repair, incierto (mismo uso que recombine_flags)\n"
    "- No uses campos extra (status, HOLD/RELEASE en mayúsculas, etc.)\n"
)

SENTENCE_USER_HEADER = (
    "Fragmentos STT (coreano, en orden):\n"
    "{fragments}\n\n"
    "{history_block}\n"
    "Decide action hold o release según completitud KO. Si 1..k cierran y el resto no, "
    "release con through_index=k. Si release, traduce SOLO 1..through_index a UNA línea. "
    "No uses el resumen ni el historial para añadir ideas."
)

HISTORY_HEADER = (
    "[Historial reciente — continuidad de términos; no copies contenido que no esté en los "
    "fragmentos actuales]\n"
)


def _general_system() -> str:
    return (
        f"{GENERAL_TASK_HEADER}\n\n{SENTENCE_FAITHFULNESS_RULES}\n\n"
        f"{GENERAL_SERVICE_RULES}\n\n{SENTENCE_COMPLETENESS_RULES}\n\n"
        f"{SENTENCE_RELEASE_GENERAL}\n\n{ANCHOR_PRIORITY_RULES}\n\n{SENTENCE_OUTPUT_SCHEMA}"
    )


def _sermon_context_system(context: dict) -> str:
    ctx_block = format_context_for_sentence(context)
    return (
        f"{TRANSLATION_TASK_HEADER}\n\n{ARGENTINE_RULES}\n\n{ctx_block}\n\n"
        f"{SENTENCE_FAITHFULNESS_RULES}\n\n{SENTENCE_COMPLETENESS_RULES}\n\n"
        f"{SENTENCE_RELEASE_SERMON}\n\n{SENTENCE_OUTPUT_SCHEMA}"
    )


def _sermon_fallback_system() -> str:
    return (
        f"{FALLBACK_SYSTEM}\n\n{SENTENCE_FAITHFULNESS_RULES}\n\n"
        f"{SENTENCE_COMPLETENESS_RULES}\n\n{SENTENCE_RELEASE_SERMON}\n\n"
        f"{ANCHOR_PRIORITY_RULES}\n\n{SENTENCE_OUTPUT_SCHEMA}"
    )


def build_sentence_system_prompt(sermon_mode: bool, context: dict | None) -> str:
    if not sermon_mode:
        return _general_system()
    if not context:
        return _sermon_fallback_system()
    return _sermon_context_system(context)


def format_fragment_list(fragments: list[tuple[str, str]]) -> str:
    lines = []
    for i, (_item_id, ko) in enumerate(fragments, start=1):
        lines.append(f"{i}. {ko.strip()}")
    return "\n".join(lines) if lines else "(vacío)"


def format_history_block(history: list[dict]) -> str:
    if not history:
        return ""
    from hki import config

    n = config.FINAL_HISTORY_LINES
    lines = []
    for entry in history[-n:]:
        ko = (entry.get("ko") or "").strip()
        es = (entry.get("es") or "").strip()
        if ko and es:
            lines.append(f"KO: {ko}")
            lines.append(f"ES: {es}")
    if not lines:
        return ""
    return HISTORY_HEADER + "\n".join(lines) + "\n"


def build_sentence_user_message(
    fragments: list[tuple[str, str]],
    history: list[dict],
    *,
    force_release: bool = False,
) -> str:
    history_block = format_history_block(history)
    msg = SENTENCE_USER_HEADER.format(
        fragments=format_fragment_list(fragments),
        history_block=history_block,
    )
    if force_release:
        msg += (
            "\n\nTimeout de respaldo: DEBES hacer release ahora con through_index=N "
            "y es=una línea de lo que hay en los fragmentos. No uses hold. No inventes."
        )
    return msg


def _prompt_info(
    sermon_mode: bool,
    context: dict | None,
    prompt: str,
    mode: str,
) -> dict:
    return {
        "translation_prompt_mode": mode,
        "translation_prompt_label": PROMPT_MODE_LABELS[mode],
        "translation_prompt_preview": _prompt_preview(prompt),
        "translation_prompt_len": len(prompt),
        "translation_prompt_includes_context_summary": bool(
            sermon_mode and context and context.get("sermon_summary")
        ),
        "translation_prompt_includes_nvi": bool(
            sermon_mode and context and context.get("bible_es_nvi")
        ),
    }


def describe_sentence_prompt(sermon_mode: bool, context: dict | None) -> dict:
    """Prompt metadata for API status (same fields as legacy describe_translation_prompt)."""
    mode = _prompt_mode_for(sermon_mode, context)
    ctx = context if sermon_mode else None
    prompt = build_sentence_system_prompt(sermon_mode, ctx)
    info = _prompt_info(sermon_mode, ctx, prompt, mode)
    info["translator_live"] = True
    return info
