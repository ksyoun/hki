"""v2 cue-sheet store. Not mixed into LiveSession."""

from __future__ import annotations

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


def normalize_slide(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    cue = str(src.get("cue") or "speak").strip().lower()
    if cue not in CUES:
        cue = "speak"
    sid = str(src.get("id") or "").strip() or _next_id()
    ko = _clip(str(src.get("ko") or ""), MAX_TEXT)
    es = _clip(str(src.get("es") or ""), MAX_TEXT)
    label = _clip(str(src.get("label") or ""), MAX_LABEL)
    if cue not in ("caption", "bible"):
        if not label:
            label = _clip(ko, MAX_LABEL)
        es = ""
    elif cue == "bible":
        if not label:
            label = _clip(ko, MAX_LABEL) or "Lectura bíblica"
        es = ""
    return {"id": sid, "cue": cue, "ko": ko, "es": es, "label": label}


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
