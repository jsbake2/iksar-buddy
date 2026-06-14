"""Direct-login driver — the validated hands-off login + character switch for
BOTH the healer (brain) and the crafters (forge). Replaces the old
LaunchPad-PLAY -> char-select-OCR-click path (which loaded the wrong toon when
the list order changed and fought a flaky click offset).

Flow (all proven live on iksar_buddy2, 2026-06-14):

  LaunchPad.exe ──(auto-login, or creds if the LOGIN form shows + EULA accept)──>
  wait for PLAY (patch done) ──> close LaunchPad ──> EverQuest2.exe ──>
  game LOGIN form ──> type password / Tab / character / Enter ──> in world.

Switch (same account only):  open chat ──> "/camp <Character>" ──> Enter.

Two hard-won details baked in here:
  * EQ2's game login form only registers AHK Send("{Raw}…") in SendMode "Event"
    (SendText / virsh send-key do NOT register). And Event mode RACES the Shift
    key across adjacent digit/symbol pairs, corrupting passwords (`3`->`#`,
    `$`->`4`) — `SetKeyDelay 55, 45` fixes it. Always use _login_ahk() below.
  * EQ2 dialog buttons ignore AHK Click() (offset/registration). Use the guest's
    ibgclick (Guest.click) — the healer-validated native click — for any button.

The host drives the phases and gates on screenshots (OCR/pixel); each guest action
is a tiny AHK fired through Guest.run_ahk (ibrun, interactive session).
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Callable

from .guest import Guest

# --- calibration (1920x1080 fullscreen-windowed) -----------------------------
# LaunchPad opens at a fixed position every time (owner-confirmed stable), so the
# PLAY button is a constant point. Game-login coords are fullscreen-stable too.
PLAY_PX = (1300, 752)                 # solid-orange point on the PLAY button face
EULA_ACCEPT_PX = (1045, 757)          # "I ACCEPT" on the LaunchPad EULA
DISK_CONTINUE_PX = (960, 661)         # "Continue" on the low-disk warning dialog
UI_IMPORT_OK_PX = (905, 620)          # "Ok" on first-login "Import UI Settings"
WORLD = "Wuoshi"

Log = Callable[[str], None]


# --- AHK snippet builders ----------------------------------------------------
def _open_ahk(exe: str) -> str:
    return (
        '#Requires AutoHotkey v2.0\n'
        'EQDIR := "C:\\Users\\Public\\Daybreak Game Company\\Installed Games\\EverQuest II"\n'
        f"Run('\"' EQDIR '\\{exe}\"', EQDIR)\n"
    )


def _lp_login_ahk(user: str, password: str) -> str:
    # LaunchPad is a CEF app: SendText registers fine and the username field is
    # focused by default. '!' etc. are literal under SendText (no Alt modifier).
    return (
        '#Requires AutoHotkey v2.0\n'
        'SetTitleMatchMode 2\n'
        'WinActivate "EverQuest"\n'
        'WinWaitActive "EverQuest", , 5\n'
        'Sleep 1000\n'
        f'SendText("{user}")\n'
        'Sleep 400\n'
        'Send("{Tab}")\n'
        'Sleep 400\n'
        f'SendText("{password}")\n'
        'Sleep 400\n'
        'Send("{Enter}")\n'
    )


def _login_ahk(user: str, password: str, character: str, world: str) -> str:
    # Game login form. Default focus = Password. Walk to Username (Shift+Tab),
    # reset every field, retype with the Shift-safe key delay, submit.
    def raw(s: str) -> str:
        return s.replace('"', '""')
    return (
        '#Requires AutoHotkey v2.0\n'
        'SendMode "Event"\n'
        'SetKeyDelay 55, 45\n'
        'SetTitleMatchMode 2\n'
        'WinActivate "EverQuest II"\n'
        'WinWaitActive "EverQuest II", , 5\n'
        'Sleep 700\n'
        'Send("+{Tab}")\n'                       # password -> username
        'Sleep 300\n'
        'Send("^a")\nSleep 120\nSend("{Delete}")\nSleep 200\n'
        f'Send("{{Raw}}{raw(user)}")\n'
        'Sleep 300\nSend("{Tab}")\nSleep 300\n'  # -> password
        'Send("^a")\nSleep 120\nSend("{Delete}")\nSleep 200\n'
        f'Send("{{Raw}}{raw(password)}")\n'
        'Sleep 300\nSend("{Tab}")\nSleep 300\n'  # -> character
        'Send("^a")\nSleep 120\nSend("{Delete}")\nSleep 200\n'
        f'Send("{{Raw}}{raw(character)}")\n'
        'Sleep 400\nSend("{Enter}")\n'
    )


def _camp_ahk(character: str) -> str:
    return (
        '#Requires AutoHotkey v2.0\n'
        'SendMode "Event"\n'
        'SetKeyDelay 55, 45\n'
        'SetTitleMatchMode 2\n'
        'WinActivate "EverQuest II"\n'
        'WinWaitActive "EverQuest II", , 5\n'
        'Sleep 500\n'
        'Send("{Enter}")\n'                      # open chat input
        'Sleep 700\n'
        f'Send("{{Raw}}/camp {character}")\n'
        'Sleep 500\n'
        'Send("{Enter}")\n'                      # submit
    )


_KEY_AHK = (
    '#Requires AutoHotkey v2.0\n'
    'SetTitleMatchMode 2\n'
    'WinActivate "EverQuest II"\n'
    'WinWaitActive "EverQuest II", , 5\n'
    'Sleep 400\n'
    'Send("{%s}")\n'
)


class LoginDriver:
    def __init__(self, g: Guest, log: Log = lambda m: None) -> None:
        self.g = g
        self.log = log

    # --- screenshot helpers --------------------------------------------------
    def _pixel(self, x: int, y: int) -> tuple[int, int, int]:
        if not self.g.grab():
            return (0, 0, 0)
        return self.g.pixel(x, y)

    def _ocr(self, x: int, y: int, w: int, h: int, *, grab: bool = True) -> str:
        if grab and not self.g.grab():
            return ""
        crop = subprocess.run(
            ["magick", self.g.ppm, "-crop", f"{w}x{h}+{x}+{y}", "+repage",
             "-colorspace", "Gray", "-resize", "300%", "png:-"],
            capture_output=True).stdout
        try:
            out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"],
                                 input=crop, capture_output=True).stdout.decode(errors="replace")
        except OSError:
            return ""
        return re.sub(r"\s+", " ", out).strip().lower()

    @staticmethod
    def _is_orange(px: tuple[int, int, int]) -> bool:
        # EQ2 LaunchPad PLAY button face: a dark, saturated orange (~160,57,3).
        r, g, b = px
        return r > 120 and b < 60 and (r - g) > 60 and (g - b) > 20

    # --- phase gates ---------------------------------------------------------
    def _launchpad_up(self) -> bool:
        out = self.g.exec_ps("if (Get-Process LaunchPad -ErrorAction SilentlyContinue) {'Y'}")
        return bool(out and "Y" in out)

    def _play_ready(self) -> bool:
        return self._is_orange(self._pixel(*PLAY_PX))

    def _login_form_present(self) -> bool:
        # The game login form: the gold "Login" header sits centred above the fields.
        return "login" in self._ocr(760, 395, 130, 32)

    # --- public: full login (power-on assumed done; VM at desktop) -----------
    def login(self, user: str, password: str, character: str,
              world: str = WORLD, *, timeout_lp: int = 150,
              timeout_form: int = 150, timeout_world: int = 60) -> bool:
        g = self.g

        # 1) LaunchPad -------------------------------------------------------
        if not self._launchpad_up():
            self.log("opening LaunchPad")
            g.run_ahk(_open_ahk("LaunchPad.exe"))
            for _ in range(30):
                if self._launchpad_up():
                    break
                time.sleep(2)
            else:
                self.log("LaunchPad never appeared"); return False
        time.sleep(8)

        # auto-login is the norm; only log in if PLAY hasn't appeared and a LOGIN
        # form is showing. Then accept the EULA if it pops.
        logged_in = False
        t0 = time.time()
        while time.time() - t0 < timeout_lp:
            if self._play_ready():
                break
            if not logged_in and self._launchpad_login_needed():
                self.log("LaunchPad LOGIN form — entering credentials")
                g.run_ahk(_lp_login_ahk(user, password))
                logged_in = True
                time.sleep(6)
                self._accept_eula_if_present()
            time.sleep(3)
        else:
            self.log("LaunchPad PLAY never went ready (patch stuck?) — continuing anyway")
        self.log("LaunchPad ready; closing it")
        g.exec_ps("Stop-Process -Name LaunchPad -Force -ErrorAction SilentlyContinue", wait=True)
        time.sleep(2)

        # 2) EverQuest2.exe --------------------------------------------------
        self.log("launching EverQuest2.exe")
        g.run_ahk(_open_ahk("EverQuest2.exe"))
        t0 = time.time()
        while time.time() - t0 < timeout_form:
            if self._login_form_present():
                break
            if "continue" in self._ocr(880, 630, 170, 60, grab=False):
                self.log("dismissing low-disk warning")
                g.click(*DISK_CONTINUE_PX)
                time.sleep(2)
            time.sleep(3)
        else:
            self.log("game login form never appeared"); return False

        # 3) credentials -> in world ----------------------------------------
        self.log(f"submitting login for {character}")
        g.run_ahk(_login_ahk(user, password, character, world))
        ok = self._await_world(character, timeout_world)
        return ok

    def _launchpad_login_needed(self) -> bool:
        # The account name shows top-right ONLY when authed; the un-authed panel
        # has a big LOGIN button instead. Cheap signal: OCR the lower-centre band
        # for a standalone "login" button (the PLAY band reads "play").
        return "login" in self._ocr(1040, 690, 240, 50)

    def _accept_eula_if_present(self) -> None:
        for _ in range(10):
            if "accept" in self._ocr(900, 730, 320, 50):
                self.log("accepting EULA")
                self.g.click(*EULA_ACCEPT_PX)
                time.sleep(3)
                return
            time.sleep(2)

    def _await_world(self, character: str, timeout: int) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(4)
            if self._login_form_present():
                # still at the form — likely a rejection dialog
                if "rejected" in self._ocr(560, 380, 360, 80):
                    self.log("LOGIN REJECTED (bad credentials?)")
                    return False
                continue
            # form gone -> zoning/in-world. Clear the first-login UI-import dialog.
            self._dismiss_ui_import()
            self.log(f"in world as {character}")
            return True
        self.log("did not reach world within timeout")
        return False

    def _dismiss_ui_import(self) -> None:
        if "import" in self._ocr(640, 270, 360, 40):
            self.log("dismissing Import-UI dialog")
            self.g.run_ahk(_KEY_AHK % "Enter")
            time.sleep(3)

    # --- public: same-account switch via /camp ------------------------------
    def camp_to(self, character: str, *, timeout: int = 60) -> bool:
        if not self.g.eq2_running():
            self.log("camp_to: EQ2 not running"); return False
        self.log(f"/camp {character}")
        self.g.run_ahk(_camp_ahk(character))
        return self._await_world(character, timeout)
