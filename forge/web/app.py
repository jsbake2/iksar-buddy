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

import web_common

from .. import debug
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

    @app.middleware("http")
    async def no_cache_assets(request, call_next):
        """Force the browser to REVALIDATE html/js/css every load. Without this a
        deploy can leave a stale app.js running against fresh index.html — e.g. the
        new Search box renders but the cached JS never sends its value (the
        'typed recipe name not search name' bug)."""
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    def _bot_ok(bot_id: str) -> bool:
        return tele.bot(bot_id) is not None

    @app.get("/api/snapshot")
    async def snapshot():
        return tele.snapshot

    @app.get("/api/push")
    async def push_state():
        from shared import push as _push
        return _push.status()

    @app.post("/api/push")
    async def push_set(payload: dict = Body(default={})):
        from shared import push as _push
        return _push.set_enabled(bool(payload.get("enabled", True)))

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
                items.append({"name": rn, "count": c,
                              "search": str(r.get("search", "")).strip()})
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
                  count=payload.get("count", 1),
                  search=payload.get("search", ""),
                  station=payload.get("station", ""),
                  writ_mode=str(payload.get("writ_mode", "standard")))
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
    async def launch(bot_id: str, payload: dict = Body(default={})):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        # The dropdown's crafter rides along so the backend has the character to pick
        # even if the dropdown was never changed (its onchange never fired).
        ch = str(payload.get("character", "")).strip()
        if ch:
            sim.configure(bot_id, character=ch, trade_class=str(payload.get("trade_class", "")).strip())
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

    def _ride_crafter(bot_id: str, payload: dict) -> None:
        ch = str((payload or {}).get("character", "")).strip()
        if ch:
            sim.configure(bot_id, character=ch,
                          trade_class=str((payload or {}).get("trade_class", "")).strip())

    @app.post("/api/bot/{bot_id}/scribemark")
    async def scribemark(bot_id: str, payload: dict = Body(default={})):
        if not _bot_ok(bot_id) or not hasattr(sim, "scribe_mark"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        _ride_crafter(bot_id, payload)
        sim.scribe_mark(bot_id)
        return {"ok": True}

    @app.post("/api/bot/{bot_id}/scriberead")
    async def scriberead(bot_id: str, payload: dict = Body(default={})):
        if not _bot_ok(bot_id) or not hasattr(sim, "scribe_read"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        _ride_crafter(bot_id, payload)
        sim.scribe_read(bot_id)
        return {"ok": True}

    # -- per-bot OCR debug capture (screenshot + log ring buffer, dashboard toggle) --
    @app.get("/api/bot/{bot_id}/debug")
    async def debug_status(bot_id: str):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        return debug.status(bot_id)

    @app.post("/api/bot/{bot_id}/debug")
    async def debug_toggle(bot_id: str, payload: dict = Body(default={})):
        if not _bot_ok(bot_id):
            return JSONResponse({"error": "unknown bot"}, status_code=404)
        on = bool(payload.get("on", not debug.is_enabled(bot_id)))
        return {"ok": True, "enabled": debug.set_enabled(bot_id, on)}

    @app.get("/api/bot/{bot_id}/debug/shot/{name}")
    async def debug_shot(bot_id: str, name: str):
        p = debug.shot_path(name)
        if not p:
            return Response(status_code=404)
        return Response(content=p.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    # -- in-guest reflex agent channel (the agent is an outbound-only HTTP client) --
    @app.get("/api/agent/{bot_id}/command")
    async def agent_command(bot_id: str):
        if not hasattr(sim, "agent_command"):
            return {"action": "idle", "epoch": 0}
        return sim.agent_command(bot_id)

    @app.post("/api/agent/{bot_id}/command")
    async def set_agent_command(bot_id: str, payload: dict = Body(default={})):
        """Externally set what an agent should do (e.g. trigger the healer's 'heal'
        loop, or 'idle' to stop it). The agent picks it up on its next poll."""
        if not hasattr(sim, "set_agent_command"):
            return JSONResponse({"error": "unavailable"}, status_code=400)
        action = str(payload.get("action", "idle"))
        params = {k: v for k, v in (payload or {}).items() if k != "action"}
        epoch = sim.set_agent_command(bot_id, action, **params)
        return {"ok": True, "epoch": epoch}

    @app.post("/api/agent/{bot_id}/telemetry")
    async def agent_telemetry(bot_id: str, payload: dict = Body(default={})):
        if hasattr(sim, "agent_push"):
            sim.agent_push(bot_id, payload)
        return {"ok": True}

    @app.get("/api/agent/{bot_id}/status")
    async def agent_status(bot_id: str):
        if not hasattr(sim, "agent_status"):
            return {"alive": False}
        return sim.agent_status(bot_id)

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

    # Scraped recipe data for the in-dashboard recipe browser (recipes.html). Mounted
    # before the catch-all so /recipedata/* serves the per-class JSON same-origin.
    RECIPE_DATA = Path(__file__).resolve().parents[2] / "tools" / "recipe_scrape" / "data"
    if RECIPE_DATA.exists():
        app.mount("/recipedata", StaticFiles(directory=str(RECIPE_DATA)), name="recipedata")

    if STATIC.exists():
        app.mount("/", web_common.app_statics(STATIC), name="static")  # + web_common fallthrough (P0.5)

    return app
