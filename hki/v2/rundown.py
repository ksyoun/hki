"""v2 cue-sheet store. Not mixed into LiveSession."""

from __future__ import annotations

from datetime import datetime
from typing import Any

CUES = ("speak", "caption", "sermon", "bible")
CAPTION_MODES = ("caption", "bible")
MAX_SLIDES = 80
MAX_TEXT = 500
MAX_LABEL = 80

_store: "RundownStore | None" = None
_id_seq = 0


def _next_id() -> str:
    global _id_seq
    _id_seq += 1
    return f"s{_id_seq}"


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


CALL_APPEND_KO = "하나님은 영이시니 예배하는자가 영과 진리로 예배할지니라. 아멘"
CALL_APPEND_ES = (
    "Dios es espíritu, y quienes lo adoran deben hacerlo en espíritu y en verdad. Amén."
)
ABS_APPEND_KO = "그러므로 그리스도 안에 있는 자들에게는 결코 정죄함이 없느니라. 아멘"
ABS_APPEND_ES = (
    "Por lo tanto, ya no hay ninguna condenación para los que están unidos a Cristo Jesús. Amén."
)

WELCOME_ES = "¡Bienvenidos al culto!"
CLOSING_ES = "Ha concluido el culto, ¡Bendiciones!"


def culto_dominical_es(when: datetime | None = None) -> str:
    day = when or datetime.now()
    return f"Culto dominical {day.strftime('%d/%m/%y')}"


CREED_GUIDE_ES = "Credo Apostólico"
CREED_LINES_ES = (
    "Creo en Dios Padre Todopoderoso, Creador del cielo y de la tierra;",
    "y en Jesucristo, su único Hijo, Señor nuestro",
    "que fue concebido del Espíritu Santo, nació de la virgen María",
    "padeció bajo el poder de Poncio Pilato; fue crucificado, muerto y sepultado;",
    "descendió a los infiernos; al tercer día resucitó de entre los muertos",
    "subió al cielo, y está sentado a la diestra de Dios Padre Todopoderoso;",
    "y desde allí vendrá al fin del mundo a juzgar a los vivos y a los muertos.",
    "Creo en el Espíritu Santo, la Santa Iglesia Universal, la comunión de los santos,",
    "el perdón de los pecados, la resurrección de la carne y la vida perdurable. Amén.",
)


def empty_slide(cue: str = "speak") -> dict:
    cue = (cue or "speak").strip().lower()
    if cue not in CUES:
        cue = "speak"
    return {
        "id": _next_id(),
        "cue": cue,
        "ko": "",
        "es": "",
        "label": "",
    }


def _caption(
    ko: str = "",
    es: str = "",
    *,
    label: str = "",
    append_ko: str = "",
    append_es: str = "",
) -> dict:
    slide = empty_slide("caption")
    slide["ko"] = ko
    slide["es"] = es
    if label:
        slide["label"] = label
    if append_ko:
        slide["append_ko"] = append_ko
    if append_es:
        slide["append_es"] = append_es
    return slide


def _speak(label: str) -> dict:
    slide = empty_slide("speak")
    slide["label"] = label
    slide["ko"] = label
    return slide


def _bible(label: str) -> dict:
    slide = empty_slide("bible")
    slide["label"] = label
    slide["ko"] = label
    return slide


def _sermon(label: str = "") -> dict:
    slide = empty_slide("sermon")
    if label:
        slide["label"] = label
        slide["ko"] = label
    return slide


def default_sunday_slides() -> list[dict]:
    """Operator cue sheet for a typical Sunday service (one caption per PPT slide)."""
    creed = [_caption("사도신경", CREED_GUIDE_ES)]
    creed.extend(_caption("", line) for line in CREED_LINES_ES)
    raw = [
        _caption("주일예배안내", WELCOME_ES),
        _caption("인트로찬양"),
        _caption("주일예배안내", culto_dominical_es()),
        _caption(
            "",
            label="예배의 부름",
            append_ko=CALL_APPEND_KO,
            append_es=CALL_APPEND_ES,
        ),
        _caption("찬양1"),
        _speak("기도1"),
        _caption(
            "",
            label="사죄선언",
            append_ko=ABS_APPEND_KO,
            append_es=ABS_APPEND_ES,
        ),
        *creed,
        _caption("찬양2"),
        _caption("", "Oración:", label="대표기도"),
        _bible("성경봉독"),
        _caption("찬양3"),
        _sermon(""),
        _caption("찬양4"),
        _speak("Anuncios"),
        _caption("찬양5"),
        _speak("Oración final de bendición"),
        _caption("예배안내", CLOSING_ES),
    ]
    return normalize_slides(raw)


def apply_default_slides(store: "RundownStore") -> None:
    store.set_slides(default_sunday_slides(), cursor=-1)


def normalize_slide(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    cue = str(src.get("cue") or "speak").strip().lower()
    if cue not in CUES:
        cue = "speak"
    sid = str(src.get("id") or "").strip() or _next_id()
    ko = _clip(str(src.get("ko") or ""), MAX_TEXT)
    es = _clip(str(src.get("es") or ""), MAX_TEXT)
    label = _clip(str(src.get("label") or ""), MAX_LABEL)
    if cue == "bible":
        if not label:
            label = _clip(ko, MAX_LABEL) or "Lectura bíblica"
        es = ""
    elif cue == "speak":
        if not label:
            label = _clip(ko, MAX_LABEL)
        es = ""
    elif cue == "sermon":
        if not label:
            label = _clip(ko, MAX_LABEL)
    out = {"id": sid, "cue": cue, "ko": ko, "es": es, "label": label}
    append_ko = _clip(str(src.get("append_ko") or ""), MAX_TEXT)
    append_es = _clip(str(src.get("append_es") or ""), MAX_TEXT)
    if append_ko:
        out["append_ko"] = append_ko
    if append_es:
        out["append_es"] = append_es
    return out


def normalize_slides(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        out.append(normalize_slide(item))
        if len(out) >= MAX_SLIDES:
            break
    return out


def insert_slide(slides: list[dict], index: int, cue: str = "caption") -> list[dict]:
    out = list(slides)
    index = max(0, min(int(index), len(out)))
    out.insert(index, empty_slide(cue))
    return normalize_slides(out)


def move_slide(slides: list[dict], from_index: int, to_index: int) -> list[dict]:
    out = list(slides)
    n = len(out)
    if n == 0:
        return out
    from_index = int(from_index)
    to_index = int(to_index)
    if from_index < 0 or from_index >= n:
        raise ValueError("Índice de origen no válido")
    item = out.pop(from_index)
    if to_index > from_index:
        to_index -= 1
    to_index = max(0, min(to_index, len(out)))
    out.insert(to_index, item)
    return normalize_slides(out)


def page_of(cursor: int) -> int:
    return cursor + 1 if cursor >= 0 else 0


class RundownStore:
    def __init__(self) -> None:
        self.slides: list[dict] = []
        self.cursor: int = -1

    def reset_cursor(self) -> None:
        self.cursor = -1

    def clear(self) -> None:
        self.slides = []
        self.cursor = -1

    def current(self) -> dict | None:
        if 0 <= self.cursor < len(self.slides):
            return self.slides[self.cursor]
        return None

    def current_cue(self) -> str | None:
        slide = self.current()
        return str(slide["cue"]) if slide else None

    def set_slides(self, slides: list[dict], cursor: int | None = None) -> None:
        old_id = None
        cur = self.current()
        if cur:
            old_id = cur.get("id")
        self.slides = normalize_slides(slides)
        if not self.slides:
            self.cursor = -1
            return
        if cursor is not None:
            self.cursor = max(-1, min(int(cursor), len(self.slides) - 1))
            return
        if old_id:
            for i, slide in enumerate(self.slides):
                if slide.get("id") == old_id:
                    self.cursor = i
                    return
        if self.cursor >= len(self.slides):
            self.cursor = len(self.slides) - 1

    def status(self) -> dict:
        return {
            "slides": list(self.slides),
            "cursor": self.cursor,
            "page": page_of(self.cursor),
            "page_count": len(self.slides),
            "current_cue": self.current_cue(),
        }


def get_store() -> RundownStore:
    global _store
    if _store is None:
        _store = RundownStore()
    return _store


def reset_store() -> RundownStore:
    global _store, _id_seq
    _id_seq = 0
    _store = RundownStore()
    return _store
