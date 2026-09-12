"""Apply rundown cues by calling v1 pipeline pause/resume/sermon + lyrics WS."""

from __future__ import annotations

from hki.live.session import SessionState
from hki.v2.rundown import CAPTION_MODES, RundownStore

CUE_ICON = {
    "speak": "🎤",
    "caption": "♪",
    "bible": "📖",
    "sermon": "✝",
}
CUE_TITLE = {
    "speak": "Servicio",
    "bible": "Lectura Bíblica",
    "sermon": "Sermón",
}


def format_cue_caption(cue: str, text: str = "") -> str:
    """Audience line: icon wrap so a cue change is visible on phones."""
    cue = (cue or "").strip().lower()
    icon = CUE_ICON.get(cue, "♪")
    inner = (text or "").strip()
    if not inner:
        inner = CUE_TITLE.get(cue, "")
    if not inner:
        return icon
    return f"{icon} {inner} {icon}"


def _prev_cue(store: RundownStore, session) -> str | None:
    current = store.current()
    if current:
        return str(current["cue"])
    if session.state == SessionState.STREAMING:
        return "speak"
    if session.state == SessionState.PAUSED:
        return "caption"
    return None


def nvi_caption_chunks(session) -> list[str]:
    """Spanish NVI lines from Contextualizar; empty if none yet."""
    ctx = session.translation_context or {}
    verses = ctx.get("bible_es_nvi") or []
    chunks: list[str] = []
    if isinstance(verses, list):
        for v in verses:
            if isinstance(v, dict):
                line = f"{v.get('ref', '')} {v.get('text', '')}".strip()
            else:
                line = str(v or "").strip()
            if line:
                chunks.append(line)
    if chunks:
        return chunks
    display = session.passage_display or {}
    nvi = str(display.get("nvi") or "").strip()
    if not nvi:
        return []
    parts = [p.strip() for p in nvi.replace("\r\n", "\n").split("\n") if p.strip()]
    return parts or [nvi]


async def _broadcast_lyrics(
    session, broadcaster, kind: str, text: str, store: RundownStore
) -> dict:
    caption = (text or "").strip()
    if not caption:
        return {}
    item_id = session.next_lyrics_item_id(kind)
    session.add_final_translation(caption)
    payload = {
        "type": "lyrics",
        "kind": kind,
        "text": caption,
        "item_id": item_id,
        "item_ids": [item_id],
        "song_index": 0,
        "slide_index": store.cursor,
        "slide_count": len(store.slides),
    }
    await broadcaster.broadcast(payload)
    return payload


async def _pause_stt(pipeline, session) -> None:
    if session.state == SessionState.STREAMING:
        await pipeline.pause()


async def _enter_caption(
    pipeline, session, broadcaster, store: RundownStore, slide: dict
) -> None:
    await _pause_stt(pipeline, session)
    await _broadcast_lyrics(
        session,
        broadcaster,
        "caption",
        format_cue_caption("caption", slide.get("es") or ""),
        store,
    )


async def _enter_bible(pipeline, session, broadcaster, store: RundownStore) -> None:
    await _pause_stt(pipeline, session)
    chunks = nvi_caption_chunks(session) or [CUE_TITLE["bible"]]
    for chunk in chunks:
        await _broadcast_lyrics(
            session, broadcaster, "bible", format_cue_caption("bible", chunk), store
        )


async def _announce_speech(
    session, broadcaster, store: RundownStore, slide: dict, *, sermon: bool
) -> None:
    cue = "sermon" if sermon else "speak"
    title = CUE_TITLE[cue]
    if not sermon:
        title = (slide.get("label") or slide.get("ko") or "").strip() or title
    await _broadcast_lyrics(
        session, broadcaster, cue, format_cue_caption(cue, title), store
    )


async def _leave_caption_to_speech(
    pipeline, session, broadcaster, store: RundownStore, slide: dict, *, sermon: bool
) -> None:
    await _announce_speech(session, broadcaster, store, slide, sermon=sermon)
    if session.state == SessionState.PAUSED:
        await pipeline.resume()
    await pipeline.set_sermon_mode(sermon)


async def goto_slide(
    store: RundownStore,
    pipeline,
    session,
    broadcaster,
    *,
    action: str | None = None,
    index: int | None = None,
) -> dict:
    if session.state not in (SessionState.STREAMING, SessionState.PAUSED):
        return {"ok": False, "error": "No hay transmisión en curso"}
    slides = store.slides
    if not slides:
        return {"ok": False, "error": "No hay hojas en el rundown"}

    action = (action or "").strip().lower()
    if action == "next":
        target = store.cursor + 1
        if target >= len(slides):
            return {"ok": True, "at_end": True, **store.status()}
    elif action == "prev":
        if store.cursor <= 0:
            return {"ok": True, "at_start": True, **store.status()}
        target = store.cursor - 1
    elif index is not None:
        target = int(index)
        if target < 0 or target >= len(slides):
            return {"ok": False, "error": "Índice no válido"}
    else:
        return {"ok": False, "error": "Acción no válida"}

    if target == store.cursor:
        return {"ok": True, **store.status()}

    prev = _prev_cue(store, session)
    slide = slides[target]
    new_cue = slide["cue"]
    leaving_hold = prev in CAPTION_MODES
    entering_hold = new_cue in CAPTION_MODES

    store.cursor = target

    if entering_hold:
        if new_cue == "bible":
            await _enter_bible(pipeline, session, broadcaster, store)
        else:
            await _enter_caption(pipeline, session, broadcaster, store, slide)
    elif leaving_hold:
        await _leave_caption_to_speech(
            pipeline,
            session,
            broadcaster,
            store,
            slide,
            sermon=new_cue == "sermon",
        )
    else:
        want_sermon = new_cue == "sermon"
        if session.state == SessionState.PAUSED:
            await pipeline.resume()
        await _announce_speech(session, broadcaster, store, slide, sermon=want_sermon)
        if bool(session.sermon_on) != want_sermon:
            await pipeline.set_sermon_mode(want_sermon)

    await pipeline.broadcast_status()
    await broadcaster.broadcast({"type": "v2_rundown", **store.status()})
    return {"ok": True, **store.status()}
