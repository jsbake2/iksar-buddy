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
try:
    import nav_graph                       # dense waypoint graph + A* (deployed alongside us)
except Exception:
    nav_graph = None

import glob as _glob
_LOGDIR = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
           r"\EverQuest II\logs\Wuoshi")


def _freshest_log():
    """The active character's log = the most-recently-written eq2log_*.txt. Auto-adapts to
    whoever's logged in (Trailmix, Furyflatulence, ...) instead of a hardcoded name."""
    fs = _glob.glob(os.path.join(_LOGDIR, "eq2log_*.txt"))
    return max(fs, key=os.path.getmtime) if fs else os.path.join(_LOGDIR, "eq2log_Furyflatulence.txt")


LOG = _freshest_log()
HARV = re.compile(r"You (?:mine|forage|gather|fell|trap|acquire|catch|chop|cut) \d+ .*? from the (.+?)\.")
FAIL = re.compile(r"(?:fail(?:ed)? to (?:gather|harvest|mine|forage|trap|acquire|catch|fell|chop)"
                  r"|did not (?:find|gather|harvest))", re.I)   # node STILL there -> retry
RARE = re.compile(r"You have found a rare item")
# Ctrl+0 = /consider. Only ATTACKABLE creatures (mobs) con; harvest nodes do not. Gives the
# mob name too. Used as a gate so the bot never wastes pulls on a badger. (owner: testing aid)
CONSIDER = re.compile(r"You consider (?:an? |the )?(.+?)\s*\.\.\.", re.I)
NOTARGET = re.compile(r"no eligible target", re.I)         # gather found nothing harvestable
NOT_ATTACKABLE = re.compile(r"not attackable", re.I)       # /consider says it's a node, not a mob
# damage TO the player = we're under attack -> flee, never stand and die
RE_DMG = re.compile(r"(?:hits YOU|YOU take \d+|tries to \w+ YOU|\bMaul\b.*YOU|"
                    r"crush(?:es)? YOU|slash(?:es)? YOU|pierc(?:es)? YOU|burn(?:s)? YOU"
                    r"|has killed you)", re.I)

# --- combat watcher (background): sets a flag from the log so the act loop can flee fast ---
_combat = {"hit": False, "ts": 0.0}
_combat_stop = False


def _combat_watch():
    off = _log_len()
    while not _combat_stop:
        try:
            new = _log_since(off); off = _log_len()
            if RE_DMG.search(new):
                _combat["hit"] = True; _combat["ts"] = time.time()
        except Exception:
            pass
        time.sleep(0.5)

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


def _eq2_pids():
    """All PIDs named EverQuest2.exe via a Toolhelp snapshot (position-independent)."""
    TH32CS_SNAPPROCESS = 0x2

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]
    k = ctypes.windll.kernel32
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pids = []
    e = PE32(); e.dwSize = ctypes.sizeof(PE32)
    if k.Process32First(snap, ctypes.byref(e)):
        while True:
            if e.szExeFile.decode("latin-1", "ignore").lower() == PROC.lower():
                pids.append(e.th32ProcessID)
            if not k.Process32Next(snap, ctypes.byref(e)):
                break
    k.CloseHandle(snap)
    return set(pids)


def _eq2_window_any():
    """Largest visible window owned by an EverQuest2.exe process — no position needed (works
    even at the login/zoning screen, unlike _live_eq2). For firing chat commands like /loc."""
    eq2 = _eq2_pids()
    wins = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if _u.IsWindowVisible(h):
            r = wintypes.RECT(); _u.GetWindowRect(h, ctypes.byref(r))
            w, hh = r.right - r.left, r.bottom - r.top
            if w > 200 and hh > 150 and _win_pid(h) in eq2:
                wins.append((w * hh, h))
        return True
    _u.EnumWindows(cb, 0)
    wins.sort(reverse=True)
    return wins[0][1] if wins else None


def _tap(vk, shift=False):
    KEYUP = 0x02
    if shift:
        _u.keybd_event(0x10, 0, 0, 0)            # VK_SHIFT down
    _u.keybd_event(vk, 0, 0, 0); time.sleep(0.02)
    _u.keybd_event(vk, 0, KEYUP, 0); time.sleep(0.02)
    if shift:
        _u.keybd_event(0x10, 0, KEYUP, 0)


def _click(x, y):
    """Left-click at a screen pixel (Event-mode mouse, like keybd_event — lands on the form)."""
    _u.SetCursorPos(int(x), int(y)); time.sleep(0.12)
    _u.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.06)     # LEFTDOWN
    _u.mouse_event(0x0004, 0, 0, 0, 0)                        # LEFTUP


def _type_text(s):
    for ch in str(s):
        res = _u.VkKeyScanW(ord(ch))
        if res != -1:
            _tap(res & 0xFF, bool((res >> 8) & 1)); time.sleep(0.04)


def _combo(mods, vk):
    """Tap vk while holding modifier VKs (e.g. Ctrl+A = _combo([0x11], 0x41))."""
    KEYUP = 0x02
    for m in mods:
        _u.keybd_event(m, 0, 0, 0)
    time.sleep(0.03)
    _u.keybd_event(vk, 0, 0, 0); time.sleep(0.03); _u.keybd_event(vk, 0, KEYUP, 0); time.sleep(0.03)
    for m in reversed(mods):
        _u.keybd_event(m, 0, KEYUP, 0)


def _clear_field():
    # Owner's method: Ctrl+A select-all, then Delete. Light BackSpace fallback in case the field
    # didn't take the select-all (different fields behaved differently in testing).
    _combo([0x11], 0x41); time.sleep(0.08)       # Ctrl+A
    _tap(0x2E); time.sleep(0.05)                  # Delete
    _tap(0x23); time.sleep(0.03)                  # End
    for _ in range(16):
        _tap(0x08)                               # BackSpace (clears any residue)


def type_login_form(hwnd, user, password, character, world, user_click=None, submit=True):
    """Fill the EQ2 game login form via keybd_event (AHK Send doesn't land on the fullscreen
    form). CLICK the username field first (resets focus to a known field every attempt — Tab
    navigation from an unknown state was leaving the username unchanged), then Tab forward,
    clearing each field with Ctrl+A+Delete before typing. Long settle avoids dropped keystrokes."""
    focus_eq2(hwnd); time.sleep(0.9)
    if user_click:
        _click(user_click[0], user_click[1]); time.sleep(0.5)    # focus USERNAME directly
    else:
        _tap(0x09, shift=True); time.sleep(0.5)                  # fallback: Shift+Tab to username
    _clear_field(); time.sleep(0.2); _type_text(user); time.sleep(0.3)
    _tap(0x09); time.sleep(0.4)                  # Tab -> password
    _clear_field(); time.sleep(0.2); _type_text(password); time.sleep(0.3)
    _tap(0x09); time.sleep(0.4)                  # Tab -> character
    _clear_field(); time.sleep(0.2); _type_text(character); time.sleep(0.3)
    _tap(0x09); time.sleep(0.4)                  # Tab -> world
    _clear_field(); time.sleep(0.2); _type_text(world); time.sleep(0.3)
    if submit:
        _tap(0x0D)                               # Enter -> submit


def type_chat(hwnd, text):
    """Deliberately type a slash command into EQ2 chat: focus the game, Enter to open the chat
    input, type the text (VkKeyScan maps each char to VK + shift state), Enter to send. Uses
    Event-mode keybd_event — the only input the EQ2 UI/chat widgets accept."""
    focus_eq2(hwnd); time.sleep(0.4)
    _tap(0x0D); time.sleep(0.6)                   # Enter -> open chat input
    for ch in text:
        res = _u.VkKeyScanW(ord(ch))
        if res == -1:
            continue
        _tap(res & 0xFF, bool((res >> 8) & 1)); time.sleep(0.03)
    time.sleep(0.3)
    _tap(0x0D)                                    # Enter -> send


POS_OFF = 0x1822b78          # recalibrated 2026-06-23 (was 0x1822b68; player struct shifted +0x10
HDG_OFF = 0x1822b84          # after a client update). HDG = POS+0xC as before. ZONE ptr unchanged.
PROC = "EverQuest2.exe"
TARGET = r"C:\ib\nav_target.json"
STATUS = r"C:\ib\nav_status.json"
HUD = r"C:\ib\hud.json"            # clean, uncontended status mirror for the on-screen overlay
STOP_FLAG = r"C:\ib\STOP"          # touch this file to halt the bot near-instantly
GRAPH_FILE = r"C:\ib\graph.json"   # dense recorded waypoint graph (OgreNav-style wall avoidance)
ROAM = 28.0                        # max off-path deviation: leave the graph to grab a node up to
                                   # this far from the nearest graph point (tighter -> stays on
                                   # the MAPPED mesh, never beelines into unmapped areas/walls)


class StopRequested(Exception):
    pass


def _check_stop():
    if os.path.exists(STOP_FLAG):
        raise StopRequested()


_DBG = r"C:\ib\gdbg.log"


def _dbg(m):
    try:
        with open(_DBG, "a") as f:
            f.write(f"{time.time():.1f} {m}\n")
    except Exception:
        pass


def _status(s):
    """Write nav_status atomically and NEVER raise. The status file is for the dashboard UI only —
    if a reader (dashboard/sensor) momentarily holds it open (sharing violation -> PermissionError),
    that must NOT crash the gather. Best-effort: write a temp then atomic-replace, swallow errors."""
    try:
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            f.write(s)
        os.replace(tmp, STATUS)
    except Exception:
        pass
    try:                                  # HUD mirror: only the overlay reads this, so no lock
        tmp = HUD + ".tmp"
        with open(tmp, "w") as f:
            f.write(s)
        os.replace(tmp, HUD)
    except Exception:
        pass

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


def _settle(pm, base, keys, timeout=3.0):
    """Release ALL keys and wait until the character is FULLY stopped before harvesting — EQ2
    will not harvest while you're moving, and nav leaves momentum/drift. (owner: stop completely
    then harvest.) Returns once two reads in a row show < 0.05 m of movement."""
    keys.release_all()
    last = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        x, z, _h = state(pm, base)
        if last is not None and math.hypot(x - last[0], z - last[1]) < 0.05:
            break
        last = (x, z)
    keys.release_all()
    time.sleep(0.2)                              # tiny extra beat for the client to register idle


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
    last_hb = time.time()
    hist = []                                    # (t, dist-to-target)
    while time.time() - t0 < TIMEOUT:
        _check_stop()                            # halt mid-nav if the STOP flag appears
        now = time.time()
        if now - last_focus > 1.0:               # re-assert focus periodically
            if _u.GetForegroundWindow() != hwnd:
                keys.set(set()); focus_eq2(hwnd)
            last_focus = now
        x, z, h = state(pm, base)
        d = math.hypot(tx - x, tz - z)
        if now - last_hb > 6.0:                   # travel heartbeat: keeps the log alive on long
            _dbg(f"  ..travel {x:.0f},{z:.0f} -> {tx:.0f},{tz:.0f} d={d:.0f}")   # trips so the
            last_hb = now                         # watchdog doesn't read a long goto as a stall
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
        diff = signed_diff(bearing, h)               # -180..180, + = target is to our right
        ad = abs(diff)
        dr = math.radians(diff)
        want = set()
        # SIMPLE: point heading at the node, then walk straight in (owner's model). No strafe.
        if ad > 20:
            # not pointed at it -> TURN IN PLACE until facing (clean pivot, no forward = no arc)
            want.add(TURN_FOR_POS_DIFF if diff > 0 else TURN_FOR_NEG_DIFF)
        else:
            # facing it -> drive straight forward; small turn nudge only if heading drifts
            want.add("w")
            if ad > 7:
                want.add(TURN_FOR_POS_DIFF if diff > 0 else TURN_FOR_NEG_DIFF)
        keys.set(want)
        time.sleep(0.03)
    keys.release_all()
    return False, math.hypot(tx - x, tz - z), False    # timed out (not flagged stuck)


def _jump():
    try:
        pydirectinput.keyDown("space"); time.sleep(0.08); pydirectinput.keyUp("space")
    except Exception:
        pass


def load_graph():
    """Load the dense recorded waypoint graph, or None if not recorded yet / too small."""
    if nav_graph is None:
        return None
    try:
        g = nav_graph.Graph.load(GRAPH_FILE)
        return g if len(g) >= 2 else None
    except Exception:
        return None


def reachable(graph, tx, tz):
    """Nodes live OFF the path. Only skip ones absurdly far from the walked loop (> ROAM from the
    nearest graph point) — those are almost certainly across a wall. Everything else we go grab."""
    if graph is None:
        return True                                    # no graph yet -> don't filter
    _, d = graph.nearest(tx, tz)
    return d <= ROAM


def _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace):
    """nav() to (tx,tz); if it jams on a barrier, run the unstuck ladder (jump + back off) and
    retry ONCE. Returns (ok, dist_left, stuck)."""
    ok, d, stuck = nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    if stuck:
        keys.release_all(); _jump()
        keys.set({"s"}); time.sleep(0.4); keys.release_all()
        ok, d, stuck = nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    return ok, d, stuck


def goto(pm, base, hwnd, keys, tx, tz, graph, grace=GRACE):
    """Travel to (tx,tz) AROUND walls: graph-route to the nearest graph point to the target, then
    LEAVE the path and straight-hop the rest of the way to the node (nodes are off-path). The next
    goto re-enters the path automatically (it routes from wherever we end up). Falls back to
    straight nav with no graph. Returns (ok, dist_left, stuck)."""
    if graph is None:
        return nav(pm, base, hwnd, tx, tz, keys, grace=grace)
    x, z, _ = state(pm, base)
    for wx, wz in graph.route(x, z, tx, tz):           # 1) follow the path around walls
        _check_stop()
        cx, cz, _ = state(pm, base)
        if math.hypot(wx - cx, wz - cz) < 2.5:
            continue                                   # already at/past this graph point
        ok, d, stuck = _nav_unstuck(pm, base, hwnd, keys, wx, wz, grace=2.2)
        if stuck:
            keys.release_all()
            return False, math.hypot(tx - cx, tz - cz), True
    keys.release_all()
    return _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace)   # 2) off-path hop to the exact node


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


def target_key():
    # Ctrl+0 = owner macro: TARGET nearest harvestable + /consider, in one press. A node cons
    # as 'not attackable'; a creature cons as attackable. This is our acquire+classify step —
    # harvest_key (Ctrl+9) then works the CURRENT target, so we never re-target off the node.
    KEYUP = 0x02
    _u.keybd_event(0x11, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x30, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x30, 0, KEYUP, 0); time.sleep(0.05)
    _u.keybd_event(0x11, 0, KEYUP, 0)


def tab_key():
    # TAB cycles the current target to the NEXT-nearest thing. When the gather macro grabbed a
    # creature, one Tab can shift the target onto the node sitting right next to it. VK_TAB=0x09.
    KEYUP = 0x02
    _u.keybd_event(0x09, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x09, 0, KEYUP, 0)


def _wait_harvest(off, window=5.0):
    """Watch the log after a HARVEST press. Returns (status, name, rare):
    ok=harvested (node), fail=failed-but-still-there, toofar=out of range, notarget=nothing
    harvestable on the current target (creature/empty), none=no line within the window."""
    rare = False; t = time.time()
    while time.time() - t < window:
        time.sleep(0.25)
        new = _log_since(off)
        if RARE.search(new): rare = True
        m = HARV.search(new)
        if m: return ("ok", m.group(1), rare)
        if FAIL.search(new): return ("fail", None, rare)
        if FAR.search(new): return ("toofar", None, rare)
        if NOTARGET.search(new): return ("notarget", None, rare)
    return ("none", None, rare)


def harvest(hwnd):
    """Acquire a NODE as the current target, then deplete it on the HELD target so we never
    re-target and lose it. EQ2 has no 'target nearest harvestable' — only target-nearest-
    non-player, which also grabs creatures. So:
      1. Ctrl+0 (target nearest non-player + /consider). 'not attackable' => node (locked);
         attackable => a creature, step off it with Tab.
      2. If not yet on a node, Tab through the nearby non-players, probing each with a HARVEST
         press — a node harvests, a creature/empty target does nothing — until one harvests.
      3. HARVEST the held node to depletion (3 pulls / bountiful = done). No re-targeting.
    """
    focus_eq2(hwnd)
    succ = 0; rare = False; node = None; debug = []
    have_node = False

    # ---- acquire: make a NODE the current target ----
    _check_stop()
    coff = _log_len(); target_key(); time.sleep(0.9); cnew = _log_since(coff)
    debug.append("T:" + cnew[-200:].replace("\n", " | "))
    cm = CONSIDER.search(cnew)
    if NOTARGET.search(cnew) and not cm:
        return {"node": None, "harvests": 0, "rare": False, "done": "gone", "debug": debug}
    if cm and not NOT_ATTACKABLE.search(cnew):
        tab_key(); time.sleep(0.4)                 # nearest non-player is a creature -> step past it
    elif cm:
        node = cm.group(1).strip(); have_node = True   # /consider says node ('not attackable')

    # ---- probe the non-player ring with harvest presses until one is a node ----
    # A mob/empty target gives "too far"/"no eligible" -> Tab PAST it; only a real node harvests.
    # (Don't bail on the first "too far" — that was a mob sitting on the node blocking everything.)
    if not have_node:
        for _ in range(7):                        # Tab CAN reach the node (owner SME) — ring through
            _check_stop()                          # more targets; a mob/empty press does nothing, the
            off = _log_len(); harvest_key(); res = _wait_harvest(off)   # node press harvests.
            rare = rare or res[2]
            if res[0] == "ok":
                succ += 1; node = res[1]; have_node = True; break
            tab_key(); time.sleep(0.3)             # not harvestable here -> next non-player
        if not have_node:
            return {"node": node, "harvests": succ, "rare": rare,
                    "done": ("mob_blocked" if succ == 0 else "depleted"), "debug": debug}

    # ---- deplete: harvest the HELD node target (3 pulls, bountiful counts as one) ----
    for _ in range(12):
        if succ >= 3:
            break
        _check_stop()
        off = _log_len(); harvest_key(); res = _wait_harvest(off)
        if res[2]: rare = True
        if res[0] == "ok":
            succ += 1; node = res[1]
        elif res[0] == "fail":
            continue                               # still there -> harvest again, same target
        elif res[0] == "toofar":
            return {"node": node, "harvests": succ, "rare": rare, "done": "toofar", "debug": debug}
        else:
            break                                  # target gone -> depleted
    return {"node": node, "harvests": succ, "rare": rare,
            "done": ("depleted" if succ else "gone"), "debug": debug}


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
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"}))
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
        _status(json.dumps(progress))
        ok, d, _ = nav(pm, base, hwnd, tgt[0], tgt[1], keys)
        keys.release_all()
        hv = harvest(hwnd) if ok else {"harvests": 0, "done": "nav_fail"}
        visited.add((round(tgt[0] / 3), round(tgt[1] / 3)))
        progress["nodes_done"].append({"xz": tgt, "nav_dist": round(d, 1),
                                       "harvests": hv.get("harvests", 0),
                                       "node": hv.get("node"), "rare": hv.get("rare"),
                                       "result": hv.get("done")})
        progress["harvests_total"] += hv.get("harvests", 0)
        _status(json.dumps(progress))
    progress["finished"] = True
    _status(json.dumps(progress))


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
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
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
                _status(json.dumps(prog))
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
                        _status(json.dumps(prog))
        prog["finished"] = True
        _status(json.dumps(prog))
    finally:
        _scan_stop = True
        keys.release_all()


FAR = re.compile(r"too far away", re.I)         # gather locked a node but out of range
# The game's live "nearby harvestables" array (module-static). Pointers to harvest-node
# objects (vtable in the 0x149x-0x14ex family); world position at obj+0x60. Found via the
# target-diff: this list is what the gather skill walks, so it's REAL nodes only.
NODE_LO = 0x177bf00
NODE_HI = 0x177c100


# Harvest nodes are render objects with these EXACT vtables — confirmed by standing on each type
# (2026-06-24): wood (felled high plains arbor), ore (wind swept stones) AND bush (high plains
# shrubbery) ALL share 0x14eb830, with a co-located sibling 0x14eba10. World position is at
# obj+0x60 (NOT +0x1a0 — that offset hid the bush nodes). Mobs/NPCs are DIFFERENT vtables, so an
# exact-vtable match needs no actor/monster filter at all — that filter was deleting real nodes.
NODE_VTS = {0x14eb830, 0x14eba10}
NODE_POS = 0x60
ACTOR_VT = 0x1782848             # monsters/NPCs/players actor vtable (pos @ +0x1f0)
ACTOR_POS = 0x1f0
NODE_RADIUS = 110.0
MOB_SAME = 3.0                   # a node candidate within this of an actor IS that actor — some mobs
                                 # (skeletons) carry the node vtable; drop them (confirmed 2026-06-24)
ACTOR_BLOCK = 4.5                # softer: a mob this close to a (real) node likely blocks Ctrl+0
_node_cache = {"nodes": [], "actors": [], "ts": 0.0, "px": 0.0, "pz": 0.0}


def read_node_array(pm, base):
    """Nearby REAL harvest nodes. Heap-scan for objects whose vtable is EXACTLY a node vtable
    (NODE_VTS — wood/ore/bush all share 0x14eb830 + sibling 0x14eba10), reading world pos at
    obj+0x60. Mobs/NPCs are different vtables, so there is NO monster filter to mis-fire and no
    real nodes get dropped. Cached ~6s / until the player moves 30m (a full scan is a few sec)."""
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    now = time.time()
    if (now - _node_cache["ts"] < 6.0
            and math.hypot(px - _node_cache["px"], pz - _node_cache["pz"]) < 30):
        return _node_cache["nodes"]   # scan covers ~110m; don't re-scan every 10m hop (pause-y)
    try:
        import numpy as np
    except Exception:
        return _node_cache["nodes"]
    vts = np.array(sorted(base + v for v in NODE_VTS), dtype="<u8")
    actvt = base + ACTOR_VT
    VQ = ctypes.windll.kernel32.VirtualQueryEx; VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    cand = {}                                    # rounded (x,z) -> (x,z)  (dedup co-located objs)
    acts = {}                                    # rounded (x,z) -> (x,z)  non-player actor positions
    addr = 0; mbi = _MBI()
    while addr < 0x7fff00000000:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            addr += 0x10000; continue
        b0 = mbi.BaseAddress; sz = mbi.RegionSize
        if (mbi.State == 0x1000 and not (mbi.Protect & 0x100)
                and (mbi.Protect & 0xff) in (0x04, 0x02, 0x20)
                and 0x10000000000 < b0 < 0x7ff000000000 and 0 < sz < 0x8000000):
            try:
                buf = pm.read_bytes(b0, sz)
            except Exception:
                buf = b""
            if len(buf) >= 0x80:
                arr = np.frombuffer(buf[:(len(buf) // 8) * 8], dtype="<u8")
                for i in np.where(np.isin(arr, vts))[0]:
                    o = int(i) * 8
                    if o + NODE_POS + 12 > len(buf):
                        continue
                    x, y, z = struct.unpack_from("<fff", buf, o + NODE_POS)
                    if (math.isfinite(x) and math.isfinite(z) and math.isfinite(y)
                            and abs(y - py) < 25):
                        d = math.hypot(x - px, z - pz)
                        if d < NODE_RADIUS:
                            cand[(round(x), round(z))] = (b0 + o, round(x, 1), round(z, 1))
                for i in np.where(arr == actvt)[0]:          # non-player actors (mobs/NPCs)
                    o = int(i) * 8
                    if o + ACTOR_POS + 12 > len(buf):
                        continue
                    x, y, z = struct.unpack_from("<fff", buf, o + ACTOR_POS)
                    if (math.isfinite(x) and math.isfinite(z) and abs(y - py) < 35):
                        d = math.hypot(x - px, z - pz)
                        if 3.0 < d < NODE_RADIUS:             # >3m skips the player's own actor(s)
                            acts[(round(x), round(z))] = (round(x, 1), round(z, 1))
        addr = b0 + sz if sz else addr + 0x10000
    actors = list(acts.values())
    # (1) Drop candidates that sit ON an actor (<MOB_SAME) — that mob IS the candidate (some
    #     skeletons carry the node vtable). Real nodes have their nearest actor many metres away.
    survivors = [(a, x, z) for (a, x, z) in cand.values()
                 if not any(math.hypot(x - ax, z - az) < MOB_SAME for ax, az in actors)]
    # (2) MOTION filter — the robust one: re-read each survivor's pos after a short delay; anything
    #     that MOVED is a wandering mob (skeletal trooper) carrying the node vtable. Static harvest
    #     nodes never move. Catches mobs the actor-coincidence test misses (offset/idle actor pos).
    try:
        time.sleep(0.6)
        kept = []
        for a, x, z in survivors:
            try:
                nx = pm.read_float(a + NODE_POS); nz = pm.read_float(a + NODE_POS + 8)
                if math.isfinite(nx) and math.isfinite(nz) and math.hypot(nx - x, nz - z) > 0.5:
                    continue                         # moved -> mob, drop
            except Exception:
                pass
            kept.append((x, z))
        nodes = kept
    except Exception:
        nodes = [(x, z) for _a, x, z in survivors]
    nodes.sort(key=lambda n: math.hypot(n[0] - px, n[1] - pz))
    _node_cache.update(nodes=nodes, actors=actors, ts=time.time(), px=px, pz=pz)
    return nodes


def read_actors(pm, base):
    """Non-player actor (mob/NPC) positions from the most recent node scan. Excludes the player's
    own actor(s) (anything within 3 m of the player at scan time). Call read_node_array first."""
    return _node_cache.get("actors", [])


def diag_scan_main():
    """Dump exactly what the PRODUCTION read_node_array sees: exact-vtable node objects (NODE_VTS,
    pos @ +0x60), nearest-first. Owner stands on a node -> nearest entry should be ~0-2m. Verifies
    the detector with no guessing. Writes to gdbg.log."""
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _dbg("DIAG: no live EQ2"); return
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    _dbg(f"DIAG === player @ {px:.1f},{py:.1f},{pz:.1f}  vts={[hex(v) for v in NODE_VTS]} pos+{NODE_POS:#x} ===")
    _node_cache["ts"] = 0.0                       # force a fresh scan
    nodes = read_node_array(pm, base)
    actors = read_actors(pm, base)
    _dbg(f"DIAG nodes ({len(nodes)} within {NODE_RADIUS:.0f}m)  [nearAct = dist to nearest actor]:")
    for x, z in nodes[:40]:
        d = math.hypot(x - px, z - pz)
        na = min((math.hypot(x - a[0], z - a[1]) for a in actors), default=9e9)
        flag = "  <-- ON AN ACTOR (mob?)" if na < 2.0 else ""
        _dbg(f"  node d={d:6.1f} @ {x:8.1f},{z:8.1f}   nearAct={na:5.1f}{flag}")
    _dbg(f"DIAG actors ({len(actors)} non-self within {NODE_RADIUS:.0f}m):")
    for a in sorted(actors, key=lambda a: math.hypot(a[0] - px, a[1] - pz))[:20]:
        _dbg(f"  actor d={math.hypot(a[0]-px, a[1]-pz):6.1f} @ {a[0]:8.1f},{a[1]:8.1f}")
    _dbg("DIAG === done ===")


def diag_wide_main(rad=10.0):
    """Find ANY object whose world pos (@+0x1a0) is within `rad` m of the player, regardless of
    vtable range. Reports each object's vtable (relative to base). Used to capture the vtable of a
    node type the narrow scan misses (e.g. bush/shrubbery) — owner stands ON it, we read what's
    under him. Tries a few likely pos offsets too in case bush nodes store pos elsewhere."""
    import numpy as np
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _dbg("DIAG2: no live EQ2"); return
    px = pm.read_float(base + POS_OFF); py = pm.read_float(base + POS_OFF + 4)
    pz = pm.read_float(base + POS_OFF + 8)
    _dbg(f"DIAG2 === player @ {px:.1f},{py:.1f},{pz:.1f} rad={rad} ===")
    # accept any qword that points into the module's vtable band (covers node 0x149xxxx..
    # actor 0x178xxxx and a margin) — that's an object header (vtable at offset 0).
    vlo = base + 0x1000000; vhi = base + 0x1900000
    POS_OFFS = (0x1a0, 0x60, 0x1f0, 0x90)         # try several struct layouts
    VQ = ctypes.windll.kernel32.VirtualQueryEx; VQ.restype = ctypes.c_size_t
    h = pm.process_handle
    hits = []   # (dist, pos_off, vt_rel, x, z)
    addr = 0; mbi = _MBI()
    while addr < 0x7fff00000000:
        if not VQ(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            addr += 0x10000; continue
        b0 = mbi.BaseAddress; sz = mbi.RegionSize
        if (mbi.State == 0x1000 and not (mbi.Protect & 0x100)
                and (mbi.Protect & 0xff) in (0x04, 0x02, 0x20)
                and 0x10000000000 < b0 < 0x7ff000000000 and 0 < sz < 0x8000000):
            try:
                buf = pm.read_bytes(b0, sz)
            except Exception:
                buf = b""
            if len(buf) >= 0x200:
                arr = np.frombuffer(buf[:(len(buf) // 8) * 8], dtype="<u8")
                for i in np.where((arr >= vlo) & (arr < vhi))[0]:
                    o = int(i) * 8
                    for po in POS_OFFS:
                        if o + po + 12 > len(buf):
                            continue
                        x, y, z = struct.unpack_from("<fff", buf, o + po)
                        if (math.isfinite(x) and math.isfinite(z) and math.isfinite(y)
                                and abs(y - py) < 30):
                            d = math.hypot(x - px, z - pz)
                            if d < rad:
                                hits.append((d, po, int(arr[i]) - base, round(x, 1), round(z, 1)))
        addr = b0 + sz if sz else addr + 0x10000
    hits.sort()
    # drop the player's own object stack (everything basically at his feet), dedup by (posOff,vt)
    # keeping the NEAREST instance, so distinct nearby objects (the node) stand out.
    best = {}
    for d, po, vt, x, z in hits:
        if d < 1.5:                          # player self-cluster
            continue
        key = (po, vt)
        if key not in best or d < best[key][0]:
            best[key] = (d, x, z)
    rows = sorted((d, po, vt, x, z) for (po, vt), (d, x, z) in best.items())
    _dbg(f"DIAG2 distinct objects 1.5..{rad}m ({len(rows)}):")
    for d, po, vt, x, z in rows[:40]:
        _dbg(f"  obj d={d:5.1f} posOff=+{po:#05x} vt=+{vt:#08x} @ {x:8.1f},{z:8.1f}")
    _dbg("DIAG2 === done ===")


def _tour_anchors(graph):
    """Coarse list of (x,z) anchors to drive the loop, IN WALK ORDER. From the graph if recorded
    (every Nth point ~ a tour of the walked loop), else the legacy sparse route.json waypoints."""
    if graph is not None and len(graph) >= 2:
        step = max(1, len(graph.pts) // 40)            # ~40 anchors spread along the loop
        return [tuple(p) for p in graph.pts[::step]]
    try:
        wps = json.loads(Path(ROUTE).read_text())["waypoints"]
        if len(wps) > 2 and math.hypot(wps[0][0] - wps[-1][0], wps[0][1] - wps[-1][1]) < 3:
            wps = wps[:-1]
        return [tuple(w) for w in wps]
    except Exception:
        return []


def gather_loop_main(keys, laps):
    global _combat_stop
    hwnd, pid, pm, base = _live_eq2()
    if not hwnd:
        _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
    _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
    graph = load_graph()                               # dense recorded waypoint graph (or None)
    anchors = _tour_anchors(graph)
    # SURVIVAL: watch the log for damage to us in the background
    _combat["hit"] = False; _combat_stop = False
    threading.Thread(target=_combat_watch, daemon=True).start()
    x0, z0, _ = state(pm, base)
    safe = (x0, z0); flees = [0]
    done = {}                                          # node-cell -> last-visited ts; expires so a
    DONE_TTL = 240.0                                   # respawned node gets re-harvested on a long run
    prog = {"mode": "gather_loop", "graph": bool(graph), "anchors": len(anchors),
            "lap": 0, "wp": 0, "harvests_total": 0, "events": [], "named_nodes": []}

    def flee_if_combat():
        """Under attack -> run to the last clear spot to break aggro. Never stand and die."""
        if not _combat["hit"]:
            return False
        _combat["hit"] = False; flees[0] += 1; prog["fled"] = flees[0]
        prog["status"] = "FLEEING combat"; _status(json.dumps(prog))
        keys.release_all()
        nav(pm, base, hwnd, safe[0], safe[1], keys, grace=3.0); keys.release_all()
        time.sleep(2.5); _combat["hit"] = False
        prog["status"] = "resumed"
        return True

    def harvest_nearest():
        """Harvest sensed nodes NEAREST-FIRST. Nodes are OFF the path: graph-route to the closest
        graph point then straight-hop out to the node, harvest, and the next pass re-enters the
        path automatically. Skip nodes farther than ROAM off the walked loop (walled off)."""
        nonlocal safe
        misses = 0
        blocked = 0          # consecutive mob_blocked — skeletons squatting on nodes (NOT in the
        for _ in range(40):  # actor list, so the proximity filter misses them); bail the area at 3
            _check_stop()
            if flee_if_combat():
                return                          # fled — bail this batch, caller moves on
            x, z, _h = state(pm, base)
            # Only nodes near the MAPPED mesh (reachable) — never beeline into unmapped areas/walls.
            _now = time.time()
            cand = sorted((n for n in read_node_array(pm, base)
                           if _now - done.get((round(n[0] / 3), round(n[1] / 3)), 0) > DONE_TTL
                           and reachable(graph, n[0], n[1])),
                          key=lambda n: math.hypot(n[0] - x, n[1] - z))
            if not cand:
                _dbg("hn: no reachable nodes -> travel"); return
            # Prefer CLEAR nodes — skip ones with a mob squatting on them (a non-player actor within
            # ACTOR_BLOCK m). Only fall back to guarded nodes when nothing clear is reachable, so we
            # don't waste trips on skeleton-camp nodes we can't acquire (Ctrl+0 grabs the mob).
            # Detection already dropped candidates that ARE mobs (skeletons on the node vtable).
            # Here just PREFER nodes with no mob within ACTOR_BLOCK (so Ctrl+0 grabs the node, not a
            # mob beside it); fall back to the rest if none are clear. Camp-bail (3x mob_blocked)
            # backstops dense camps where every node has a wanderer next to it.
            actors = read_actors(pm, base)
            clear = [n for n in cand
                     if not any(math.hypot(n[0] - a[0], n[1] - a[1]) < ACTOR_BLOCK for a in actors)]
            cand = clear or cand
            tx, tz = cand[0]                     # the NEAREST reachable node to us
            d0 = math.hypot(tx - x, tz - z)
            prog["status"] = "to nearest node"; prog["target"] = [tx, tz]
            _status(json.dumps(prog))
            _dbg(f"hn: {len(cand)} cand; go {tx:.0f},{tz:.0f} d0={d0:.0f}")
            # Go STRAIGHT to it (smooth: face once, walk in). The reachability gate already keeps
            # targets near the mapped mesh, so straight-line stays on walkable ground; unstuck
            # handles the odd bump. (Graph routing zigzagged through dense mesh points = jerky.)
            ok, dist_left, stuck = _nav_unstuck(pm, base, hwnd, keys, tx, tz, grace=1.0)
            keys.release_all()
            done[(round(tx / 3), round(tz / 3))] = time.time()   # visited (expires after DONE_TTL)
            _dbg(f"  -> dist_left={dist_left:.1f} stuck={stuck}")
            if dist_left > 3.5:
                misses += 1
                if misses >= 3:                  # 3 unreachable in a row -> stop grinding, relocate
                    prog["status"] = "nodes unreachable here — relocating"
                    _status(json.dumps(prog))
                    return
                continue                         # couldn't get within ~3m -> skip, next nearest
            misses = 0
            if not _combat["hit"]:
                safe = (tx, tz)
            prog["status"] = "settling to harvest"; _status(json.dumps(prog))
            _settle(pm, base, keys)              # STOP COMPLETELY before harvesting (owner rule)
            hv = harvest(hwnd)
            tries = 0                            # held node drifted out of ~2m range -> hug & retry
            while hv.get("done") == "toofar" and tries < 2:
                tries += 1
                _dbg(f"  toofar -> hug closer + settle, retry {tries}")
                nav(pm, base, hwnd, tx, tz, keys, grace=0.5); keys.release_all()
                _settle(pm, base, keys)
                hv = harvest(hwnd)
            # Skeleton wandered onto a REAL node (owner: wait/retry — it'll move off). Linger a few
            # seconds and retry; the node is static, the mob isn't. Don't abandon a real node to a
            # passing wanderer.
            if hv.get("done") in ("mob_blocked", "mob"):
                prog["status"] = "mob on node — waiting for it to wander"
                _status(json.dumps(prog))
                _dbg("  mob on node -> wait 3.5s for it to wander, retry once")
                time.sleep(3.5)
                _check_stop()
                _settle(pm, base, keys)
                hv = harvest(hwnd)
            _dbg(f"  HARVEST done={hv.get('done')} n={hv.get('harvests')} node={hv.get('node')} "
                 f"dbg={hv.get('debug')}")
            if hv.get("harvests"):
                blocked = 0
                prog["harvests_total"] += hv["harvests"]
                prog["events"].append({"node": hv.get("node"), "n": hv["harvests"],
                                       "rare": hv.get("rare"), "at": [tx, tz]})
                if hv.get("node"):
                    prog["named_nodes"].append({"xz": [round(tx, 1), round(tz, 1)], "name": hv["node"]})
                _status(json.dumps(prog))
            elif hv.get("done") in ("mob_blocked", "mob"):
                blocked += 1
                # full DONE_TTL lockout — don't grind a camped (stationary) mob's node; the one
                # wait-retry above already gave a wanderer its chance. Move on.
                prog.setdefault("mobs_skipped", []).append({"mob": hv.get("node"), "at": [tx, tz]})
                _status(json.dumps(prog))
                if blocked >= 2:                 # 2 mob nodes here -> camp, bail so the tour leaves
                    _dbg("  mob camp -> bail this area, advance tour")
                    return
            else:
                blocked = 0                       # gone/depleted/toofar — not a mob, keep going

    try:
        try:
            os.remove(_DBG)
        except OSError:
            pass
        _dbg(f"=== gather start: {len(anchors)} anchors, graph={bool(graph)} ===")
        # RIGHT WHEN WE START: grab the nearest reachable node(s) to where we stand.
        harvest_nearest()
        # then tour the loop (graph-routed travel), harvesting nearest-first at each anchor.
        for lap in range(laps):
            prog["lap"] = lap + 1
            if not anchors:
                break
            x0, z0, _ = state(pm, base)
            start = min(range(len(anchors)),
                        key=lambda i: math.hypot(anchors[i][0] - x0, anchors[i][1] - z0))
            tour = anchors[start:] + anchors[:start]    # start at the nearest anchor, walk in order
            for wi, anc in enumerate(tour):
                _check_stop()
                if flees[0] >= 8:
                    prog["stop"] = "too much combat — bailed"; break
                prog["wp"] = wi + 1; prog["status"] = "travel (path)"
                _status(json.dumps(prog))
                flee_if_combat()
                goto(pm, base, hwnd, keys, anc[0], anc[1], graph); keys.release_all()
                if not _combat["hit"]:
                    safe = (anc[0], anc[1])             # reached clear -> new safe anchor
                harvest_nearest()
            if prog.get("stop"):
                break
        prog["finished"] = True; _status(json.dumps(prog))
    finally:
        _combat_stop = True
        keys.release_all()


def main():
    keys = Keys()
    try:                                       # clear any stale STOP flag from a prior run
        os.remove(STOP_FLAG)
    except OSError:
        pass
    try:
        tgt = json.loads(Path(TARGET).read_text())
    except Exception as e:
        _status(json.dumps({"ok": False, "err": f"target: {e}"})); return
    try:
        if tgt.get("chat"):
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            type_chat(hwnd, str(tgt["chat"]))
            _status(json.dumps({"ok": True, "chat": tgt["chat"], "ts": time.time()}))
        elif tgt.get("login_form"):
            p = tgt["login_form"]                    # {user, password, character, world, fields, submit}
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            type_login_form(hwnd, p["user"], p["password"], p["character"], p.get("world", "Wuoshi"),
                            user_click=(p.get("fields") or {}).get("user"),
                            submit=bool(p.get("submit", True)))
            _status(json.dumps({"ok": True, "typed": p["character"], "ts": time.time()}))
        elif tgt.get("submit_enter"):
            hwnd = _eq2_window_any()
            if hwnd:
                focus_eq2(hwnd); time.sleep(0.3); _tap(0x0D)
            _status(json.dumps({"ok": True, "submit": True, "ts": time.time()}))
        elif tgt.get("form_type") is not None:
            # TEST: focus the EQ2 login form and type into the USERNAME field via keybd_event
            # (the proven in-world input path). Default focus = password; Shift+Tab -> username.
            hwnd = _eq2_window_any()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "no EQ2 window"})); return
            focus_eq2(hwnd); time.sleep(0.4)
            _tap(0x09, shift=True); time.sleep(0.3)     # Shift+Tab: password -> username
            _tap(0x23); time.sleep(0.1)                  # End
            for _ in range(40):
                _tap(0x08)                               # BackSpace x40 (clear)
            time.sleep(0.2)
            for ch in str(tgt["form_type"]):
                res = _u.VkKeyScanW(ord(ch))
                if res != -1:
                    _tap(res & 0xFF, bool((res >> 8) & 1)); time.sleep(0.03)
            _status(json.dumps({"ok": True, "form_type": tgt["form_type"], "ts": time.time()}))
        elif tgt.get("diag"):
            diag_scan_main()
            _status(json.dumps({"ok": True, "diag": True, "ts": time.time()}))
        elif tgt.get("diag2"):
            diag_wide_main(float(tgt.get("rad", 10.0)))
            _status(json.dumps({"ok": True, "diag2": True, "ts": time.time()}))
        elif tgt.get("gather_loop"):
            gather_loop_main(keys, int(tgt.get("laps", 1)))
        elif tgt.get("route_loop"):
            route_loop_main(keys, int(tgt.get("laps", 1)))
        elif tgt.get("loop"):
            loop_main(keys, int(tgt.get("max_nodes", 5)))
        else:
            tx, tz = float(tgt["tx"]), float(tgt["tz"])
            do_harvest = bool(tgt.get("harvest", True))
            hwnd, pid, pm, base = _live_eq2()
            if not hwnd:
                _status(json.dumps({"ok": False, "err": "live EQ2 not found"})); return
            _u.ShowWindow(hwnd, 3); _u.SetForegroundWindow(hwnd); time.sleep(0.3)
            ok, d, _ = nav(pm, base, hwnd, tx, tz, keys); keys.release_all()
            out = {"ok": ok, "dist": round(d, 2), "pid": pid, "ts": time.time()}
            if ok and do_harvest:
                out["harvest"] = harvest(hwnd)
            _status(json.dumps(out))
    except StopRequested:
        keys.release_all()
        _status(json.dumps({"stopped": True, "ts": time.time()}))
    except Exception as e:
        import traceback
        keys.release_all()
        _status(json.dumps({"ok": False, "err": str(e),
                                            "tb": traceback.format_exc()[-400:], "ts": time.time()}))
    finally:
        keys.release_all()


if __name__ == "__main__":
    import faulthandler
    try:
        _cf = open(r"C:\ib\crash.log", "w")
        faulthandler.enable(file=_cf)            # native crash (segfault) -> crash.log
    except Exception:
        _cf = None
    try:
        main()
    except BaseException:
        import traceback
        try:
            with open(r"C:\ib\crash.log", "a") as f:
                f.write("PYEXC:\n" + traceback.format_exc())
        except Exception:
            pass
        raise
