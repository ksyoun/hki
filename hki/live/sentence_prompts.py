"""Understand (KO hold/STT repair) and Translate (KO→ES + NVI) prompts."""

from __future__ import annotations

from hki.live.context import (
    format_context_for_translate,
    format_context_for_understand,
    has_sermon_summary,
)
from hki.live.translate import (
    GENERAL_SERVICE_RULES,
    PROMPT_MODE_LABELS,
    _prompt_mode_for,
    _prompt_preview,
)

UNDERSTAND_TASK_HEADER = (
    "너는 실시간 설교 STT 조각을 보고, 한국어 문장이 닫혔는지 판단하는 시스템이다. "
    "번역하지 않는다. JSON만 출력한다."
)

UNDERSTAND_REPAIR_RULES = (
    "ko_corrected 우선순위 (이 순서를 어기지 말 것):\n"
    "1. 원 STT가 기본값이다. 그대로 말이 되면 한 글자도 고치지 않는다.\n"
    "2. 명백한 STT 오인식만 고친다. 고유명사·동음이의(key_names/stt_variants), "
    "성경 책 이름, 숫자 표기(오장→5장)처럼 들은 말이 틀린 경우.\n"
    "3. 그 결과만 ko_corrected다. 원조각 1..k를 이어 붙인 것에 가깝다.\n"
    "금지:\n"
    "- 문맥상 이 말이었을 것이라며 절·서술어·예시를 추가\n"
    "- 원고·요약·핵심 문장으로 빈 문장을 완성하거나 그 구절을 그대로 복사\n"
    "- 어순 재작성, 문체 다듬기, 경어 통일, 새 문장 생성\n"
    "- hold인데 ko_corrected를 채워 다음 단계로 넘기기\n"
    "수정이 없으면 ko_corrected는 원 STT 1..k를 공백으로 이은 것과 같고 "
    "flags.stt_repair는 false다."
)

UNDERSTAND_COMPLETENESS_RULES = (
    "완결성 (action은 hold 또는 release만):\n"
    "RELEASE — 서술어와 문장 종결, 또는 짧은 완결 단위:\n"
    "- 종결: 습니다/ㅂ니다, 요, 다, 라, 죠, 세요\n"
    "- 짧은 단위: 아멘, 할렐루야, 안녕하세요, 감사합니다, 기도하겠습니다, "
    "읽어 드리겠습니다 / 읽겠습니다\n"
    "HOLD — 마지막 조각이 닫히지 않음:\n"
    "- 연결: 고, 서, 며, 면서, 는데, 니까, 도록, 려고, 러 (뒤에 종결 없음)\n"
    "- 주어/목적어+조사만 (은/는/이/가/을/를/의/에) 서술어 없음\n"
    "- 인용 도중 (…라고 / …하고 말씀, 인용 내용이나 동사 없음)\n"
    "- 부사·접속만 (정말, 그러나, 그때, 그러므로)\n"
    "through_index는 지금 목록의 상대 번호(1부터 N). 아이템 id가 아니다.\n"
    "- 1..k가 닫히고 k+1..N이 미완: action=release, through_index=k, "
    "ko_corrected는 1..k만.\n"
    "- 아무것도 안 닫힘: action=hold, through_index=0, ko_corrected 빈 문자열.\n"
    "- 백엔드가 timeout을 알리면 그때만 있는 것을 release (through_index=N). "
    "생각을 완성하지 말 것."
)

UNDERSTAND_OUTPUT_SCHEMA = (
    "유효한 JSON만:\n"
    '{"action":"hold"|"release","through_index":0,"ko_corrected":"","flags":{"stt_repair":false}}\n'
    "- hold: through_index=0, ko_corrected 빈 문자열\n"
    "- release: through_index=k (1..N, N보다 작을 수 있음), "
    "ko_corrected=조각 1..k의 복구 한 줄\n"
    "- flags.stt_repair: 원 STT와 실제로 다를 때만 true\n"
    "- 다른 필드 금지. through_index가 N보다 크면 안 된다 (백엔드가 hold 처리).\n"
)

UNDERSTAND_USER_HEADER = (
    "STT 조각 (한국어, 현재 pending 상대 번호 1..N):\n"
    "{fragments}\n\n"
    "{history_block}\n"
    "한국어 완결성으로 hold 또는 release. "
    "1..k가 닫히고 나머지가 아니면 through_index=k로 release. "
    "ko_corrected는 원 STT의 명백한 오인식만 수정. 문장을 만들지 말 것."
)

UNDERSTAND_HISTORY_HEADER = (
    "[최근 확정된 한국어 — 용어 연속용. 없는 내용을 가져오지 말 것]\n"
)

SENTENCE_TRANSLATE_TASK_HEADER = (
    "Eres la etapa final de traducción al español de un pipeline de subtítulos "
    "en vivo por oración (coreano → español argentino).\n"
    "El KO ya fue evaluado (hold/release). ko_corrected es el resultado permitido "
    "de la corrección STT en Understand (o el STT original si el guard rechazó "
    "la corrección): es tu única fuente de contenido.\n"
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
    "Traduce a UNA línea ES. No uses hold. No inventes.\n\n"
    "Fuente KO (delimitada):\n{ko}\n\n"
    "STT original (referencia; la fuente manda):\n{original_stt}\n\n"
    "{history_block}"
)

TRANSLATE_HISTORY_HEADER = (
    "[Historial reciente — continuidad de términos; no copies contenido que no "
    "esté en la fuente actual]\n"
)

# Kept for tests that still name completeness/faithfulness of the split pipeline.
SENTENCE_COMPLETENESS_RULES = UNDERSTAND_COMPLETENESS_RULES
SENTENCE_FAITHFULNESS_RULES = TRANSLATE_FAITHFULNESS_RULES


def format_fragment_list(fragments: list[tuple[str, str]]) -> str:
    lines = []
    for i, (_item_id, ko) in enumerate(fragments, start=1):
        lines.append(f"{i}. {ko.strip()}")
    return "\n".join(lines) if lines else "(vacío)"


def format_understand_history(history: list[dict]) -> str:
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
    return UNDERSTAND_HISTORY_HEADER + "\n".join(lines) + "\n"


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


def _understand_general() -> str:
    return (
        f"{UNDERSTAND_TASK_HEADER}\n\n{UNDERSTAND_REPAIR_RULES}\n\n"
        f"{UNDERSTAND_COMPLETENESS_RULES}\n\n"
        "모드: 일반 예배(인사·기도·안내). 설교 원고로 문장을 채우지 말 것.\n\n"
        f"{UNDERSTAND_OUTPUT_SCHEMA}"
    )


def _understand_sermon(context: dict) -> str:
    ctx_block = format_context_for_understand(context)
    return (
        f"{UNDERSTAND_TASK_HEADER}\n\n{UNDERSTAND_REPAIR_RULES}\n\n"
        f"{ctx_block}\n\n{UNDERSTAND_COMPLETENESS_RULES}\n\n"
        "모드: 설교. 원고는 STT 대조용일 뿐, 문장 생성용이 아니다.\n\n"
        f"{UNDERSTAND_OUTPUT_SCHEMA}"
    )


def _understand_fallback() -> str:
    return (
        f"{UNDERSTAND_TASK_HEADER}\n\n{UNDERSTAND_REPAIR_RULES}\n\n"
        f"{UNDERSTAND_COMPLETENESS_RULES}\n\n"
        "모드: 설교, Contextualizar 없음. 없는 내용을 추측하지 말 것.\n\n"
        f"{UNDERSTAND_OUTPUT_SCHEMA}"
    )


def build_understand_system_prompt(sermon_mode: bool, context: dict | None) -> str:
    if not sermon_mode:
        return _understand_general()
    if not context:
        return _understand_fallback()
    return _understand_sermon(context)


def build_understand_user_message(
    fragments: list[tuple[str, str]],
    history: list[dict],
    *,
    force_release: bool = False,
) -> str:
    msg = UNDERSTAND_USER_HEADER.format(
        fragments=format_fragment_list(fragments),
        history_block=format_understand_history(history),
    )
    if force_release:
        n = len(fragments)
        msg += (
            f"\n\n백엔드 강제 방출: 지금 release하고 through_index={n}으로. "
            "hold 금지. 문장을 지어내지 말 것. ko_corrected는 원 STT 복구만."
        )
    return msg


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
    """Operator preview: translate prompt (NVI lives here, not in understand)."""
    return build_translate_system_prompt(sermon_mode, context)


def build_sentence_user_message(
    fragments: list[tuple[str, str]],
    history: list[dict],
    *,
    force_release: bool = False,
) -> str:
    """Back-compat alias for understand user message."""
    return build_understand_user_message(
        fragments, history, force_release=force_release
    )


def _prompt_info(
    sermon_mode: bool,
    context: dict | None,
    prompt: str,
    mode: str,
) -> dict:
    understand = build_understand_system_prompt(sermon_mode, context)
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
        "understand_prompt_len": len(understand),
        "translator_live": True,
    }


def describe_sentence_prompt(sermon_mode: bool, context: dict | None) -> dict:
    mode = _prompt_mode_for(sermon_mode, context)
    ctx = context if sermon_mode else None
    prompt = build_translate_system_prompt(sermon_mode, ctx)
    info = _prompt_info(sermon_mode, ctx, prompt, mode)
    info["translator_live"] = True
    return info
