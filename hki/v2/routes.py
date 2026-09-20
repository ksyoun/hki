"""v2 HTTP routes. Wired from app.py via register_v2 (no circular import)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hki.v2.engine import goto_slide
from hki.v2.prepare import convertir_captions, fill_nvi_indices, translate_indices
from hki.v2.rundown import apply_default_slides, get_store, normalize_slides

V2_STATIC = Path(__file__).parent / "static"


class RundownSaveBody(BaseModel):
    slides: list[dict] = []
    cursor: int | None = None


class RundownTranslateBody(BaseModel):
    index: int | None = None
    indices: list[int] | None = None
    mode: str = "es"


class RundownGotoBody(BaseModel):
    action: str | None = None
    index: int | None = None


def register_v2(app: FastAPI, pipeline, session, broadcaster) -> None:
    @app.get("/v2")
    async def v2_control_page():
        return FileResponse(V2_STATIC / "control.html")

    @app.get("/api/v2/rundown")
    async def rundown_get():
        store = get_store()
        if not store.slides:
            apply_default_slides(store)
        return {"ok": True, **store.status()}

    @app.post("/api/v2/rundown")
    async def rundown_save(body: RundownSaveBody):
        store = get_store()
        store.set_slides(normalize_slides(body.slides), cursor=body.cursor)
        return {"ok": True, **store.status()}

    @app.post("/api/v2/rundown/modelo")
    async def rundown_modelo():
        store = get_store()
        apply_default_slides(store)
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
            apply_default_slides(store)
        mode = (body.mode or "es").strip().lower()
        if mode == "nvi":
            updated, warnings = await fill_nvi_indices(store.slides, indices)
        elif mode == "es":
            updated, warnings = await translate_indices(
                store.slides,
                indices,
                context=session.translation_context,
                passage_display=session.passage_display,
            )
        else:
            return {"ok": False, "error": "Modo no válido"}
        result = {"ok": True, "updated": updated, "warnings": warnings, **store.status()}
        if warnings and not updated:
            result["ok"] = False
            result["error"] = "; ".join(warnings)
        elif warnings:
            result["warning"] = "; ".join(warnings)
        return result

    @app.post("/api/v2/rundown/convertir")
    async def rundown_convertir():
        store = get_store()
        if not store.slides:
            apply_default_slides(store)
        updated, warnings = await convertir_captions(
            store.slides,
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
