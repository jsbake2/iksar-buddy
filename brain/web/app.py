"""FastAPI app: serves the dashboard, streams telemetry over websocket, and
exposes manual controls (PROJECT.md §7) — force combat/follow/rez, pause,
emergency stop."""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..server import Brain
from ..state import Override
from ..telemetry import CURE_TYPES, Telemetry

STATIC = Path(__file__).resolve().parent / "static"
# The host sensor agent writes the latest VM framebuffer here every cycle
# (host_sensor.PPM). We serve it as a JPEG for the dashboard's live-view panel,
# so the dashboard shows the real game with NO extra screenshot cost.
FRAME_PPM = "/tmp/ib_sensor.ppm"
_frame_cache: dict = {"ts": 0.0, "data": b""}

_OVERRIDES = {
    "force_combat": Override.FORCE_COMBAT,
    "force_ooc": Override.FORCE_OOC,
    "force_follow": Override.FORCE_FOLLOW,
    "force_rez": Override.FORCE_REZ,
}

# Per-member manual actions (modernized from the prior tool's action_list):
# heal / ward / cure_<type>. Each maps to an ability role the agent resolves.
_MEMBER_ACTIONS = {"heal": "direct_heal", "ward": "ward",
                   **{f"cure_{c}": f"cure_{c}" for c in CURE_TYPES}}
# Group + emergency actions (no per-member slot).
_GROUP_ACTIONS = {
    "group_heal": "group_heal",
    "group_ward": "group_ward",
    "group_cure": "group_cure",
    "emergency_heal": "emergency_heal",
    "emergency_ward": "emergency_ward",
    "follow": "follow",
    "rez": "rez",
    "debuff": "debuff",
    "buff": "buff",
}


def create_app(brain: Brain, telemetry: Telemetry) -> FastAPI:
    app = FastAPI(title="ib", docs_url=None, redoc_url=None)

    @app.get("/api/snapshot")
    async def snapshot():
        return telemetry.snapshot

    @app.get("/api/frame.jpg")
    async def frame():
        """Live VM view: the agent's latest framebuffer as a downscaled JPEG.
        Cached ~0.7s (the agent only refreshes ~1Hz) so rapid <img> reloads are
        cheap. Returns 503 until the first frame exists."""
        now = time.time()
        if now - _frame_cache["ts"] > 0.7:
            try:
                out = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: subprocess.run(
                        ["magick", FRAME_PPM, "-scale", "960", "-quality", "70", "jpg:-"],
                        capture_output=True, timeout=4).stdout)
                if out:
                    _frame_cache.update(ts=now, data=out)
            except Exception:
                pass
        if not _frame_cache["data"]:
            return Response(status_code=503)
        return Response(content=_frame_cache["data"], media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/override/{name}")
    async def override(name: str):
        if name == "clear":
            await brain.apply_override(None)
            return {"ok": True, "override": None}
        ov = _OVERRIDES.get(name)
        if ov is None:
            return JSONResponse({"error": "unknown override"}, status_code=400)
        await brain.apply_override(ov)
        return {"ok": True, "override": ov.value}

    @app.post("/api/act/{action}/{slot}")
    async def act_member(action: str, slot: int):
        role = _MEMBER_ACTIONS.get(action)
        if role is None or not (0 <= slot < 6):
            return JSONResponse({"error": "unknown member action"}, status_code=400)
        members = telemetry.snapshot.get("members", [])
        name = next((m.get("name") for m in members if m.get("slot") == slot), None) or f"slot{slot}"
        telemetry.push_event("manual", f"{action} -> {name}")
        await brain.send("command", role=role, key="", target_slot=slot,
                         reason=f"manual {action} on {name}")
        return {"ok": True, "action": action, "slot": slot}

    @app.post("/api/act/{action}")
    async def act_group(action: str):
        role = _GROUP_ACTIONS.get(action)
        if role is None:
            return JSONResponse({"error": "unknown group action"}, status_code=400)
        telemetry.push_event("manual", action.replace("_", " "))
        await brain.send("command", role=role, key="", target_slot=None,
                         reason=f"manual {action}")
        return {"ok": True, "action": action}

    @app.post("/api/launch")
    async def launch():
        # Future: host virsh-start -> agent launcher boots the client into group.
        # For now stub the telemetry so the dashboard button is wired end-to-end.
        telemetry.update(vm={**telemetry.snapshot.get("vm", {}), "running": True})
        telemetry.push_event("control", "launch requested (host virsh start stub)")
        return {"ok": True, "launch": "requested"}

    @app.post("/api/control/{name}")
    async def control(name: str):
        # pause / resume / estop are surfaced to the agent as commands.
        if name in ("pause", "resume", "estop"):
            await brain.send("command", role=f"_{name}", key="", target_slot=None,
                             reason=f"dashboard {name}")
            telemetry.push_event("control", name)
            return {"ok": True, "control": name}
        return JSONResponse({"error": "unknown control"}, status_code=400)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = telemetry.subscribe()
        try:
            await websocket.send_json(telemetry.snapshot)  # prime
            while True:
                snap = await q.get()
                await websocket.send_json(snap)
        except WebSocketDisconnect:
            pass
        finally:
            telemetry.unsubscribe(q)

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")

    return app
