"""v2 HTTP routes. Wired from app.py via register_v2 (no circular import)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hki.v2.engine import goto_slide
from hki.v2.prepare import translate_indices
from hki.v2.rundown import get_store, normalize_slides

V2_STATIC = Path(__file__).parent / "static"


class RundownSaveBody(BaseModel):
    slides: list[dict] = []
    cursor: int | None = None


class RundownTranslateBody(BaseModel):
    index: int | None = None
    indices: list[int] | None = None


class RundownGotoBody(BaseModel):
    action: str | None = None
    index: int | None = None


def register_v2(app: FastAPI, pipeline, session, broadcaster) -> None:
    @app.get("/v2")
    async def v2_control_page():
        return FileResponse(V2_STATIC / "control.html")

    @app.get("/api/v2/rundown")
    async def rundown_get():
        return {"ok": True, **get_store().status()}

    @app.post("/api/v2/rundown")
    async def rundown_save(body: RundownSaveBody):
        store = get_store()
        store.set_slides(normalize_slides(body.slides), cursor=body.cursor)
        return {"ok": True, **store.status()}

    @app.post("/api/v2/rundown/translate")
    async def rundown_translate(body: RundownTranslateBody):
        store = get_store()
        if body.indices is not None:
            indices = list(body.indices)
        elif body.index is not None:
            indices = [body.index]
        else:
            return {"ok": False, "error": "Falta index"}
        if not store.slides:
            return {"ok": False, "error": "No hay hojas en el rundown"}
        updated, warnings = await translate_indices(
            store.slides,
            indices,
            context=session.translation_context,
            passage_display=session.passage_display,
        )
        result = {"ok": True, "updated": updated, "warnings": warnings, **store.status()}
        if warnings and not updated:
            result["ok"] = False
            result["error"] = "; ".join(warnings)
        elif warnings:
            result["warning"] = "; ".join(warnings)
        return result

    @app.post("/api/v2/rundown/goto")
    async def rundown_goto(body: RundownGotoBody):
        return await goto_slide(
            get_store(),
            pipeline,
            session,
            broadcaster,
            action=body.action,
            index=body.index,
        )

    app.mount("/v2-static", StaticFiles(directory=V2_STATIC), name="v2-static")
