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

DOM = "iksar_buddy"                                 # the GPU VM
SPICE_PORT = 5900                                  # iksar_buddy SPICE (same as the healer)
GUEST_PY = r"C:\ib\py\python.exe"
GUEST_READER = r"C:\ib\agent\harvest_read.py"
GUEST_SPAWNS = r"C:\ib\agent\spawns_live.py"       # nearby-entity scanner (vtable RE)
GUEST_PUSH = r"C:\ib\agent\sense_push.py"          # persistent sensor (HTTP push, real-time)
DATA = Path(os.environ.get("IB_DATA_DIR", str(Path.home() / "ib-data"))) / "harvest"
DATA.mkdir(parents=True, exist_ok=True)
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
        if h.recording is not None and body.get("ok") and body.get("pos"):
            h._maybe_record(body["pos"])
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
