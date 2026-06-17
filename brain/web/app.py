"""FastAPI app: serves the dashboard, streams telemetry over websocket, and
exposes manual controls (PROJECT.md §7) — force combat/follow/rez, pause,
emergency stop."""
from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
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

# Healer VM domain (matches stop_bot.sh). When it's powered off the agent stops
# refreshing FRAME_PPM, so the file holds a STALE frame — we'd serve last-known
# state forever. Check domstate so the live view can show "powered off" instead.
VIRSH = ["sudo", "-n", "virsh", "-c", "qemu:///system"]
HEALER_DOM = "iksar_buddy"
_vm_state: dict = {"ts": 0.0, "off": False}


def _healer_powered_off() -> bool:
    """Cached (~2s) domstate probe. Keeps the prior reading on a transient virsh
    hiccup so a one-off failure never falsely flips the view to 'powered off'."""
    now = time.time()
    if now - _vm_state["ts"] > 2.0:
        try:
            r = subprocess.run(VIRSH + ["domstate", HEALER_DOM],
                               capture_output=True, text=True, timeout=4)
            if r.returncode == 0:
                _vm_state.update(ts=now, off=("shut off" in (r.stdout or "")))
        except Exception:
            pass
    return _vm_state["off"]


def _save_config(path: Path, text: str) -> None:
    """Persist owner config safely: back up the previous version (.bak) and write
    ATOMICALLY (tmp + rename) so a crash or partial write can't corrupt or lose
    it. The config dir itself lives OUTSIDE the code-deploy path (IB_CONFIG_DIR)
    so deploys never clobber owner edits."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

_OVERRIDES = {
    "force_combat": Override.FORCE_COMBAT,
    "force_ooc": Override.FORCE_OOC,
    "force_follow": Override.FORCE_FOLLOW,
    "force_rez": Override.FORCE_REZ,
}

# Per-member manual actions -> ability role. Cure is GENERIC now, so every cure_*
# button maps to the one 'cure'; rez targets the member (revive button per slot).
_MEMBER_ACTIONS = {"heal": "direct_heal", "ward": "ward", "hot": "hot", "rez": "rez",
                   "follow": "follow", "cure": "cure",
                   **{f"cure_{c}": "cure" for c in CURE_TYPES}}
# Group / utility actions (no per-member slot). Maps the dashboard button to the
# ability role; the agent resolves the role -> key from the keymap.
_GROUP_ACTIONS = {
    "group_heal": "group_heal", "group_ward": "group_ward", "group_cure": "cure",
    "hot": "hot", "group_hot": "group_hot", "emergency_hot": "emergency_hot",
    "emergency_heal": "emergency_heal", "emergency_ward": "emergency_ward",
    "follow": "follow", "stop_follow": "stop_follow", "rez": "rez",
    "attack": "attack", "spell_attack": "spell_attack",
    "debuff": "debuff", "deaggro": "deaggro", "call_home": "call_home",
    "jump": "jump", "sow": "sow", "hail": "hail", "collect": "collect",
    "gather": "gather", "evac": "evac", "pre_pull": "pre_pull",
}


def create_app(brain: Brain, telemetry: Telemetry) -> FastAPI:
    app = FastAPI(title="ib", docs_url=None, redoc_url=None)

    def _profile_state() -> dict:
        return {"active": brain.cfg.active_profile,
                "available": brain.cfg.list_profiles(),
                "healer": brain.cfg.healer_class,
                "maint_role": brain.cfg.maint_role,                 # ward | hot
                "group_maint_role": brain.cfg.group_maint_role,     # group_ward | group_hot
                "names": {str(k): v for k, v in brain.cfg.names.items()}}

    # seed the dashboard with the current profile
    telemetry.update(profile=_profile_state())

    @app.get("/api/snapshot")
    async def snapshot():
        return telemetry.snapshot

    @app.get("/api/profiles")
    async def get_profiles():
        return _profile_state()

    @app.post("/api/profile/{name}")
    async def set_profile(name: str):
        if not brain.cfg.set_profile(name):
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        await brain.push_config()          # re-push keymap + names to the agent
        telemetry.update(profile=_profile_state())
        telemetry.push_event("config", f"profile -> {name} ({brain.cfg.healer_class})")
        return {"ok": True, **_profile_state()}

    @app.post("/api/profile/{name}/swap")
    async def swap_profile(name: str):
        """Camp-and-switch: log the current toon out to char-select, pick the target
        profile's character, then (only if that succeeds) commit the profile config
        swap. Lets the owner change healer/character in-game without a relog — works
        for same-account toons (Jenskin<->Croolst); cross-account needs Stop+Launch."""
        from ..charswitch import healer_switch
        if name not in brain.cfg.list_profiles():
            return JSONResponse({"error": "unknown profile"}, status_code=404)
        target_char = brain.cfg.peek_select_character(name)
        if not target_char:
            return JSONResponse({"error": "profile has no character"}, status_code=400)
        telemetry.push_event("control", f"camp+switch -> {name} ({target_char})")

        async def _go():
            loop = asyncio.get_running_loop()
            # push_event touches asyncio.Queues -> hop back to the loop thread
            tlog = lambda m: loop.call_soon_threadsafe(telemetry.push_event, "switch", m)
            # disarm while we switch so the loop can't fire stray keys mid-camp
            await brain.send("command", role="_pause", key="", target_slot=None,
                             reason="camp+switch")
            ok = await loop.run_in_executor(None, healer_switch, target_char, tlog)
            if ok:
                brain.cfg.set_profile(name)
                await brain.push_config()
                telemetry.update(profile=_profile_state())
                telemetry.push_event("config", f"profile -> {name} ({brain.cfg.healer_class})")
            else:
                telemetry.push_event("switch", "camp+switch failed — profile unchanged")
        asyncio.create_task(_go())
        return {"ok": True, "swap": name, "character": target_char}

    @app.get("/api/frame.jpg")
    async def frame():
        """Live VM view: the agent's latest framebuffer as a downscaled JPEG.
        Cached ~0.7s (the agent only refreshes ~1Hz) so rapid <img> reloads are
        cheap. Returns 503 until the first frame exists, 409 when the VM is
        powered off (so the view shows 'powered off', not a stale frame)."""
        if await asyncio.get_running_loop().run_in_executor(None, _healer_powered_off):
            _frame_cache["data"] = b""   # drop stale so a restart can't flash it
            return Response(status_code=409)
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

    # ---- keybind map: read + edit from the web (self-service config) --------
    _KEYMAP_HEADER = (
        "# Ability -> keybind map (OWNER-OWNED). Edited from the dashboard keymap\n"
        "# page or by hand. Roles are referenced by the code; you map each to a key.\n"
        "# Key format: bare ('4','f2') or 'Mod+Key' ('Ctrl+1','Alt+='). mode: auto =\n"
        "# the loop may fire it; manual = dashboard button only. Keep keys off chat\n"
        "# triggers (Enter, /, ', ...).\n\n"
    )

    @app.get("/api/keymap")
    async def get_keymap():
        return brain.cfg.ability_map

    @app.post("/api/keymap")
    async def post_keymap(payload: dict = Body(...)):
        if not isinstance(payload.get("abilities"), dict):
            return JSONResponse({"error": "missing abilities"}, status_code=400)
        path = brain.cfg.config_dir / "ability_map.yaml"
        try:
            body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False,
                                  allow_unicode=True)
            _save_config(path, _KEYMAP_HEADER + body)
            brain.cfg.reload_if_changed()
        except Exception as e:  # pragma: no cover
            return JSONResponse({"error": str(e)}, status_code=500)
        telemetry.push_event("config", "keymap saved")
        return {"ok": True}

    # --- live-tunable numeric thresholds (edited from the dashboard) ----------
    # Allowed keys -> (caster, min, max). Edits patch thresholds.yaml in place
    # (comments preserved) and hot-reload.
    _TUNABLES = {
        "ward_heartbeat_s": (float, 0.0, 60.0),
        "hp_standard": (float, 0.0, 1.0),
        "mana_floor": (float, 0.0, 1.0),
    }

    @app.get("/api/tunables")
    async def get_tunables():
        return {k: brain.cfg.threshold(k) for k in _TUNABLES}

    @app.post("/api/tunables")
    async def post_tunables(payload: dict = Body(...)):
        path = brain.cfg.config_dir / "thresholds.yaml"
        try:
            text = path.read_text(encoding="utf-8")
            applied = {}
            for k, (cast, lo, hi) in _TUNABLES.items():
                if k not in payload:
                    continue
                try:
                    v = max(lo, min(hi, cast(payload[k])))
                except (ValueError, TypeError):
                    continue
                if cast is float:
                    v = round(v, 3)
                pat = re.compile(rf"^(\s*{re.escape(k)}:\s*)(\S+)(.*)$", re.M)
                if pat.search(text):
                    text = pat.sub(lambda m: f"{m.group(1)}{v}{m.group(3)}", text, count=1)
                else:
                    text = text.rstrip() + f"\n{k}: {v}\n"
                applied[k] = v
            _save_config(path, text)
            brain.cfg.reload_if_changed()
        except Exception as e:  # pragma: no cover
            return JSONResponse({"error": str(e)}, status_code=500)
        telemetry.push_event("config", "tuning: " + ", ".join(f"{k}={v}" for k, v in applied.items()))
        return {"ok": True, "applied": applied}

    @app.post("/api/role/{slot}/{role}")
    async def set_role(slot: int, role: str):
        """Assign a group slot's role from the dashboard. tank_slot is DERIVED
        from wherever 'tank' lands so the loop targets the right F-key even when
        the tank isn't in slot 1. Persists + hot-reloads."""
        if not (0 <= slot < 6) or role not in ("healer", "tank", "dps", "support", "none"):
            return JSONResponse({"error": "bad slot/role"}, status_code=400)
        am = brain.cfg.ability_map
        roles = list((am.get("slot_roles") or []) + [""] * 6)[:6]
        roles[slot] = role
        am["slot_roles"] = roles
        am["tank_slot"] = roles.index("tank") if "tank" in roles else am.get("tank_slot", 0)
        path = brain.cfg.config_dir / "ability_map.yaml"
        try:
            _save_config(path, _KEYMAP_HEADER + yaml.safe_dump(am, sort_keys=False,
                         default_flow_style=False, allow_unicode=True))
            brain.cfg.reload_if_changed()
        except Exception as e:  # pragma: no cover
            return JSONResponse({"error": str(e)}, status_code=500)
        telemetry.push_event("config", f"slot {slot} role -> {role}")
        return {"ok": True, "slot_roles": roles, "tank_slot": am["tank_slot"]}

    @app.post("/api/override/{name}")
    async def override(name: str):
        if name == "clear":
            await brain.apply_override(None)
            return {"ok": True, "override": None}
        ov = _OVERRIDES.get(name)
        if ov is None:
            return JSONResponse({"error": "unknown override"}, status_code=400)
        await brain.apply_override(ov)
        # Force In Combat = engage NOW: target the TANK then press attack, so we
        # assist onto the tank's target (EQ2 implied-target) rather than nothing.
        # Manual, so it fires even while the auto-loop is disarmed.
        if name == "force_combat":
            akey = brain.cfg.key_for("attack")
            if akey and akey != "none":
                slot = int(brain.cfg.ability_map.get("tank_slot", 0))
                telemetry.push_event("manual", "force combat -> assist tank")
                await brain.send("command", role="attack", key=akey, target_slot=slot,
                                 manual=True, reason="force combat: assist tank")
        # Force OOC = disengage NOW: tap Esc twice (clear target / stop attack /
        # close any open window), then re-target the tank so we're ready to heal it.
        elif name == "force_ooc":
            gtk = brain.cfg.ability_map.get("group_target_keys") or []
            slot = int(brain.cfg.ability_map.get("tank_slot", 0))
            tkey = gtk[slot] if 0 <= slot < len(gtk) else None
            seq = "Esc,pause_0.15,Esc" + (f",pause_0.15,{tkey}" if tkey else "")
            telemetry.push_event("manual", "force ooc -> esc x2 + target tank")
            await brain.send("command", role="force_ooc", key=seq, target_slot=None,
                             manual=True, reason="force ooc: esc x2 + retarget tank")
        return {"ok": True, "override": ov.value}

    @app.post("/api/spice/restart")
    async def spice_restart():
        """Bounce the in-browser console tunnel (websockify -> VM SPICE) if keys/
        input stop passing through. Doesn't touch the VM or the bot."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "restart", "ib-spice.service",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out = (await proc.communicate())[0].decode(errors="replace")
            ok = proc.returncode == 0
        except Exception as e:  # pragma: no cover
            return JSONResponse({"error": str(e)}, status_code=500)
        telemetry.push_event("control",
                             "console tunnel restarted" if ok else f"console restart failed: {out[:80]}")
        return {"ok": ok}

    @app.post("/api/combat/reset")
    async def combat_reset():
        """Unstick combat: clear any latched override, force OOC now, and tell the
        agent to discard the combat lines it has already seen. Auto-detection then
        resumes on the next NEW combat line -- no babysitting an override."""
        await brain.apply_override(None)
        brain.sm.on_combat_signal(False)
        await brain.send("command", role="_reset_combat", key="", target_slot=None)
        telemetry.push_event("control", "combat reset (unstick)")
        return {"ok": True}

    @app.post("/api/act/{action}/{slot}")
    async def act_member(action: str, slot: int):
        role = _MEMBER_ACTIONS.get(action)
        if role is None or not (0 <= slot < 6):
            return JSONResponse({"error": "unknown member action"}, status_code=400)
        members = telemetry.snapshot.get("members", [])
        name = next((m.get("name") for m in members if m.get("slot") == slot), None) or f"slot{slot}"
        telemetry.push_event("manual", f"{action} -> {name}")
        await brain.send("command", role=role, key=brain.cfg.key_for(role),
                         target_slot=slot, manual=True, reason=f"manual {action} on {name}")
        return {"ok": True, "action": action, "slot": slot}

    # buff_* buttons resolve the right group slot from slot_roles, so the agent
    # targets that member's F-key BEFORE casting (single-target buffs land on the
    # intended player instead of whatever happens to be targeted).
    _BUFF_TARGET_ROLE = {"buff_tank": "tank", "buff_dps": "dps", "buff_self": "healer"}

    def _slot_for_role(want: str):
        am = brain.cfg.ability_map
        roles = am.get("slot_roles") or []
        if want == "tank":
            return am.get("tank_slot", roles.index("tank") if "tank" in roles else 0)
        if want == "healer":
            return roles.index("healer") if "healer" in roles else 0
        return roles.index(want) if want in roles else None  # first dps

    @app.post("/api/act/{action}")
    async def act_group(action: str):
        # combined buff: fire buff1 then buff2 as one sequence (pause covers cast).
        if action == "buff":
            keys = [k for k in (brain.cfg.key_for("buff1"), brain.cfg.key_for("buff2"))
                    if k and k != "none"]
            if not keys:
                return JSONResponse({"error": "no buff keys mapped"}, status_code=400)
            telemetry.push_event("manual", "buff (1+2)")
            await brain.send("command", role="buff", key=",pause_1.5,".join(keys),
                             target_slot=None, manual=True, reason="manual buff combo")
            return {"ok": True, "action": action}
        # targeted buffs: pick the member, target their F-key, then cast.
        if action in _BUFF_TARGET_ROLE:
            slot = _slot_for_role(_BUFF_TARGET_ROLE[action])
            key = brain.cfg.key_for(action)
            telemetry.push_event("manual", f"{action.replace('_', ' ')} -> slot {slot}")
            await brain.send("command", role=action, key=key,
                             target_slot=slot, manual=True, reason=f"manual {action}")
            return {"ok": True, "action": action, "slot": slot}
        # food: target the tank (F2), consume (Ctrl+0), wait for the cast, then
        # target self (F1) and consume again. One-shot manual macro.
        if action == "food":
            gtk = brain.cfg.ability_map.get("group_target_keys") or ["F1", "F2", "F3", "F4", "F5", "F6"]
            tslot = int(brain.cfg.ability_map.get("tank_slot", 1))
            tank_key = gtk[tslot] if 0 <= tslot < len(gtk) else "F2"
            self_key = gtk[0] if gtk else "F1"
            seq = f"{tank_key},Ctrl+0,pause_1,{self_key},Ctrl+0"
            telemetry.push_event("manual", "food -> tank + self")
            await brain.send("command", role="food", key=seq,
                             target_slot=None, manual=True, reason="food: tank then self (Ctrl+0)")
            return {"ok": True, "action": "food"}
        # spell_attack: target the TANK first so EQ2 implied-targeting lands the
        # offensive cast on the tank's target instead of whatever's selected.
        if action == "spell_attack":
            key = brain.cfg.key_for("spell_attack")
            if not key or key == "none":
                return JSONResponse({"error": "no spell_attack key mapped"}, status_code=400)
            slot = int(brain.cfg.ability_map.get("tank_slot", 0))
            telemetry.push_event("manual", "spell attack -> tank")
            await brain.send("command", role="spell_attack", key=key,
                             target_slot=slot, manual=True, reason="spell attack (assist tank)")
            return {"ok": True, "action": action, "slot": slot}
        role = _GROUP_ACTIONS.get(action)
        if role is None:
            return JSONResponse({"error": "unknown group action"}, status_code=400)
        telemetry.push_event("manual", action.replace("_", " "))
        await brain.send("command", role=role, key=brain.cfg.key_for(role),
                         target_slot=None, manual=True, reason=f"manual {action}")
        return {"ok": True, "action": action}

    async def _run_bot_script(name: str, *args: str):
        """Run a host orchestration script (~/ib-build/<name>) in the background,
        streaming each output line to the dashboard event stream."""
        path = str(Path.home() / "ib-build" / name)
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", path, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except Exception as e:  # pragma: no cover
            telemetry.push_event("bot", f"failed to start {name}: {e}")
            return None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                telemetry.push_event("bot", line)
        await proc.wait()
        return proc.returncode

    @app.post("/api/launch")
    async def launch():
        """Launch Bot: power on the VM and log DIRECTLY into the active profile's
        character (forge.login.LoginDriver — same path as the crafters). No more
        char-select OCR pick; the EQ2 login form takes the character by name."""
        from ..charswitch import healer_login
        telemetry.update(vm={**telemetry.snapshot.get("vm", {}), "running": True})
        telemetry.push_event("control", "Launch Bot")

        async def _go():
            loop = asyncio.get_running_loop()
            # push_event touches asyncio.Queues -> must hop back to the loop thread
            tlog = lambda m: loop.call_soon_threadsafe(telemetry.push_event, "launch", m)
            char = brain.cfg.select_character
            ok = await loop.run_in_executor(None, healer_login, char, tlog)
            if not ok:
                telemetry.push_event("launch", f"login failed for {char}")
                return
            await _run_bot_script("ensure_logging.sh")    # combat-log on (detection signal)
            telemetry.push_event("launch", f"in-world as {char}; logging on")
        asyncio.create_task(_go())
        return {"ok": True, "launch": "started"}

    @app.post("/api/stop")
    async def stop():
        """Stop Bot: press the camp key (clean logout) then shut down the VM."""
        camp = brain.cfg.key_for("camp") or "none"
        camp_wait = str(int(brain.cfg.threshold("camp_wait_s", 25)))
        telemetry.push_event("control", "Stop Bot (camp + shutdown)")

        async def _go():
            await _run_bot_script("stop_bot.sh", camp, camp_wait)
            telemetry.update(vm={**telemetry.snapshot.get("vm", {}), "running": False})
        asyncio.create_task(_go())
        return {"ok": True, "stop": "started"}

    @app.post("/api/shutdown")
    async def shutdown():
        """Shutdown: power off the healer VM directly (no camp wait). Windows ACPI
        shutdown closes EQ2 cleanly; forces off if it hangs (stop_bot.sh "none")."""
        telemetry.push_event("control", "Shutdown VM (no camp)")

        async def _go():
            await _run_bot_script("stop_bot.sh", "none", "0")
            telemetry.update(vm={**telemetry.snapshot.get("vm", {}), "running": False})
        asyncio.create_task(_go())
        return {"ok": True, "shutdown": "started"}

    # Dialog accepts: run the host-side OCR-and-click helper ONCE on click (no
    # background watching). Each entry is the helper's argv. quest passes --accept
    # so the manual button accepts whatever quest is shown (the policy allowlist is
    # for autonomous use only, which isn't wired).
    _ACCEPT = {"invite": ["invite_accept.py"],
               "quest": ["quest_accept.py", "--accept"],
               "revive": ["revive_accept.py"]}

    async def _run_helper(argv: list, label: str):
        path = str(Path.home() / "ib-build" / argv[0])
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", path, *argv[1:], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
        except Exception as e:  # pragma: no cover
            telemetry.push_event("accept", f"{label}: {e}"); return
        out = (await proc.communicate())[0].decode(errors="replace")
        last = next((l for l in reversed(out.splitlines()) if l.strip()), "")
        telemetry.push_event("accept", f"{label}: {last[:80]}")

    @app.post("/api/accept/{what}")
    async def accept(what: str):
        argv = _ACCEPT.get(what)
        if argv is None:
            return JSONResponse({"error": "unknown accept"}, status_code=400)
        telemetry.push_event("accept", f"accept {what} requested")
        asyncio.create_task(_run_helper(argv, f"accept {what}"))
        return {"ok": True, "accept": what}

    @app.post("/api/nudge/{d}")
    async def nudge(d: str):
        """Tap-hold a movement key (~0.3s) -- WASD nudge buttons."""
        if d not in ("w", "a", "s", "d"):
            return JSONResponse({"error": "bad direction"}, status_code=400)
        await brain.send("command", role=f"nudge_{d}", key=f"hold_{d}_0.3",
                         target_slot=None, manual=True, reason=f"nudge {d}")
        return {"ok": True, "nudge": d}

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

    # SPICE web console proxy: bridge a browser WebSocket to the VM's local SPICE
    # TCP port, SAME-ORIGIN, so spice-html5 works both on the LAN and remotely THROUGH
    # Cloudflare (wss://<dash>/spice/ws/5900) — inheriting the dashboard's Access auth,
    # no extra hostname/port exposed. Replaces the LAN-only websockify bridge.
    SPICE_PORTS = {5900}                      # healer VM SPICE (allowlist)

    @app.websocket("/spice/ws/{port}")
    async def spice_ws(websocket: WebSocket, port: int):
        if port not in SPICE_PORTS:
            await websocket.close(code=1008); return
        # spice-html5 connects with the 'binary' subprotocol
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
