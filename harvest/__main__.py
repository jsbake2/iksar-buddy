"""ibh — harvest dashboard + controller entrypoint (HARVEST.md).

    python -m harvest --web-port 18082    # -> harvest.jsb-emr.us

Host-side for now: polls the in-guest memory reader via guest-exec for the dashboard
(slow but fine for display); the real-time nav loop will move in-guest later. Opsec:
process title 'ibh'.
"""
from __future__ import annotations

import argparse, asyncio, base64, contextlib, json, math, os, re, subprocess, time
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, Body, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    import setproctitle
except ImportError:
    setproctitle = None

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from forge.guest import Guest                      # reuse the proven host I/O core
from forge.login import LoginDriver, load_accounts
from harvest.nav_graph import Graph as NavGraph    # dense waypoint graph (OgreNav-style)

DOM = "iksar_buddy"                                 # the GPU VM
SPICE_PORT = 5900                                  # iksar_buddy SPICE (same as the healer)
GUEST_PY = r"C:\ib\py\python.exe"
GUEST_READER = r"C:\ib\agent\harvest_read.py"
GUEST_SPAWNS = r"C:\ib\agent\spawns_live.py"       # nearby-entity scanner (vtable RE)
GUEST_PUSH = r"C:\ib\agent\sense_push.py"          # persistent sensor (HTTP push, real-time)
DATA = Path(os.environ.get("IB_DATA_DIR", str(Path.home() / "ib-data"))) / "harvest"
DATA.mkdir(parents=True, exist_ok=True)
# EQ2Emu launches straight to the game LOGIN FORM from EverQuest2.exe (no LaunchPad needed).
LAUNCH_AHK = (
    '#Requires AutoHotkey v2.0\n'
    'EQDIR := "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest II"\n'
    "Run('\"' EQDIR '\\EverQuest2.exe\"', EQDIR)\n"
)
VIRSH = ["sudo", "-n", "virsh", "-c", "qemu:///system"]
# Game login form (EQ2 fullscreen 1920x1080): click the username field to set focus, OCR it to
# verify the username actually changed (the silent-stale-username bug). Measured 2026-06-24.
USER_CLICK = (743, 442)
USERNAME_OCR = (700, 432, 112, 22)
HARVEST_DB = DATA / "harvested.json"               # all-time harvested-item tallies
# EQ2 chat/combat log (the authoritative event stream). server=Wuoshi; char filled per-login.
EQ2_LOG = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
           r"\EverQuest II\logs\Wuoshi\eq2log_{char}.txt")

# --- log line patterns ------------------------------------------------------
RE_HARVEST = re.compile(r"You (mine|forage|gather|fell|trap|acquire|catch|chop|cut) (\d+) "
                        r"\\aITEM [^:]*:([^\\]+)\\/a from the (.+?)\.")
RE_RARE    = re.compile(r"You have found a rare item")
RE_TELL    = re.compile(r"\\aPC[^:]*:([^\\]+)\\/a tells you,\s*\"(.*?)\"")
RE_COMBAT  = re.compile(r"(hits? YOU|YOU take|tries to (?:hit|slash|crush|pierce) YOU|"
                        r"You are no longer)", re.I)
VERB_TYPE  = {"mine": "ore", "forage": "groundcover", "gather": "bush/roots",
              "fell": "wood", "chop": "wood", "trap": "den", "acquire": "den",
              "catch": "fish", "cut": "wood"}
ROUTES_FILE = DATA / "routes.json"
WEB = Path(__file__).resolve().parent / "web"

# movement keys (EQ2 defaults; tunable). down/up via AHK so we can blend/hold.
MOVE_KEYS = {"forward": "w", "back": "s", "left": "a", "right": "d",
             "strafeL": "q", "strafeR": "e", "jump": "Space"}


class Harvest:
    def __init__(self) -> None:
        self.g = Guest(DOM)
        self.state: dict = {"ok": False, "err": "starting"}   # guest-exec fallback reader
        self.pushed: dict = {"st": {"ok": False}, "ts": 0.0}  # fast push from in-guest sensor
        self.spawns: dict = {"mobs": [], "nodes": [], "ts": 0}   # vtable-scan cache (slow scan)
        self.active_char: str = "Furyflatulence"
        self.recording: dict | None = None        # {name, zone, points:[[x,y,z,t]]}
        # log-derived state, DURABLY PERSISTED server-side (survives restarts; client only
        # displays it). Stored whole in HARVEST_DB.
        saved = {}
        try:
            saved = json.loads(HARVEST_DB.read_text()) if HARVEST_DB.exists() else {}
        except Exception:
            saved = {}
        # back-compat: old DB was just the all_time dict
        if saved and "all_time" not in saved and isinstance(next(iter(saved.values()), {}), dict):
            saved = {"all_time": saved}
        self.harvest_log = {"session": saved.get("session", {}),
                            "all_time": saved.get("all_time", {}),
                            "rares": saved.get("rares", []),
                            "tells": saved.get("tells", []),
                            "combat_ts": 0, "_off": None}
        self.routes: dict = json.loads(ROUTES_FILE.read_text()) if ROUTES_FILE.exists() else {}
        self.graph = None                          # active dense-graph recorder (NavGraph) or None
        self._graph_n = 0                          # point count at last save
        self.log: list[str] = []
        # character roster — select-from-table, no typing. character -> account user;
        # password resolved from the gitignored accounts.yaml. Seed with Furyflatulence.
        self.chars_file = DATA / "characters.yaml"
        if not self.chars_file.exists():
            self.chars_file.write_text(yaml.safe_dump(
                {"characters": [{"character": "Furyflatulence", "user": "meatwad33w",
                                 "class": "fury", "zone": "Thundering Steppes"}]}))

    def _persist_harvest(self) -> None:
        hl = self.harvest_log
        try:
            tmp = HARVEST_DB.with_suffix(".tmp")
            tmp.write_text(json.dumps({"session": hl["session"], "all_time": hl["all_time"],
                                       "rares": hl["rares"], "tells": hl["tells"]}))
            os.replace(tmp, HARVEST_DB)
        except Exception:
            pass

    def _user_pw(self) -> dict:
        """user -> password from accounts.yaml (keyed by VM dom there)."""
        accts, _ = load_accounts()
        return {a.get("user"): a.get("password") for a in accts.values() if a.get("user")}

    def characters(self) -> list[dict]:
        try:
            cfg = yaml.safe_load(self.chars_file.read_text()) or {}
        except Exception:
            cfg = {}
        return cfg.get("characters", [])

    def _creds_for(self, character: str) -> tuple[str, str]:
        for c in self.characters():
            if c.get("character") == character:
                return c.get("user", ""), self._user_pw().get(c.get("user", ""), "")
        return "", ""

    # --- guest exec helper -------------------------------------------------
    def _gx(self, path: str, args: list[str], wait: float = 8.0) -> str:
        j = json.dumps({"execute": "guest-exec", "arguments":
                        {"path": path, "arg": args, "capture-output": True}})
        r = subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system",
                            "qemu-agent-command", DOM, j], capture_output=True, text=True)
        try:
            pid = json.loads(r.stdout)["return"]["pid"]
        except Exception:
            return ""
        out = ""
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.3)
            s = json.dumps({"execute": "guest-exec-status", "arguments": {"pid": pid}})
            r2 = subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system",
                                 "qemu-agent-command", DOM, s], capture_output=True, text=True)
            try:
                d = json.loads(r2.stdout)["return"]
            except Exception:
                continue
            if d.get("exited"):
                for k in ("out-data", "err-data"):
                    if d.get(k):
                        out += base64.b64decode(d[k]).decode("utf-8", "replace")
                break
        return out

    def deploy_reader(self) -> None:
        """Push memory_read.py into the guest as the reader."""
        src = (Path(__file__).resolve().parent / "memory_read.py").read_bytes()
        b64 = base64.b64encode(src).decode()
        # chunk to be safe on arg length
        # remove BOTH the .py and the staging .b64 — Add-Content appends, so a stale
        # .b64 would concatenate a second copy and corrupt the file (double __future__).
        self._gx("powershell", ["-NoProfile", "-Command",
                                f"Remove-Item '{GUEST_READER}','{GUEST_READER}.b64' -EA SilentlyContinue"])
        for i in range(0, len(b64), 6000):
            self._gx("powershell", ["-NoProfile", "-Command",
                                    f"Add-Content -Path '{GUEST_READER}.b64' -Value '{b64[i:i+6000]}' -NoNewline"])
        self._gx("powershell", ["-NoProfile", "-Command",
                 f"[IO.File]::WriteAllBytes('{GUEST_READER}',[Convert]::FromBase64String((Get-Content -Raw '{GUEST_READER}.b64')))"])

    def deploy_spawns(self) -> None:
        """Push spawns_live.py (vtable nearby-entity scanner) into the guest."""
        src = (Path(__file__).resolve().parent / "spawns_live.py").read_bytes()
        b64 = base64.b64encode(src).decode()
        self._gx("powershell", ["-NoProfile", "-Command",
                                f"Remove-Item '{GUEST_SPAWNS}','{GUEST_SPAWNS}.b64' -EA SilentlyContinue"])
        for i in range(0, len(b64), 6000):
            self._gx("powershell", ["-NoProfile", "-Command",
                                    f"Add-Content -Path '{GUEST_SPAWNS}.b64' -Value '{b64[i:i+6000]}' -NoNewline"])
        self._gx("powershell", ["-NoProfile", "-Command",
                 f"[IO.File]::WriteAllBytes('{GUEST_SPAWNS}',[Convert]::FromBase64String((Get-Content -Raw '{GUEST_SPAWNS}.b64')))"])

    def read_spawns(self) -> dict:
        """Run the in-guest vtable scan (~5-6s). Player self is the dist~0 actor; mark it."""
        out = self._gx(GUEST_PY, [GUEST_SPAWNS], wait=20)
        for line in out.splitlines()[::-1]:
            line = line.strip()
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                mobs = d.get("mobs", [])
                # drop the player's own actor (nearest, ~0 dist) from the monster list
                others = [m for m in mobs if m.get("dist", 99) > 1.0]
                return {"mobs": others, "nodes": d.get("nodes", []),
                        "player": d.get("player"), "ts": time.time()}
        return {"mobs": [], "nodes": [], "ts": time.time()}

    async def spawns_loop(self) -> None:
        """Background vtable scan (slow ~6s) on its own cadence, separate from the 1.5s
        position poll. Feeds the nearby-mob / harvestable dashboard panels."""
        self.deploy_spawns()
        while True:
            try:
                self.spawns = await asyncio.get_running_loop().run_in_executor(None, self.read_spawns)
            except Exception as e:
                self.log.append(f"spawns scan: {e}")
            await asyncio.sleep(6.0)

    # --- EQ2 log scrape: harvested items, rares, tells, combat -------------
    def _log_path(self) -> str:
        return EQ2_LOG.format(char=self.active_char)

    def _read_log_from(self, off: int | None) -> tuple[str, int]:
        """Return (new_text, new_length). off=None -> just get the current length (skip
        history so we only tally THIS session)."""
        p = self._log_path()
        ln = self._gx("powershell", ["-NoProfile", "-Command",
                                     f"(Get-Item '{p}' -EA SilentlyContinue).Length"]).strip()
        try:
            length = int(ln)
        except Exception:
            return "", off or 0
        if off is None or off > length:
            return "", length
        if off == length:
            return "", length
        txt = self._gx("powershell", ["-NoProfile", "-Command",
            f"$fs=[IO.File]::Open('{p}','Open','Read','ReadWrite');$fs.Seek({off},'Begin')|Out-Null;"
            f"$sr=New-Object IO.StreamReader($fs);$t=$sr.ReadToEnd();$sr.Close();$fs.Close();$t"])
        return txt, length

    def _tally(self, item: str, n: int, node: str, verb: str, rare: bool = False) -> None:
        hl = self.harvest_log
        for bucket in (hl["session"], hl["all_time"]):
            e = bucket.setdefault(item, {"qty": 0, "node": node,
                                         "type": VERB_TYPE.get(verb, verb), "rare": False})
            e["qty"] += n
            e["node"] = node
            if rare:
                e["rare"] = True

    async def log_loop(self) -> None:
        while True:
            try:
                txt, newlen = await asyncio.get_running_loop().run_in_executor(
                    None, self._read_log_from, self.harvest_log["_off"])
                self.harvest_log["_off"] = newlen
                rare_armed = False
                changed = False
                for line in txt.splitlines():
                    m = RE_HARVEST.search(line)
                    if m:
                        verb, n, item, node = m.group(1), int(m.group(2)), m.group(3).strip(), m.group(4).strip()
                        self._tally(item, n, node, verb, rare=rare_armed)
                        if rare_armed:
                            self.harvest_log["rares"].append({"item": item, "node": node, "t": time.time()})
                            self.harvest_log["rares"] = self.harvest_log["rares"][-20:]
                            rare_armed = False
                        changed = True
                        continue
                    if RE_RARE.search(line):
                        rare_armed = True; continue
                    mt = RE_TELL.search(line)
                    if mt:
                        self.harvest_log["tells"].append({"from": mt.group(1), "msg": mt.group(2), "t": time.time()})
                        self.harvest_log["tells"] = self.harvest_log["tells"][-20:]
                        changed = True
                        continue
                    if RE_COMBAT.search(line):
                        self.harvest_log["combat_ts"] = time.time()
                if changed:
                    self._persist_harvest()           # durable server-side write
            except Exception as e:
                self.log.append(f"log scrape: {e}")
            await asyncio.sleep(3.0)

    def start_sensor(self) -> None:
        """Deploy + (re)start the persistent in-guest sensor (reads at ~8Hz, pushes via HTTP).
        Read-only; safe. Kills only the prior sense_push instance, not the nav agent."""
        src = (Path(__file__).resolve().parent / "sense_push.py").read_bytes()
        b64 = base64.b64encode(src).decode()
        self._gx("powershell", ["-NoProfile", "-Command",
                                f"Remove-Item '{GUEST_PUSH}','{GUEST_PUSH}.b64' -EA SilentlyContinue"])
        for i in range(0, len(b64), 6000):
            self._gx("powershell", ["-NoProfile", "-Command",
                                    f"Add-Content -Path '{GUEST_PUSH}.b64' -Value '{b64[i:i+6000]}' -NoNewline"])
        self._gx("powershell", ["-NoProfile", "-Command",
                 f"[IO.File]::WriteAllBytes('{GUEST_PUSH}',[Convert]::FromBase64String((Get-Content -Raw '{GUEST_PUSH}.b64')))"])
        # restart: kill any prior sense_push, launch detached
        self._gx("powershell", ["-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object {$_.CommandLine -like '*sense_push*'} | "
            "ForEach-Object {Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue}; "
            f"Start-Process -FilePath '{GUEST_PY}' -ArgumentList '{GUEST_PUSH}' -WindowStyle Hidden"])

    def read_guest(self) -> dict:
        out = self._gx(GUEST_PY, [GUEST_READER])
        for line in out.splitlines()[::-1]:
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        return {"ok": False, "err": "read failed"}

    def cur_state(self) -> dict:
        """Freshest state: the in-guest sensor's HTTP push if recent (~8 Hz, real-time),
        else the guest-exec reader fallback."""
        if time.time() - self.pushed["ts"] < 3.0:
            return self.pushed["st"]
        return self.state

    # --- background poll (FALLBACK) ----------------------------------------
    async def poll_loop(self) -> None:
        self.deploy_reader()
        while True:
            # only do the heavy guest-exec read when the fast push is stale (sensor down)
            if time.time() - self.pushed["ts"] >= 3.0:
                st = await asyncio.get_running_loop().run_in_executor(None, self.read_guest)
                self.state = st
            cur = self.cur_state()
            if self.recording is not None and cur.get("ok") and cur.get("pos"):
                self._maybe_record(cur["pos"])
            await asyncio.sleep(1.5)

    def _maybe_record(self, pos: list) -> None:
        pts = self.recording["points"]
        if not pts:
            pts.append([*pos, time.time()]); return
        last = pts[-1]
        dist = math.dist(pos[:3], last[:3])
        if dist >= 4.0 or (time.time() - last[3]) >= 4.0:   # sample on distance OR time
            pts.append([*pos, time.time()])

    # --- dense GRAPH recorder (OgreNav-style, runs inside the persistent dashboard) --------
    # Passive: feeds every position push into a waypoint graph while active. Standing still
    # adds nothing (point only every ~3 m of movement); long AFK pauses are harmless. Lives in
    # the dashboard (not a standalone proc) because SSH-launched procs get reaped on the host.
    def _graph_path(self, zone) -> Path:
        z = (zone or "zone").replace(" ", "_").replace("/", "_")
        return DATA / f"graph_{z}.json"

    def _graph_save(self) -> None:
        if self.graph is None:
            return
        DATA.mkdir(parents=True, exist_ok=True)
        self.graph.save(str(self._graph_path(self.graph.zone)))

    def graph_start(self) -> dict:
        zone = (self.cur_state() or {}).get("zone") or "zone"
        p = self._graph_path(zone)
        self.graph = NavGraph.load(str(p)) if p.exists() else NavGraph(zone)
        if self.graph.zone is None:
            self.graph.zone = zone
        self._graph_n = len(self.graph)            # APPEND across sessions -> accumulate
        return self.graph_status()

    def graph_stop(self) -> dict:
        self._graph_save()
        st = self.graph_status(); self.graph = None
        return st

    def _graph_feed(self, pos: list) -> None:
        if self.graph is None:
            return
        before = len(self.graph)
        self.graph.add_point(pos[0], pos[2])        # x,z from [x,y,z]
        if len(self.graph) != before and len(self.graph) % 5 == 0:
            self._graph_save()                      # checkpoint every 5 new points

    def graph_status(self) -> dict:
        if self.graph is None:
            return {"recording": False}
        edges = sum(len(a) for a in self.graph.adj) // 2
        return {"recording": True, "zone": self.graph.zone, "points": len(self.graph),
                "edges": edges, "file": str(self._graph_path(self.graph.zone))}

    # --- actions -----------------------------------------------------------
    def move(self, direction: str, ms: int) -> None:
        key = MOVE_KEYS.get(direction)
        if not key:
            return
        self.g.run_ahk(f'Send("{{{key} down}}")\nSleep {int(ms)}\nSend("{{{key} up}}")\n')

    def stop_keys(self) -> None:
        ups = "".join(f'Send("{{{k} up}}")\n' for k in set(MOVE_KEYS.values()))
        self.g.run_ahk(ups)

    def harvest_key(self) -> None:
        # EQ2 default: target nearest + interact. Owner can rebind; placeholder.
        self.g.run_ahk('Send("{F8}")\nSleep 200\nSend("{Enter}")\n')

    def record_start(self, name: str) -> None:
        zone = (self.state.get("zone") or "unknown")
        self.recording = {"name": name or f"route-{int(time.time())}", "zone": zone, "points": []}

    def record_stop(self) -> dict:
        if not self.recording:
            return {"ok": False, "err": "not recording"}
        r = self.recording; self.recording = None
        if len(r["points"]) < 2:
            return {"ok": False, "err": "too few points"}
        # close the loop if start/end are near
        a, b = r["points"][0][:3], r["points"][-1][:3]
        r["loop"] = math.dist(a, b) < 12.0
        zone = r["zone"]
        self.routes.setdefault(zone, {})[r["name"]] = {"points": [p[:3] for p in r["points"]],
                                                        "loop": r["loop"]}
        ROUTES_FILE.write_text(json.dumps(self.routes, indent=1))
        return {"ok": True, "name": r["name"], "zone": zone,
                "points": len(r["points"]), "loop": r["loop"]}

    def recalibrate(self, x: float, y: float, z: float) -> dict:
        out = self._gx(GUEST_PY, [GUEST_READER, "--recalibrate", str(x), str(y), str(z)], wait=90)
        for line in out.splitlines()[::-1]:
            if line.strip().startswith("{"):
                return json.loads(line.strip())
        return {"ok": False, "err": "recalibrate failed", "raw": out[-200:]}

    def login_char(self, character: str) -> bool:
        user, pw = self._creds_for(character)
        if not (user and pw):
            self.log.append(f"no creds for {character}"); return False
        self.active_char = character           # log scrape follows the active char
        self.harvest_log["_off"] = None        # re-baseline log offset for the new char/session
        self.harvest_log["session"] = {}
        self._persist_harvest()
        drv = LoginDriver(Guest(DOM), lambda m: self.log.append(m))
        return drv.boot_and_login(user, pw, character, "Wuoshi")

    # --- guest helpers for the agent-driven login ----------------------------
    def _push_text(self, path: str, text: str) -> None:
        """Write a UTF-8 text file in the guest via base64 (handles quotes/JSON safely)."""
        b = base64.b64encode(text.encode()).decode()
        self._gx("powershell", ["-NoProfile", "-Command",
                                f"Remove-Item '{path}','{path}.b64' -EA SilentlyContinue"])
        for i in range(0, len(b), 6000):
            self._gx("powershell", ["-NoProfile", "-Command",
                                    f"Add-Content -Path '{path}.b64' -Value '{b[i:i+6000]}' -NoNewline"])
        self._gx("powershell", ["-NoProfile", "-Command",
                 f"[IO.File]::WriteAllBytes('{path}',[Convert]::FromBase64String((Get-Content -Raw '{path}.b64')));"
                 f"Remove-Item '{path}.b64' -EA SilentlyContinue"])

    def _fire_agent(self, target: dict) -> None:
        """Hand the in-guest agent (ibharv) a one-shot job via nav_target.json."""
        self._push_text(r"C:\ib\nav_target.json", json.dumps(target))
        self._gx("powershell", ["-NoProfile", "-Command",
                 "Enable-ScheduledTask -TaskName ibharv | Out-Null;"
                 "Remove-Item C:\\ib\\STOP,C:\\ib\\nav_status.json -EA SilentlyContinue;"
                 "Start-ScheduledTask -TaskName ibharv"])

    def launch_and_login(self, character: str) -> dict:
        """Full hands-off login: launch EQ2 straight to the form (no LaunchPad), wait for the
        form, let the AGENT type the creds (keybd_event — AHK Send doesn't land on this VM's
        fullscreen form), then wait for the world. Returns {ok, character}."""
        user, pw = self._creds_for(character)
        if not (user and pw):
            return {"ok": False, "err": f"no creds for {character}"}
        self.active_char = character
        self.harvest_log["_off"] = None; self.harvest_log["session"] = {}; self._persist_harvest()
        # SAME sequence as the crafters: boot VM -> desktop -> LaunchPad (update/PLAY) -> close ->
        # game -> form. ONLY the form-typing differs: the agent types it (keybd_event), because
        # AHK Send doesn't land on this VM's fullscreen form. Verified + retried in _agent_type_login.
        drv = LoginDriver(self.g, lambda m: self.log.append(m), form_typer=self._agent_type_login)
        ok = drv.boot_and_login(user, pw, character, "Wuoshi")
        if ok:
            self.start_sensor()
            self.start_hud()                      # on-screen status overlay over EQ2
        self.log.append(("launch: IN WORLD as " if ok else "launch: did NOT reach world as ") + character)
        return {"ok": ok, "character": character}

    def start_hud(self) -> None:
        """(Re)register + start the on-screen status HUD (ibhud) in the interactive session so it
        floats over EQ2. C:\\ib reverts on reboot, so do it every launch. Mirrors ibharv's user +
        python so it runs in session 1 (guest-exec is session 0 and can't draw on the desktop)."""
        reg = (
            "$t=Get-ScheduledTask ibharv -EA SilentlyContinue;"
            "$uid= if($t){$t.Principal.UserId}else{'iksar'};"
            "$exe= if($t){$t.Actions[0].Execute}else{'C:\\ib\\py\\python.exe'};"
            "$a=New-ScheduledTaskAction -Execute $exe -Argument 'C:\\ib\\agent\\hud_overlay.py';"
            "$pr=New-ScheduledTaskPrincipal -UserId $uid -LogonType Interactive -RunLevel Highest;"
            "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1);"
            "Register-ScheduledTask -TaskName ibhud -Action $a -Principal $pr -Settings $s -Force | Out-Null;"
            "Stop-ScheduledTask ibhud -EA SilentlyContinue; Start-ScheduledTask ibhud"
        )
        try:
            self._gx("powershell", ["-NoProfile", "-Command", reg])
            self.log.append("launch: HUD overlay started")
        except Exception as e:
            self.log.append(f"launch: HUD start failed ({e})")

    # --- self-serve harvest (route grid + start/stop), driven from the dashboard ----------
    def list_grids(self) -> list:
        """Zones with a recorded dense grid (graph_<zone>.json) — the harvest route picker."""
        out = []
        for p in sorted(DATA.glob("graph_*.json")):
            try:
                g = NavGraph.load(str(p))
                out.append({"zone": g.zone or p.stem[len("graph_"):].replace("_", " "),
                            "points": len(g), "file": p.name})
            except Exception:
                pass
        return out

    def start_gather(self, zone: str, laps: int) -> dict:
        """Deploy the chosen zone's grid into the guest, then fire the gather loop (self-serve)."""
        p = self._graph_path(zone) if zone else None
        if not (p and p.exists()):
            grids = sorted(DATA.glob("graph_*.json"))
            if not grids:
                return {"ok": False, "err": "no recorded grid yet — record one first"}
            p = grids[0]
        self._push_text(r"C:\ib\graph.json", p.read_text())
        self._fire_agent({"gather_loop": True, "laps": int(laps)})
        self.log.append(f"harvest: START gather on {p.name} ({laps} laps)")
        return {"ok": True, "grid": p.name, "laps": int(laps)}

    def stop_gather(self) -> dict:
        """Halt the gather: STOP flag (agent bails ~instantly) + stop the scheduled task."""
        self._gx("powershell", ["-NoProfile", "-Command",
                 "New-Item C:\\ib\\STOP -ItemType File -Force | Out-Null;"
                 "Stop-ScheduledTask -TaskName ibharv -EA SilentlyContinue"])
        self.log.append("harvest: STOP gather")
        return {"ok": True}

    def deploy_agent(self) -> None:
        """Push the current agent code into the guest. C:\\ib reverts to a baseline on VM reboot,
        so a cold-start launch must redeploy or the login_form/agent code won't exist."""
        src = Path(__file__).parent
        files = {"_agent.py": r"C:\ib\agent\harvest_agent.py",
                 "nav_graph.py": r"C:\ib\agent\nav_graph.py",
                 "sense_push.py": r"C:\ib\agent\sense_push.py",
                 "memory_read.py": r"C:\ib\agent\memory_read.py",
                 "hud_overlay.py": r"C:\ib\agent\hud_overlay.py"}
        for local, remote in files.items():
            p = src / local
            if p.exists():
                self._push_text(remote, p.read_text())
        self.log.append("launch: agent code redeployed")

    def _agent_type_login(self, user, password, character, world):
        """Fill the login form with the agent (keybd_event: Shift+Tab to username, Ctrl+A clear,
        type each field, Enter inline) and submit. Retry on a fresh form if it stays on the form
        (rejected / username didn't take). Called by boot_and_login as the form_typer; boot's
        _await_world does the final in-world check after this returns."""
        self.deploy_agent()                       # C:\ib reverted on boot -> ensure agent code
        drv = LoginDriver(self.g)
        for attempt in range(3):
            self.log.append(f"launch: typing + submitting login (try {attempt + 1})")
            self._fire_agent({"login_form": {"user": user, "password": password,
                                             "character": character, "world": world, "submit": True}})
            time.sleep(20)
            if not drv._login_form_present():     # left the form -> submitted / zoning
                self.log.append("launch: login submitted (left the form)")
                return
            self.log.append("launch: still on form — relaunching EQ2 for a clean retry")
            self._gx("powershell", ["-NoProfile", "-Command",
                                    "Get-Process EverQuest2 -EA SilentlyContinue | Stop-Process -Force"])
            time.sleep(2)
            self.g.run_ahk(LAUNCH_AHK)
            for _ in range(30):
                if drv._login_form_present():
                    break
                time.sleep(3)
        self.log.append("launch: could not get past the login form")

    def camp_desktop(self) -> dict:
        """Log out cleanly to desktop (closes the client)."""
        self._fire_agent({"chat": "/camp desktop"})
        self.log.append("camp: typed /camp desktop")
        return {"ok": True}

    def shutdown_vm(self) -> dict:
        self.log.append("shutdown: powering off VM")
        r = subprocess.run(VIRSH + ["shutdown", DOM], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "msg": (r.stdout + r.stderr).strip()}

    def keymap(self) -> dict:
        """In-game keybind reference (editable at ib-data/harvest/keymap.yaml)."""
        f = DATA / "keymap.yaml"
        if f.exists():
            try:
                return yaml.safe_load(f.read_text()) or {}
            except Exception:
                pass
        km = {"binds": [
            {"key": "Ctrl+0", "action": "target_nearest_npc + /consider",
             "note": "acquire & classify: node = 'not attackable', mob = attackable"},
            {"key": "Ctrl+9", "action": "harvest current target", "note": "the harvest button"},
            {"key": "Tab", "action": "target next-nearest", "note": "built-in; used to skip mobs"},
            {"key": "W / A / S / D", "action": "forward / strafe-left / back / strafe-right", "note": "movement"},
            {"key": "Left / Right", "action": "turn", "note": "heading control"},
            {"key": "Space", "action": "jump", "note": "unstuck ladder"},
        ]}
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            f.write_text(yaml.safe_dump(km, sort_keys=False))   # seed an editable file
        except Exception:
            pass
        return km

    def frame_jpeg(self) -> bytes:
        """Live VM screen for the console preview panel. b'' if not grabbable."""
        ppm = f"/tmp/ibh_{DOM}.ppm"
        r = subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot",
                            DOM, ppm], capture_output=True)
        if r.returncode != 0:
            return b""
        m = subprocess.run(["magick", ppm, "-resize", "960", "-quality", "55", "jpg:-"],
                           capture_output=True)
        return m.stdout or b""


def create_app(h: Harvest) -> FastAPI:
    app = FastAPI(title="ibh")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (WEB / "index.html").read_text()

    @app.get("/api/state")
    async def state():
        hl = h.harvest_log
        now = time.time()
        alerts = []
        if now - hl.get("combat_ts", 0) < 8:
            alerts.append({"kind": "combat", "msg": "in combat"})
        for t in hl["tells"][-3:]:
            if now - t["t"] < 60:
                alerts.append({"kind": "pm", "msg": f'{t["from"]}: {t["msg"]}'})
        for r in hl["rares"][-3:]:
            if now - r["t"] < 120:
                alerts.append({"kind": "rare", "msg": f'RARE: {r["item"]}'})
        st = h.cur_state()
        return {"state": st,
                "recording": ({"name": h.recording["name"], "points": len(h.recording["points"])}
                              if h.recording else None),
                "routes": h.routes.get(st.get("zone") or "", {}),
                "spawns": h.spawns,
                "harvest": {"session": hl["session"], "all_time": hl["all_time"],
                            "rares": hl["rares"][-8:], "tells": hl["tells"][-8:]},
                "alerts": alerts,
                "zone": st.get("zone"), "log": h.log[-30:],
                "src": "push" if (time.time() - h.pushed["ts"] < 3.0) else "poll"}

    @app.post("/api/ingest")
    async def ingest(body: dict = Body(default={})):
        h.pushed = {"st": body, "ts": time.time()}
        if body.get("ok") and body.get("pos"):
            if h.recording is not None:
                h._maybe_record(body["pos"])
            if h.graph is not None:
                h._graph_feed(body["pos"])
        return {"ok": True}

    @app.post("/api/move")
    async def move(body: dict = Body(default={})):
        h.move(body.get("dir", ""), int(body.get("ms", 400))); return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        h.stop_keys(); return {"ok": True}

    @app.post("/api/harvest")
    async def harvest():
        h.harvest_key(); return {"ok": True}

    @app.post("/api/route/record")
    async def record(body: dict = Body(default={})):
        if body.get("action") == "start":
            h.record_start(body.get("name", "")); return {"ok": True, "recording": True}
        return h.record_stop()

    @app.get("/api/routes")
    async def routes(zone: str = ""):
        return h.routes.get(zone or (h.state.get("zone") or ""), {})

    @app.post("/api/graph")
    async def graph(body: dict = Body(default={})):
        a = body.get("action")
        if a == "start":
            return h.graph_start()
        if a == "stop":
            return h.graph_stop()
        return h.graph_status()

    @app.get("/api/grids")
    async def grids():
        return {"grids": h.list_grids()}

    @app.post("/api/gather")
    async def gather(body: dict = Body(default={})):
        # Self-serve harvest: pick a recorded grid + start/stop the gather loop from the UI.
        if body.get("action") == "stop":
            return await asyncio.get_running_loop().run_in_executor(None, h.stop_gather)
        return await asyncio.get_running_loop().run_in_executor(
            None, h.start_gather, body.get("zone", ""), int(body.get("laps", 40)))

    @app.post("/api/launch")
    async def launch(body: dict = Body(default={})):
        # Fire-and-forget: launch takes minutes; run it in the dashboard and return now so the
        # request can't be killed by the client disconnecting. Watch the controller log.
        ch = body.get("character") or h.active_char or "Trailmix"
        asyncio.get_running_loop().run_in_executor(None, h.launch_and_login, ch)
        return {"started": True, "character": ch}

    @app.post("/api/camp")
    async def camp():
        return await asyncio.get_running_loop().run_in_executor(None, h.camp_desktop)

    @app.post("/api/shutdown")
    async def shutdown():
        return await asyncio.get_running_loop().run_in_executor(None, h.shutdown_vm)

    @app.get("/api/keymap")
    async def keymap():
        return h.keymap()

    @app.post("/api/recalibrate")
    async def recal(body: dict = Body(default={})):
        return h.recalibrate(float(body["x"]), float(body["y"]), float(body["z"]))

    @app.get("/api/characters")
    async def characters():
        return [{"character": c.get("character"), "class": c.get("class", ""),
                 "zone": c.get("zone", ""), "user": c.get("user", "")} for c in h.characters()]

    @app.post("/api/char")
    async def char(body: dict = Body(default={})):
        ok = await asyncio.get_running_loop().run_in_executor(None, h.login_char, body["character"])
        return {"ok": ok}

    @app.get("/api/frame.jpg")
    async def frame():
        data = await asyncio.get_running_loop().run_in_executor(None, h.frame_jpeg)
        if not data:
            return Response(status_code=503)
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/spice/ws/{port}")
    async def spice_ws(ws: WebSocket, port: int):
        if port != SPICE_PORT:
            await ws.close(code=1008); return
        await ws.accept(subprotocol="binary")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await ws.close(code=1011); return

        async def w2t():
            with contextlib.suppress(Exception):
                while True:
                    writer.write(await ws.receive_bytes()); await writer.drain()

        async def t2w():
            with contextlib.suppress(Exception):
                while True:
                    d = await reader.read(65536)
                    if not d:
                        break
                    await ws.send_bytes(d)

        t1 = asyncio.create_task(w2t()); t2 = asyncio.create_task(t2w())
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (t1, t2):
                t.cancel()
            writer.close()
            with contextlib.suppress(Exception):
                await ws.close()

    @app.on_event("startup")
    async def _start():
        # start the persistent in-guest sensor (real-time HTTP push) in the background
        asyncio.get_running_loop().run_in_executor(None, h.start_sensor)
        asyncio.create_task(h.poll_loop())         # fallback reader when the push is stale
        asyncio.create_task(h.log_loop())          # harvested items / rares / tells / combat
        # spawns_loop (slow ~6s whole-heap vtable scan) retired: nearby NODES now come
        # instantly from the per-poll reader via the game's harvestable array. Monsters
        # (heap spawn-manager) will get their own fast path once that RE lands.

    if (WEB / "static").exists():
        app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
    return app


def main() -> int:
    ap = argparse.ArgumentParser(prog="ibh", description="ib harvest dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--web-port", type=int, default=18082)
    args = ap.parse_args()
    if setproctitle:
        setproctitle.setproctitle("ibh")
    app = create_app(Harvest())
    uvicorn.run(app, host=args.host, port=args.web_port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
