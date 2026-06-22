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
import json, math, time, struct
from pathlib import Path

import ctypes
from ctypes import wintypes

import pymem, pymem.process
import pydirectinput
pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False

_u = ctypes.windll.user32


def _find_eq2():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        n = _u.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            _u.GetWindowTextW(h, buf, n + 1)
            if "EverQuest" in buf.value and _u.IsWindowVisible(h):
                found.append(h)
        return True
    _u.EnumWindows(cb, 0)
    return found[0] if found else None


def focus_eq2(hwnd):
    """Bring EQ2 foreground so SendInput keystrokes land in it. Uses the AttachThreadInput
    trick to defeat Windows' foreground lock when called from a background task."""
    if not hwnd:
        return False
    _u.ShowWindow(hwnd, 9)            # SW_RESTORE
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


def nav(pm, base, tx, tz, keys):
    hwnd = _find_eq2()
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


def main():
    keys = Keys()
    try:
        tgt = json.loads(Path(TARGET).read_text())
        tx, tz = float(tgt["tx"]), float(tgt["tz"])
        pm, base = pm_open()
        ok, d = nav(pm, base, tx, tz, keys)
        Path(STATUS).write_text(json.dumps({"ok": ok, "dist": round(d, 2), "ts": time.time()}))
    except Exception as e:
        keys.release_all()
        Path(STATUS).write_text(json.dumps({"ok": False, "err": str(e), "ts": time.time()}))
    finally:
        keys.release_all()


if __name__ == "__main__":
    main()
