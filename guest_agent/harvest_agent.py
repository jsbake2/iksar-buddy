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
import json, math, time, struct, os, re
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


def nav(pm, base, hwnd, tx, tz, keys):
    focus_eq2(hwnd)
    t0 = time.time()
    last_focus = 0.0
    while time.time() - t0 < TIMEOUT:
        if time.time() - last_focus > 1.0:        # re-assert focus periodically
            if _u.GetForegroundWindow() != hwnd:
                keys.set(set()); focus_eq2(hwnd)
            last_focus = time.time()
        x, z, h = state(pm, base)
        d = math.hypot(tx - x, tz - z)
        if d < GRACE:
            keys.release_all()
            return True, d
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
    return False, math.hypot(tx - x, tz - z)


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
        if attempt < 2:                        # capture raw log of first tries for debugging
            debug.append(_log_since(off)[-300:].replace("\n", " | "))
        if result and result[0] == "ok":
            succ += 1; node = result[1]
            if succ >= 3:
                return {"node": node, "harvests": succ, "rare": rare, "done": "depleted", "debug": debug}
        elif result and result[0] == "fail":
            continue                          # node still there — try again, don't count
        else:
            return {"node": node, "harvests": succ, "rare": rare,
                    "done": ("gone" if succ == 0 else "depleted"), "debug": debug}
    return {"node": node, "harvests": succ, "rare": rare, "done": "maxtries", "debug": debug}


def main():
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
        ok, d = nav(pm, base, hwnd, tx, tz, keys)
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
