"""Shared Korean surface endings for classic timing, oración hold, and prompts.

One suffix list drives the regex and the prompt strings so they cannot drift.
"""

from __future__ import annotations

import re

# Classic live gate, plus 까 (questions: 있습니까 / 합니까).
KO_CLEAR_FINAL_SUFFIXES: tuple[str, ...] = (
    "다",
    "요",
    "니다",
    "습니다",
    "ㅂ니다",
    "십시오",
    "시오",
    "세요",
    "죠",
    "네요",
    "라",
    "까",
    "아멘",
)

KO_OPEN_END_SUFFIXES: tuple[str, ...] = (
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "에서",
    "에게",
    "께서",
    "와",
    "과",
    "로",
    "으로",
    "고",
    "며",
    "면",
    "니까",
    "지만",
    "는데",
    "그리고",
    "그런데",
    "그래서",
    "왜냐하면",
    "그러나",
    "하지만",
    "즉",
)

_KO_CLEAR_FINAL = re.compile(
    "(" + "|".join(re.escape(s) for s in KO_CLEAR_FINAL_SUFFIXES) + r")[.!?。]*$"
)
_KO_OPEN_END = re.compile(
    "(" + "|".join(re.escape(s) for s in KO_OPEN_END_SUFFIXES) + r")$"
)


def format_suffix_prompt(suffixes: tuple[str, ...]) -> str:
    return "/".join(f"-{s}" for s in suffixes)


def ko_tokens(ko: str) -> list[str]:
    return [t for t in re.split(r"\s+", (ko or "").strip()) if t]


def has_clear_final_ending(ko: str) -> bool:
    s = (ko or "").strip()
    if not s or s.endswith("...") or s.endswith("…"):
        return False
    if re.search(r"[.!?。]$", s):
        return True
    return bool(_KO_CLEAR_FINAL.search(s))


def fragment_looks_open_ko(ko: str) -> bool:
    """True if this KO fragment should wait. Not a full-sentence detector.

    Order matches the classic KO half: ellipsis → clear final (closed) →
    short token → open suffix → closed.
    """
    ko_s = (ko or "").strip()
    if ko_s.endswith("...") or ko_s.endswith("…"):
        return True
    if has_clear_final_ending(ko_s):
        return False
    if len(ko_tokens(ko_s)) < 5:
        return True
    if _KO_OPEN_END.search(ko_s):
        return True
    return False


def fragment_ending_rules_es() -> str:
    open_s = format_suffix_prompt(KO_OPEN_END_SUFFIXES)
    final_s = format_suffix_prompt(KO_CLEAR_FINAL_SUFFIXES)
    return (
        "Completitud del fragmento (prioridad: forma del KO original > frase "
        "española «natural»):\n"
        f"- Si el KO termina en forma no final ({open_s}, o puntos suspensivos), "
        "NO cierres el español con punto. Dejá la frase abierta: coma, conjunción "
        "(que, y, pero, porque) o puntos suspensivos (…).\n"
        f"- Solo cerrá con . ? ! si el KO termina en forma final ({final_s}) "
        "o ya trae punto.\n"
        "- Ejemplo: «야곱의 삶에는 … 갈망하는» → "
        "«…un profundo anhelo por la bendición de Dios,» (sin punto final).\n"
        "- Una terminación abierta no es [INCIERTO]: es fragmento incompleto, "
        "no duda de fidelidad.\n"
    )


def recombine_ending_rules_ko() -> str:
    open_s = format_suffix_prompt(KO_OPEN_END_SUFFIXES)
    final_s = format_suffix_prompt(KO_CLEAR_FINAL_SUFFIXES)
    return (
        "표면 어미 (해석하지 말 것. 목록만 본다):\n"
        f"- 비종결({open_s})로 끝나면 다음 조각과 같은 unit으로 잇는 것을 우선. "
        "그 경계에서 unit을 자르지 않는다.\n"
        f"- 종결({final_s})이나 문장부호(. ! ? 。)로 끝나면 그 자리에서 unit을 나눠도 된다.\n"
        "- 마지막 unit의 어미가 비종결이면 open: true, 아니면 false. 문맥을 추측하지 말 것.\n"
    )
