r"""In-guest HARVEST NAV agent — runs in the INTERACTIVE session (scheduled task,
InteractiveToken) so pydirectinput keystrokes reach the game and pymem reads are local.

The fix for jerky/circling movement: CLOSED-LOOP control at ~30 Hz. Holds a key DOWN and
releases the instant a condition is met (general facing, in-range) — no open-loop timing
guesses (the host path's launch latency made the same command move 1m or 6m at random).

Phase 1: nav to a target world (X,Z) from C:\ib\nav_target.json, write C:\ib\nav_status.json.
  - turn (Left/Right arrow) until GENERALLY facing the node (within FACE_TOL)
  - then W forward, with A/D strafe to trim small lateral offset (owner: use strafe)
  - stop within GRACE metres (harvest has a couple-metres grace) and release all keys
Keys released on every exit path so nothing sticks down.
"""
from __future__ import annotations
import json, math, time, struct, os, re, threading
from pathlib import Path

LOG = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
       r"\EverQuest II\logs\Wuoshi\eq2log_Furyflatulence.txt")
HARV = re.compile(r"You (?:mine|forage|gather|fell|trap|acquire|catch|chop|cut) \d+ .*? from the (.+?)\.")
FAIL = re.compile(r"(?:fail(?:ed)? to (?:gather|harvest|mine|forage|trap|acquire|catch|fell|chop)"
                  r"|did not (?:find|gather|harvest))", re.I)   # node STILL there -> retry
RARE = re.compile(r"You have found a rare item")

import ctypes
from ctypes import wintypes

import pymem, pymem.process
import pydirectinput
pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False

_u = ctypes.windll.user32


def _win_pid(h):
    pid = wintypes.DWORD(0)
    _u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def _live_eq2():
    """Return (hwnd, pid, pm, base) for the REAL in-world EverQuest2 — found by attaching
    pymem to the owning process and checking for a valid player position. Robust to empty
    window titles and the crashed/zombie helper procs (pos 0,0,0)."""
    wins = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if _u.IsWindowVisible(h):
            r = wintypes.RECT()
            _u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) > 200 and (r.bottom - r.top) > 150:
                wins.append((h, _win_pid(h)))
        return True
    _u.EnumWindows(cb, 0)
    seen = {}
    for h, pid in wins:
        if pid in seen:
            continue
        try:
            pm = pymem.Pymem(); pm.open_process_from_id(pid)
            base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
            x = pm.read_float(base + POS_OFF); z = pm.read_float(base + POS_OFF + 8)
            ok = abs(x) > 1 and abs(x) < 1e5 and abs(z) < 1e5
            seen[pid] = (ok, pm, base)
            if ok:
                return h, pid, pm, base
        except Exception:
            seen[pid] = (False, None, None)
    return None, None, None, None


def focus_eq2(hwnd):
    """Bring EQ2 foreground so SendInput keystrokes land in it. Uses the AttachThreadInput
    trick to defeat Windows' foreground lock when called from a background task."""
    if not hwnd:
        return False
    # NOTE: do NOT ShowWindow(SW_RESTORE) — it un-maximizes the fullscreen game into a small
    # window (made the display "get ugly" every run). Only raise/focus, never resize.
    fg = _u.GetForegroundWindow()
    t_target = _u.GetWindowThreadProcessId(hwnd, None)
    t_fore = _u.GetWindowThreadProcessId(fg, None)
    if t_fore and t_target and t_fore != t_target:
        _u.AttachThreadInput(t_fore, t_target, True)
        _u.SetForegroundWindow(hwnd)
        _u.BringWindowToTop(hwnd)
        _u.AttachThreadInput(t_fore, t_target, False)
    else:
        _u.SetForegroundWindow(hwnd)
    return _u.GetForegroundWindow() == hwnd

POS_OFF = 0x1822b68
HDG_OFF = 0x1822b74
PROC = "EverQuest2.exe"
TARGET = r"C:\ib\nav_target.json"
STATUS = r"C:\ib\nav_status.json"

GRACE = 2.5          # metres — close enough to harvest
FACE_TOL = 22.0      # degrees — "generally facing"
TURN_BRAKE = 8.0     # release turn slightly early; momentum carries it in
STRAFE_BAND = (6.0, FACE_TOL)   # trim lateral with strafe inside this |diff|
TIMEOUT = 25.0
# Right arrow INCREASES heading (calibrated); to cut a +diff we press Right.
TURN_FOR_POS_DIFF = "right"
TURN_FOR_NEG_DIFF = "left"


def pm_open():
    pm = pymem.Pymem(PROC)
    base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
    return pm, base


def state(pm, base):
    a = base + POS_OFF
    x = pm.read_float(a); z = pm.read_float(a + 8)
    h = pm.read_float(base + HDG_OFF) % 360.0
    return x, z, h


class Keys:
    """Hold-state manager: only press/release on change so keys stay smoothly held."""
    ALL = ("w", "s", "a", "d", "left", "right")

    def __init__(self):
        self.held = set()

    def set(self, want):
        want = set(want)
        for k in want - self.held:
            pydirectinput.keyDown(k)
        for k in self.held - want:
            pydirectinput.keyUp(k)
        self.held = want

    def release_all(self):
        for k in list(self.held):
            pydirectinput.keyUp(k)
        # belt-and-suspenders: release every movement key
        for k in self.ALL:
            try: pydirectinput.keyUp(k)
            except Exception: pass
        self.held = set()


def signed_diff(bearing, h):
    return (bearing - h + 540) % 360 - 180


def nav(pm, base, hwnd, tx, tz, keys, grace=GRACE):
    """Returns (ok, dist, stuck). STUCK = no PROGRESS toward the target over ~2.5s, even if
    position is changing (owner: bouncing up/down a barrier is stuck too) — so we watch the
    distance-to-target trend, not raw movement, and bail instead of grinding a wall."""
    focus_eq2(hwnd)
    t0 = time.time()
    last_focus = 0.0
    hist = []                                    # (t, dist-to-target)
    while time.time() - t0 < TIMEOUT:
        now = time.time()
        if now - last_focus > 1.0:               # re-assert focus periodically
            if _u.GetForegroundWindow() != hwnd:
                keys.set(set()); focus_eq2(hwnd)
            last_focus = now
        x, z, h = state(pm, base)
        d = math.hypot(tx - x, tz - z)
        if d < grace:
            keys.release_all()
            return True, d, False
        # progress-based stuck check: keep ~2.5s of distance history
        hist.append((now, d))
        hist = [(t, dd) for t, dd in hist if now - t < 2.6]
        if hist and now - hist[0][0] >= 2.2 and d >= hist[0][1] - 1.2:
            # <1.2 m of progress in 2.2 s while trying to move => stuck on a barrier
            keys.set({"s"}); time.sleep(0.5)      # back off a touch
            keys.release_all()
            return False, d, True
        bearing = math.degrees(math.atan2(tx - x, tz - z)) % 360
        diff = signed_diff(bearing, h)
        want = set()
        ad = abs(diff)
        if ad > FACE_TOL:
            # turn toward the node; curve forward once the turn is moderate (faster, smoother)
            want.add(TURN_FOR_POS_DIFF if diff > 0 else TURN_FOR_NEG_DIFF)
            if ad < 75:
                want.add("w")
        else:
            # generally facing: drive forward, strafe-trim small lateral offset
            want.add("w")
            if STRAFE_BAND[0] < ad <= STRAFE_BAND[1]:
                want.add("d" if diff > 0 else "a")
        keys.set(want)
        time.sleep(0.03)
    keys.release_all()
    return False, math.hypot(tx - x, tz - z), False    # timed out (not flagged stuck)


def _log_len():
    try: return os.path.getsize(LOG)
    except OSError: return 0


def _log_since(off):
    try:
        with open(LOG, "r", errors="replace") as f:
            f.seek(off)
            return f.read()
    except OSError:
        return ""


def harvest_key():
    # Ctrl+9 in-game macro = auto-target nearest node + harvest. The HOTBAR only accepts
    # Event-mode input (keybd_event), NOT SendInput scancodes (pydirectinput) — same reason
    # AHK had to use SendMode Event for it. VK_CONTROL=0x11, '9'=0x39; flags 0=down,2=up.
    KEYUP = 0x02
    _u.keybd_event(0x11, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x39, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x39, 0, KEYUP, 0); time.sleep(0.05)
    _u.keybd_event(0x11, 0, KEYUP, 0)


def harvest(hwnd):
    """Deplete the node: 3 successful pulls (bountiful = ONE), 'failed to gather' = node
    still there so RETRY, no line at all within a few sec = node gone -> done."""
    focus_eq2(hwnd)
    succ = 0; rare = False; node = None; debug = []
    for attempt in range(10):
        off = _log_len()
        harvest_key()
        result = None
        t = time.time()
        while time.time() - t < 3.5:          # await this pull's outcome line
            time.sleep(0.35)
            new = _log_since(off)
            if RARE.search(new): rare = True
            m = HARV.search(new)
            if m: result = ("ok", m.group(1)); break
            if FAIL.search(new): result = ("fail", None); break
            if FAR.search(new): result = ("toofar", None); break
        if attempt < 2:                        # capture raw log of first tries for debugging
            debug.append(_log_since(off)[-300:].replace("\n", " | "))
        if result and result[0] == "ok":
            succ += 1; node = result[1]
            if succ >= 3:
                return {"node": node, "harvests": succ, "rare": rare, "done": "depleted", "debug": debug}
        elif result and result[0] == "fail":
            continue                          # node still there — try again, don't count
        elif result and result[0] == "toofar":
            return {"node": node, "harvests": succ, "rare": rare, "done": "toofar", "debug": debug}
        else:
            return {"node": node, "harvests": succ, "rare": rare,
                    "done": ("gone" if succ == 0 else "depleted"), "debug": debug}
    return {"node": node, "harvests": succ, "rare": rare, "done": "maxtries", "debug": debug}


import ctypes.wintypes as _wt


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", _wt.DWORD), ("__a1", _wt.DWORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", _wt.DWORD),
                ("Protect", _wt.DWORD), ("Type", _wt.DWORD), ("__a2", _wt.DWORD)]


NODE_CLASSES = [(0x14eb850, 0x60), (0x14a3238, 0x40), (0x14a32d8, 0x40),
                (0x1493c58, 0x40), (0x149b2f8, 0x40)]


def scan_nodes(pm, base, px, pz, radius=160.0):
    """Inline node-candidate scan (union of harvest-node vtable classes). Runs while the bot
    is stationary between nodes, so it never makes movement jerky."""
    VQ = ctypes.windll.kernel32.VirtualQueryEx
    VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    pats = {struct.pack("<Q", base + vt): po for vt, po in NODE_CLASSES}
    out = []
    seen = set()
    addr = 0
    mbi = _MBI()
    while addr < 0x7fffffffffff:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        sz = mbi.RegionSize
        if mbi.State == 0x1000 and (mbi.Protect & 0xff) == 0x04 and 0 < sz <= 256 * 1024 * 1024:
            try:
                buf = pm.read_bytes(mbi.BaseAddress, sz)
            except Exception:
                buf = b""
            for patt, po in pats.items():
                i = buf.find(patt)
                while i != -1:
                    oa = mbi.BaseAddress + i
                    if oa not in seen and i + po + 12 <= len(buf):
                        seen.add(oa)
                        x, y, z = struct.unpack_from("<fff", buf, i + po)
                        if (math.isfinite(x) and math.isfinite(z) and abs(x) > 5 and abs(z) > 5
                                and abs(x) < 1e5 and abs(z) < 1e5 and abs(y) < 1e4):
                            d = math.hypot(x - px, z - pz)
                            if d < radius:
                                out.append((round(x, 1), round(z, 1), round(d, 1)))
                    i = buf.find(patt, i + 1)
        addr = mbi.BaseAddress + sz if sz else addr + 0x1000
    out.sort(key=lambda n: n[2])
    return out


def loop_main(keys, max_nodes):
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        Path(STATUS).write_text(json.dumps({"ok": False, "err": "live EQ2 not found"}))
        return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    visited = set()
    progress = {"loop": True, "nodes_done": [], "harvests_total": 0}
    for it in range(max_nodes):
        x, z, _ = state(pm, base)
        cands = scan_nodes(pm, base, x, z)
        tgt = None
        for cx, cz, cd in cands:
            if cd < 3.5:
                continue
            if (round(cx / 3), round(cz / 3)) in visited:
                continue
            tgt = (cx, cz); break
        if not tgt:
            progress["stop"] = "no fresh node candidates"; break
        progress["going_to"] = [tgt[0], tgt[1], it + 1]
        Path(STATUS).write_text(json.dumps(progress))
        ok, d, _ = nav(pm, base, hwnd, tgt[0], tgt[1], keys)
        keys.release_all()
        hv = harvest(hwnd) if ok else {"harvests": 0, "done": "nav_fail"}
        visited.add((round(tgt[0] / 3), round(tgt[1] / 3)))
        progress["nodes_done"].append({"xz": tgt, "nav_dist": round(d, 1),
                                       "harvests": hv.get("harvests", 0),
                                       "node": hv.get("node"), "rare": hv.get("rare"),
                                       "result": hv.get("done")})
        progress["harvests_total"] += hv.get("harvests", 0)
        Path(STATUS).write_text(json.dumps(progress))
    progress["finished"] = True
    Path(STATUS).write_text(json.dumps(progress))


ROUTE = r"C:\ib\route.json"
_scan_cache = {"nodes": [], "ts": 0}
_scan_lock = threading.Lock()
_scan_stop = False


def _scan_thread():
    """Background node scanner (the owner's 'scan must be a thread' requirement). Own pymem
    handle; scans continuously so a waypoint read is INSTANT — never waits on the sweep."""
    try:
        pm = pymem.Pymem(PROC)
        base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
    except Exception:
        return
    while not _scan_stop:
        try:
            px = pm.read_float(base + POS_OFF); pz = pm.read_float(base + POS_OFF + 8)
            if abs(px) < 1:                 # attached to a zombie; re-open
                pm = pymem.Pymem(PROC)
                base = pymem.process.module_from_name(pm.process_handle, PROC).lpBaseOfDll
                continue
            nodes = scan_nodes(pm, base, px, pz, radius=60.0)
            with _scan_lock:
                _scan_cache["nodes"] = nodes
                _scan_cache["ts"] = time.time()
        except Exception:
            time.sleep(0.5)


def route_loop_main(keys, laps):
    global _scan_stop
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        Path(STATUS).write_text(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    route = json.loads(Path(ROUTE).read_text())
    wps = route["waypoints"]
    threading.Thread(target=_scan_thread, daemon=True).start()
    prog = {"route": route.get("name"), "lap": 0, "wp": 0, "harvests_total": 0, "events": []}
    try:
        for lap in range(laps):
            prog["lap"] = lap + 1
            for wi, wp in enumerate(wps):
                prog["wp"] = wi + 1
                Path(STATUS).write_text(json.dumps(prog))
                nav(pm, base, hwnd, wp[0], wp[1], keys); keys.release_all()
                # QUICK node sweep around this waypoint from the background cache (instant)
                with _scan_lock:
                    cands = list(_scan_cache["nodes"])
                wx, wz, _ = state(pm, base)
                near = [(cx, cz) for (cx, cz, _) in cands
                        if math.hypot(cx - wx, cz - wz) < 25][:8]
                tried = set()
                for cx, cz in near:
                    key = (round(cx / 2), round(cz / 2))
                    if key in tried:
                        continue
                    tried.add(key)
                    nav(pm, base, hwnd, cx, cz, keys); keys.release_all()
                    hv = harvest(hwnd)
                    if hv.get("harvests"):
                        prog["harvests_total"] += hv["harvests"]
                        prog["events"].append({"lap": lap + 1, "wp": wi + 1,
                                               "node": hv.get("node"), "n": hv["harvests"],
                                               "rare": hv.get("rare")})
                        Path(STATUS).write_text(json.dumps(prog))
        prog["finished"] = True
        Path(STATUS).write_text(json.dumps(prog))
    finally:
        _scan_stop = True
        keys.release_all()


FAR = re.compile(r"too far away", re.I)         # gather locked a node but out of range
# The game's live "nearby harvestables" array (module-static). Pointers to harvest-node
# objects (vtable in the 0x149x-0x14ex family); world position at obj+0x60. Found via the
# target-diff: this list is what the gather skill walks, so it's REAL nodes only.
NODE_LO = 0x177bf00
NODE_HI = 0x177c100


def read_node_array(pm, base):
    """Read the game's live nearby-harvestable array. SANITY-FILTERED: the array carries
    stale/garbage entries (e.g. y=-89 underground, or 300m+ away) that would fling the bot
    off-path. Keep only nodes at the player's rough elevation and within a sane radius."""
    nodes = []
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    try:
        data = pm.read_bytes(base + NODE_LO, NODE_HI - NODE_LO)
    except Exception:
        return nodes
    for o in range(0, len(data) - 8, 8):
        ptr = struct.unpack_from("<Q", data, o)[0]
        if not (0x10000000000 < ptr < 0x7ff000000000):
            continue
        try:
            vt = struct.unpack("<Q", pm.read_bytes(ptr, 8))[0]
        except Exception:
            continue
        voff = vt - base
        if not (0x1490000 <= voff <= 0x14f0000):
            continue
        try:
            x, y, z = struct.unpack("<fff", pm.read_bytes(ptr + 0x60, 12))
        except Exception:
            continue
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        # SANITY: near the player's elevation (drops underground/garbage) + within a sane range
        if abs(y - py) > 40:
            continue
        d = math.hypot(x - px, z - pz)
        if d > 220:                          # array holds zone-wide/stale entries; ignore far ones
            continue
        nodes.append((round(x, 1), round(z, 1)))
    return nodes


def gather_loop_main(keys, laps):
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        Path(STATUS).write_text(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    route = json.loads(Path(ROUTE).read_text())
    wps = route["waypoints"]
    prog = {"mode": "gather_loop", "lap": 0, "wp": 0, "harvests_total": 0, "events": []}
    try:
        for lap in range(laps):
            prog["lap"] = lap + 1
            for wi, wp in enumerate(wps):
                prog["wp"] = wi + 1
                Path(STATUS).write_text(json.dumps(prog))
                nav(pm, base, hwnd, wp[0], wp[1], keys); keys.release_all()
                done = set()
                for _ in range(20):                 # clear all nodes around this waypoint
                    x, z, _h = state(pm, base)
                    nodes = read_node_array(pm, base)
                    near = sorted((n for n in nodes
                                   if math.hypot(n[0] - x, n[1] - z) < 45
                                   and (round(n[0] / 3), round(n[1] / 3)) not in done),
                                  key=lambda n: math.hypot(n[0] - x, n[1] - z))
                    if not near:
                        break
                    tx, tz = near[0]
                    ok, _, stuck = nav(pm, base, hwnd, tx, tz, keys, grace=2.0); keys.release_all()
                    if stuck:                             # can't reach (wall/cliff) — skip it
                        done.add((round(tx / 3), round(tz / 3)))
                        continue
                    hv = harvest(hwnd)
                    if hv.get("done") == "toofar":        # close in the last bit and retry
                        nav(pm, base, hwnd, tx, tz, keys, grace=1.0); keys.release_all()
                        hv = harvest(hwnd)
                    done.add((round(tx / 3), round(tz / 3)))
                    if hv.get("harvests"):
                        prog["harvests_total"] += hv["harvests"]
                        prog["events"].append({"node": hv.get("node"), "n": hv["harvests"],
                                               "rare": hv.get("rare"), "at": [tx, tz]})
                        Path(STATUS).write_text(json.dumps(prog))
        prog["finished"] = True
        Path(STATUS).write_text(json.dumps(prog))
    finally:
        keys.release_all()


def main():
    keys = Keys()
    try:
        tgt = json.loads(Path(TARGET).read_text())
        if tgt.get("gather_loop"):
            gather_loop_main(keys, int(tgt.get("laps", 1)))
            return
        if tgt.get("route_loop"):
            route_loop_main(keys, int(tgt.get("laps", 1)))
            return
        if tgt.get("loop"):
            loop_main(keys, int(tgt.get("max_nodes", 5)))
            return
    except Exception:
        pass
    keys = Keys()
    try:
        tgt = json.loads(Path(TARGET).read_text())
        tx, tz = float(tgt["tx"]), float(tgt["tz"])
        do_harvest = bool(tgt.get("harvest", True))
        hwnd, pid, pm, base = _live_eq2()
        if not hwnd:
            Path(STATUS).write_text(json.dumps({"ok": False, "err": "live EQ2 window not found"}))
            return
        _u.ShowWindow(hwnd, 3)          # SW_MAXIMIZE — we ARE in session 1 here, so this works
        _u.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        ok, d, _ = nav(pm, base, hwnd, tx, tz, keys)
        keys.release_all()
        out = {"ok": ok, "dist": round(d, 2), "pid": pid, "ts": time.time()}
        if ok and do_harvest:
            out["harvest"] = harvest(hwnd)
        Path(STATUS).write_text(json.dumps(out))
    except Exception as e:
        keys.release_all()
        import traceback
        Path(STATUS).write_text(json.dumps({"ok": False, "err": str(e),
                                            "tb": traceback.format_exc()[-400:], "ts": time.time()}))
    finally:
        keys.release_all()


if __name__ == "__main__":
    main()
