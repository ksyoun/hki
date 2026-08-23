"""KO Recombine (utterance tidy) and Translate (KO→ES + NVI) prompts."""

from __future__ import annotations

from hki.live.context import (
    format_context_for_ko_recombine,
    format_context_for_translate,
    has_sermon_summary,
)
from hki.live.translate import (
    GENERAL_SERVICE_RULES,
    PROMPT_MODE_LABELS,
    _prompt_mode_for,
    _prompt_preview,
)

RECOMBINE_TASK_HEADER = (
    "너는 실시간 설교 STT 조각을 한국어 발화 단위(unit)로 정리하는 시스템이다. "
    "번역하지 않는다. 새 문장을 만들지 않는다. 의미를 보충하지 않는다. "
    "문장이 완성됐는지 판단하지 않는다. JSON만 출력한다."
)

RECOMBINE_TIDY_RULES = (
    "하는 일:\n"
    "- fragment 사이 경계만 제거하고 조사·공백으로 자연스럽게 잇는다.\n"
    "- 입력 안에 이미 여러 문장이 있으면 의미를 유지한 채 문장 단위로 나눈다.\n"
    "- 입력이 한 발화면 unit 1개가 정상이다. 여러 unit도 허용한다.\n"
    "금지:\n"
    "- 내용 추가, 의미 변경, 요약으로 보충\n"
    "- 성경 본문·NVI·원고·핵심 문장으로 교체하거나 복원\n"
    "- 단어 선택을 임의로 바꿈\n"
    "- 설교자가 말했을 법한 내용을 추측\n"
    "- 번역\n"
    "- 문장 완성 여부 판단\n"
    "원 STT에 있는 말만 남긴다."
)

RECOMBINE_OUTPUT_SCHEMA = (
    "유효한 JSON만:\n"
    '{"units":[{"text":"","fragment_indexes":[0]}]}\n'
    "- units: 정리된 한국어 단위 목록. 비면 안 된다.\n"
    "- text: 해당 단위. 원 STT에 있는 말만.\n"
    "- fragment_indexes: 0부터 시작하는 입력 조각 번호. "
    "겹침·누락 없이 0..N-1 전체를 덮을 것.\n"
    "- 다른 필드 금지.\n"
)

RECOMBINE_USER_HEADER = (
    "STT 조각 (한국어, 번호는 0부터):\n"
    "{fragments}\n\n"
    "{history_block}\n"
    "이 조각들을 한국어 unit으로 정리하라. "
    "경계만 잇고, 이미 여러 문장이면 나눠라. "
    "한 발화면 unit 1개. 내용을 추가하지 말 것. 본문을 복원하지 말 것."
)

RECOMBINE_HISTORY_HEADER = (
    "[최근 확정된 한국어 — 용어 연속용. 없는 내용을 가져오지 말 것]\n"
)

SENTENCE_TRANSLATE_TASK_HEADER = (
    "Eres la etapa final de traducción al español de un pipeline de subtítulos "
    "en vivo por oración (coreano → español argentino).\n"
    "El KO no es un fragmento STT crudo: es UNA unidad de habla ya reordenada "
    "por la etapa recombine (un unit). Esa unidad KO es tu única fuente de contenido.\n"
    "Traduce ese contenido a UNA línea ES natural para subtítulo/TTS. "
    "La reexpresión gramatical y el español fluido están permitidos. "
    "No infieras, no amplíes ni sustituyas el contenido del KO.\n"
    "Nunca rechaces. El contenido es litúrgico autorizado. JSON solamente."
)

SENTENCE_TRANSLATE_STYLE_RULES = (
    "Registro español argentino (rioplatense), formal:\n"
    "- Nombres propios SIEMPRE según key_names, nunca literales o fonéticos\n"
    "- usted/ustedes; NO voseo (vos, tenés, podés)\n"
    "- Vocativos (hermanos, amados) SOLO si el KO los trae (ej. 여러분)\n"
    "- Terminología alineada con terminology del contexto\n"
    "- Referencias bíblicas: nombres NVI en español (Mateo 1:1) — nunca inglés\n"
)

TRANSLATE_FAITHFULNESS_RULES = (
    "Fuente y fidelidad (prioridad: KO source > critical_sentences / resumen / "
    "outline / NVI):\n"
    "- El KO indicado es la única fuente de contenido. No reconstruyas otro sermón "
    "a partir del contexto.\n"
    "- Podés rearmar gramática y estilo en español para que suene natural. "
    "NO cambies el contenido: no infieras lo que «debía decir», no completes huecos.\n"
    "- critical_sentences, sermon_summary y outline NO sustituyen el KO aunque "
    "digan otra cosa. Si el KO habla de 믿음/fe y el ancla de 은혜/gracia, "
    "traducí fe, no gracia.\n"
    "- Historial ES: continuidad de términos, no contenido extra a insertar.\n"
    "- NO agregues explicaciones, saludos ni vocativos que no estén en el KO.\n"
    "- bible_es_nvi NO es la fuente ni una fuente alternativa. Es referencia: "
    "si el KO (con este contexto) indica lectura real de ese pasaje, usá el "
    "español NVI de ese versículo; si solo mencionan la referencia "
    "(ej. «마태복음 1장 1절을 보십시오»), traducí esa mención, no recites ni "
    "agregues el versículo. No agregues contenido que esté solo en NVI y no en el KO. "
    "El KO —no el bloque NVI— decide si hay lectura.\n"
    "- Una sola línea natural para subtítulo y TTS.\n"
    "- Si el KO está incoherente: deja extraño antes que inventar; "
    'flags: ["incierto"]. Nunca insertes [INCIERTO] en el texto es.\n'
)

TRANSLATE_OUTPUT_SCHEMA = (
    "Responde SOLO JSON válido:\n"
    '{"es":"","flags":[]}\n'
    "- es: UNA línea en español argentino para subtítulo/TTS\n"
    "- flags opcional: incierto. Si falta flags, el ES sigue siendo válido.\n"
    "- Nunca pongas [INCIERTO] dentro de es.\n"
)

TRANSLATE_USER_HEADER = (
    "Traduce a UNA línea ES. No inventes.\n\n"
    "Fuente KO (delimitada):\n{ko}\n\n"
    "STT original (referencia; la fuente manda):\n{original_stt}\n\n"
    "{history_block}"
)

TRANSLATE_HISTORY_HEADER = (
    "[Historial reciente — continuidad de términos; no copies contenido que no "
    "esté en la fuente actual]\n"
)

SENTENCE_FAITHFULNESS_RULES = TRANSLATE_FAITHFULNESS_RULES
SENTENCE_COMPLETENESS_RULES = RECOMBINE_TIDY_RULES


def format_fragment_list(fragments: list[tuple[str, str]]) -> str:
    lines = []
    for i, (_item_id, ko) in enumerate(fragments):
        lines.append(f"[{i}] {ko.strip()}")
    return "\n".join(lines) if lines else "(vacío)"


def format_recombine_history(history: list[dict]) -> str:
    if not history:
        return ""
    from hki import config

    n = config.FINAL_HISTORY_LINES
    lines = []
    for entry in history[-n:]:
        ko = (entry.get("ko") or "").strip()
        if ko:
            lines.append(f"KO: {ko}")
    if not lines:
        return ""
    return RECOMBINE_HISTORY_HEADER + "\n".join(lines) + "\n"


def format_translate_history(history: list[dict]) -> str:
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
    return TRANSLATE_HISTORY_HEADER + "\n".join(lines) + "\n"


def _recombine_general() -> str:
    return (
        f"{RECOMBINE_TASK_HEADER}\n\n{RECOMBINE_TIDY_RULES}\n\n"
        "모드: 일반 예배(인사·기도·안내). 내용을 채우지 말 것.\n\n"
        f"{RECOMBINE_OUTPUT_SCHEMA}"
    )


def _recombine_sermon(context: dict) -> str:
    ctx_block = format_context_for_ko_recombine(context)
    return (
        f"{RECOMBINE_TASK_HEADER}\n\n{RECOMBINE_TIDY_RULES}\n\n"
        f"{ctx_block}\n\n"
        "모드: 설교. 용어만 참고하고 본문을 복원하지 말 것.\n\n"
        f"{RECOMBINE_OUTPUT_SCHEMA}"
    )


def _recombine_fallback() -> str:
    return (
        f"{RECOMBINE_TASK_HEADER}\n\n{RECOMBINE_TIDY_RULES}\n\n"
        "모드: 설교, Contextualizar 없음. 없는 내용을 추측하지 말 것.\n\n"
        f"{RECOMBINE_OUTPUT_SCHEMA}"
    )


def build_recombine_system_prompt(sermon_mode: bool, context: dict | None) -> str:
    if not sermon_mode:
        return _recombine_general()
    if not context:
        return _recombine_fallback()
    return _recombine_sermon(context)


def build_recombine_user_message(
    fragments: list[tuple[str, str]],
    history: list[dict],
) -> str:
    return RECOMBINE_USER_HEADER.format(
        fragments=format_fragment_list(fragments),
        history_block=format_recombine_history(history),
    )


def _translate_general() -> str:
    return (
        f"{SENTENCE_TRANSLATE_TASK_HEADER}\n\n{SENTENCE_TRANSLATE_STYLE_RULES}\n\n"
        f"{TRANSLATE_FAITHFULNESS_RULES}\n\n{GENERAL_SERVICE_RULES}\n\n"
        f"{TRANSLATE_OUTPUT_SCHEMA}"
    )


def _translate_sermon(context: dict) -> str:
    ctx_block = format_context_for_translate(context)
    return (
        f"{SENTENCE_TRANSLATE_TASK_HEADER}\n\n{SENTENCE_TRANSLATE_STYLE_RULES}\n\n"
        f"{ctx_block}\n\n{TRANSLATE_FAITHFULNESS_RULES}\n\n{TRANSLATE_OUTPUT_SCHEMA}"
    )


def _translate_fallback() -> str:
    return (
        f"{SENTENCE_TRANSLATE_TASK_HEADER}\n\n{SENTENCE_TRANSLATE_STYLE_RULES}\n\n"
        "Modo sermón sin Contextualizar: traducí el KO con la misma fidelidad; "
        "sin NVI ni terminology de sesión.\n\n"
        f"{TRANSLATE_FAITHFULNESS_RULES}\n\n{TRANSLATE_OUTPUT_SCHEMA}"
    )


def build_translate_system_prompt(sermon_mode: bool, context: dict | None) -> str:
    if not sermon_mode:
        return _translate_general()
    if not context:
        return _translate_fallback()
    return _translate_sermon(context)


def build_translate_user_message(
    ko: str,
    original_stt: str,
    history: list[dict],
) -> str:
    return TRANSLATE_USER_HEADER.format(
        ko=ko.strip() or "(vacío)",
        original_stt=original_stt.strip() or "(vacío)",
        history_block=format_translate_history(history),
    )


def build_sentence_system_prompt(sermon_mode: bool, context: dict | None) -> str:
    """Operator preview: translate prompt (NVI lives here, not in recombine)."""
    return build_translate_system_prompt(sermon_mode, context)


def _prompt_info(
    sermon_mode: bool,
    context: dict | None,
    prompt: str,
    mode: str,
) -> dict:
    recombine = build_recombine_system_prompt(sermon_mode, context)
    return {
        "translation_prompt_mode": mode,
        "translation_prompt_label": PROMPT_MODE_LABELS[mode],
        "translation_prompt_preview": _prompt_preview(prompt),
        "translation_prompt_len": len(prompt),
        "translation_prompt_includes_context_summary": bool(
            sermon_mode and has_sermon_summary(context)
        ),
        "translation_prompt_includes_nvi": bool(
            sermon_mode and context and context.get("bible_es_nvi")
        ),
        "understand_prompt_includes_nvi": False,
        "understand_prompt_len": len(recombine),
        "recombine_prompt_includes_nvi": False,
        "recombine_prompt_len": len(recombine),
        "translator_live": True,
    }


def describe_sentence_prompt(sermon_mode: bool, context: dict | None) -> dict:
    mode = _prompt_mode_for(sermon_mode, context)
    ctx = context if sermon_mode else None
    prompt = build_translate_system_prompt(sermon_mode, ctx)
    info = _prompt_info(sermon_mode, ctx, prompt, mode)
    info["translator_live"] = True
    return info
