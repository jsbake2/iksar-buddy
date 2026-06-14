"""Forge dashboard: FastAPI app serving the two-bot control panel, streaming
telemetry over websocket, exposing per-bot craft controls. Backend is mocked
(forge/sim.py) for now; the HTTP/ws contract is what the real workers will fill."""
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import yaml
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..sim import ForgeSim
from ..telemetry import ForgeTelemetry

STATIC = Path(__file__).resolve().parent / "static"
# Owner config dir (outside the deploy path so saves survive deploys) — matches
# forge/__main__.FORGE_CFG.
_CFG = Path(os.environ.get("IB_FORGE_DIR",
            Path(__file__).resolve().parent.parent.parent / "config" / "forge"))
CRAFTERS_PATH = _CFG / "crafters.yaml"
KEYMAP_PATH = _CFG / "keymap.yaml"
LISTS_PATH = _CFG / "lists.yaml"


def create_app(tele: ForgeTelemetry, sim: ForgeSim) -> FastAPI:
    app = FastAPI(title="ib-forge", docs_url=None, redoc_url=None)

    def _bot_ok(bot_id: str) -> bool:
        return tele.bot(bot_id) is not None

    @app.get("/api/snapshot")
    async def snapshot():
        return tele.snapshot

    async def _exec(fn, *a):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

    @app.get("/api/bot/{bot_id}/frame.jpg")
    async def frame(bot_id: str):
        """Live VM screen for a bot's panel (live backend only). 503 until grabbable."""
        if not _bot_ok(bot_id) or not hasattr(sim, "frame_jpeg"):
            return Response(status_code=503)
        data = await _exec(sim.frame_jpeg, bot_id)
        if not data:
            # 409 = VM confirmed powered off (show 'powered off', not a stale
            # frame); 503 = transient/not-grabbable-yet (keep last).
            off = hasattr(sim, "vm_off") and await _exec(sim.vm_off, bot_id)
            return Response(status_code=409 if off else 503)
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/bot/{bot_id}/calibframe.jpg")
    async def calibframe(bot_id: str):
        """Full-res (1920) frame for the calibration picker — exact coords."""
        if not _bot_ok(bot_id) or not hasattr(sim, "frame_jpeg"):
            return Response(status_code=503)
        data = await _exec(sim.frame_jpeg, bot_id, True)
        if not data:
            return Response(status_code=503)
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/calib/enabled")
    async def calib_enabled():
        return {"enabled": hasattr(sim, "pixel")}     # live backend only

    @app.get("/api/bot/{bot_id}/pixel")
    async def pixel(bot_id: str, x: int, y: int):
        if not _bot_ok(bot_id) or not hasattr(sim, "pixel"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        return {"rgb": await _exec(sim.pixel, bot_id, x, y)}

    @app.get("/api/calib")
    async def get_calib():
        try:
            return yaml.safe_load((_CFG / "craft.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}

    @app.post("/api/calib")
    async def post_calib(payload: dict = Body(...)):
        if not hasattr(sim, "save_calib"):
            return JSONResponse({"error": "live backend only"}, status_code=400)
        ok = await _exec(sim.save_calib, payload.get("updates", {}))
        return {"ok": ok}

    @app.get("/api/crafters")
    async def get_crafters():
        return {"crafters": tele.snapshot.get("crafters", []),
                "trade_classes": tele.snapshot.get("trade_classes", []),
                "vms": [{"vm": b.get("vm"), "label": b.get("label"), "dom": b.get("dom")}
                        for b in tele.snapshot.get("bots", {}).values() if b.get("vm")]}

    @app.post("/api/crafters")
    async def post_crafters(payload: dict = Body(...)):
        rows = payload.get("crafters")
        if not isinstance(rows, list):
            return JSONResponse({"error": "missing crafters list"}, status_code=400)
        clean = []
        for r in rows:
            ch = str(r.get("character", "")).strip()
            if not ch:
                continue
            clean.append({"character": ch, "class": str(r.get("class", "")).strip(),
                          "vm": str(r.get("vm", "")).strip()})
        try:
            CRAFTERS_PATH.write_text(
                "# Crafter roster: character + tradeskill class + VM (edited from dashboard).\n"
                + yaml.safe_dump({"crafters": clean}, sort_keys=False, allow_unicode=True),
                encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        tele.set_crafters(clean)
        if hasattr(sim, "set_crafters"):
            sim.set_crafters(clean)
        return {"ok": True, "crafters": clean}

    @app.get("/api/forgelists")
    async def get_lists():
        try:
            return yaml.safe_load(LISTS_PATH.read_text(encoding="utf-8")) or {"lists": {}}
        except (OSError, yaml.YAMLError):
            return {"lists": {}}

    @app.post("/api/forgelists")
    async def post_lists(payload: dict = Body(...)):
        raw = payload.get("lists")
        if not isinstance(raw, dict):
            return JSONResponse({"error": "missing lists"}, status_code=400)
        clean = {}
        for name, rows in raw.items():
            nm = str(name).strip()
            if not nm or not isinstance(rows, list):
                continue
            items = []
            for r in rows:
                rn = str(r.get("name", "")).strip()
                if not rn:
                    continue
                try:
                    c = max(1, int(r.get("count", 1)))
                except (TypeError, ValueError):
                    c = 1
                items.append({"name": rn, "count": c})
            if items:
                clean[nm] = items
        try:
            LISTS_PATH.write_text(
                "# Named profit-craft lists (dashboard-edited).\n"
                + yaml.safe_dump({"lists": clean}, sort_keys=True, allow_unicode=True),
                encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"ok": True, "lists": clean}

    @app.get("/api/forgekeymap")
    async def get_keymap():
        try:
            return yaml.safe_load(KEYMAP_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}

    @app.post("/api/forgekeymap")
    async def post_keymap(payload: dict = Body(...)):
        km = {"camp": str(payload.get("camp", "/camp")).strip() or "/camp",
              "mana_recover": str(payload.get("mana_recover", "")).strip(),
              "arts": {
                  "durability": [str(k) for k in (payload.get("arts", {}).get("durability") or [])][:3],
                  "progress": [str(k) for k in (payload.get("arts", {}).get("progress") or [])][:3]}}
        try:
            KEYMAP_PATH.write_text(
                "# Forge keymap — camp command + counter#x mode art keys (dashboard-edited).\n"
                + yaml.safe_dump(km, sort_keys=False, allow_unicode=True), encoding="utf-8")
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        if hasattr(sim, "set_keymap"):
            sim.set_keymap(km)
        return {"ok": True, **km}

    @app.post("/api/bot/{bot_id}/camp")
    async def camp(bot_id: str):
        if not _bot_ok(bot_id) or not hasattr(sim, "camp"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        sim.camp(bot_id)
        return {"ok": True}

    @app.post("/api/campall")
    async def campall():
        if hasattr(sim, "camp_all"):
            sim.camp_all()
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/shutdown")
    async def shutdown(bot_id: str):
        if not _bot_ok(bot_id) or not hasattr(sim, "shutdown"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        sim.shutdown(bot_id)
        return {"ok": True}

    @app.post("/api/shutdownall")
    async def shutdownall():
        if hasattr(sim, "shutdown_all"):
            sim.shutdown_all()
        return {"ok": True}

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

    # SPICE web console proxy (same-origin) — bridge a browser WebSocket to a crafter
    # VM's local SPICE, so spice-html5 works on the LAN AND remotely through Cloudflare
    # (wss://forge.jsb-emr.us/spice/ws/5910), inheriting Access. Allowlist the bot ports.
    SPICE_PORTS = {int(b.get("spice_port")) for b in tele.snapshot.get("bots", {}).values()
                   if b.get("spice_port")} or {5910, 5920}

    @app.websocket("/spice/ws/{port}")
    async def spice_ws(websocket: WebSocket, port: int):
        if port not in SPICE_PORTS:
            await websocket.close(code=1008); return
        await websocket.accept(subprotocol="binary")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await websocket.close(code=1011); return

        async def ws_to_tcp():
            try:
                while True:
                    writer.write(await websocket.receive_bytes())
                    await writer.drain()
            except Exception:
                pass

        async def tcp_to_ws():
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:
                pass

        t1 = asyncio.create_task(ws_to_tcp())
        t2 = asyncio.create_task(tcp_to_ws())
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (t1, t2):
                t.cancel()
            writer.close()
            with contextlib.suppress(Exception):
                await websocket.close()

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")

    return app
