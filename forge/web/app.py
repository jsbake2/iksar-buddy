"""Forge dashboard: FastAPI app serving the two-bot control panel, streaming
telemetry over websocket, exposing per-bot craft controls. Backend is mocked
(forge/sim.py) for now; the HTTP/ws contract is what the real workers will fill."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..sim import ForgeSim
from ..telemetry import ForgeTelemetry

STATIC = Path(__file__).resolve().parent / "static"


def create_app(tele: ForgeTelemetry, sim: ForgeSim) -> FastAPI:
    app = FastAPI(title="ib-forge", docs_url=None, redoc_url=None)

    def _bot_ok(bot_id: str) -> bool:
        return tele.bot(bot_id) is not None

    @app.get("/api/snapshot")
    async def snapshot():
        return tele.snapshot

    @app.post("/api/bot/{bot_id}/enable")
    async def enable(bot_id: str, payload: dict = Body(default={})):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.enable(bot_id, bool(payload.get("on", True)))
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/config")
    async def config(bot_id: str, payload: dict = Body(...)):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.configure(bot_id, **payload)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/start")
    async def start(bot_id: str, payload: dict = Body(...)):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.start(bot_id,
                  mode=payload.get("mode", "single"),
                  trade_class=payload.get("trade_class", ""),
                  recipe=payload.get("recipe", ""),
                  count=payload.get("count", 1))
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/stop")
    async def stop(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.stop(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/pause")
    async def pause(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.pause(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/launch")
    async def launch(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.launch(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/switch")
    async def switch(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.switch_char(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/ocr")
    async def ocr(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.ocr_journal(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/readlog")
    async def readlog(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.read_log(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/queue")
    async def queue(bot_id: str, payload: dict = Body(...)):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        sim.set_queue(bot_id, payload.get("queue", []))
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = tele.subscribe()
        try:
            await websocket.send_json(tele.snapshot)
            while True:
                snap = await q.get()
                await websocket.send_json(snap)
        except WebSocketDisconnect:
            pass
        finally:
            tele.unsubscribe(q)

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")

    return app
