r"""Windows input + window plumbing for the in-guest agent (REFACTOR P3.2 —
split out of harvest_agent.py; all code verbatim).

Everything that touches user32 lives here: window enumeration/focus, Event-mode
keyboard (keybd_event — the only input EQ2's UI widgets accept), the login-form
and chat typists, the guarded Ctrl-chords for the harvest macros, and the
pydirectinput hold-state Keys manager the nav loop drives.
"""
from __future__ import annotations
import time

import ctypes
from ctypes import wintypes

import pydirectinput
pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False

# Offsets come from the ONE shared module (REFACTOR P0.4); deploy pushes it
# alongside this file as C:\ib\agent\offsets.py.
try:
    from offsets import PROC  # in-guest sibling
except ImportError:
    from guest_agent.offsets import PROC

_u = ctypes.windll.user32


def _win_pid(h):
    pid = wintypes.DWORD(0)
    _u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


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


def _ctrl_chord(vk):
    """Send Ctrl+<vk> with the modifier GUARANTEED held while the number lands.

    Why this is fussy: keybd_event queues Ctrl-down and the number-down as separate events.
    If EQ2 samples input on a frame between them, it sees a BARE number -> that fires hotbar
    slot 9/10 (a combat art) on the current target = the bot 'attacking' creatures. So we
    press Ctrl, SPIN until GetAsyncKeyState confirms the OS sees it down, THEN send the
    number, and only release Ctrl after the number is fully up. A bare number can never leak.
    VK_CONTROL=0x11; high bit (0x8000) of GetAsyncKeyState = key currently down."""
    KEYUP = 0x02
    _u.keybd_event(0x11, 0, 0, 0)
    t = time.time()
    while not (_u.GetAsyncKeyState(0x11) & 0x8000) and time.time() - t < 0.3:
        time.sleep(0.01)
    time.sleep(0.05)
    _u.keybd_event(vk, 0, 0, 0); time.sleep(0.06)
    _u.keybd_event(vk, 0, KEYUP, 0); time.sleep(0.05)
    _u.keybd_event(0x11, 0, KEYUP, 0); time.sleep(0.02)


def harvest_key():
    # Ctrl+9 in-game macro = auto-target nearest node + harvest. The HOTBAR only accepts
    # Event-mode input (keybd_event), NOT SendInput scancodes (pydirectinput) — same reason
    # AHK had to use SendMode Event for it. VK_CONTROL=0x11, '9'=0x39.
    _ctrl_chord(0x39)


def target_key():
    # Ctrl+0 = owner macro: TARGET nearest harvestable + /consider, in one press. A node cons
    # as 'not attackable'; a creature cons as attackable. This is our acquire+classify step —
    # harvest_key (Ctrl+9) then works the CURRENT target, so we never re-target off the node.
    _ctrl_chord(0x30)


def tab_key():
    # TAB cycles the current target to the NEXT-nearest thing. When the gather macro grabbed a
    # creature, one Tab can shift the target onto the node sitting right next to it. VK_TAB=0x09.
    KEYUP = 0x02
    _u.keybd_event(0x09, 0, 0, 0); time.sleep(0.05)
    _u.keybd_event(0x09, 0, KEYUP, 0)


def _jump():
    try:
        pydirectinput.keyDown("space"); time.sleep(0.08); pydirectinput.keyUp("space")
    except Exception:
        pass


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
