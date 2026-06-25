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

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import yaml

from .guest import Guest

# --- calibration (1920x1080 fullscreen-windowed) -----------------------------
# LaunchPad opens at a fixed position every time (owner-confirmed stable), so the
# PLAY button is a constant point. Game-login coords are fullscreen-stable too.
PLAY_PX = (1300, 752)                 # solid-orange point on the PLAY button face
EULA_ACCEPT_PX = (1045, 757)          # "I ACCEPT" on the LaunchPad EULA
DISK_CONTINUE_PX = (960, 661)         # "Continue" on the low-disk warning dialog
UI_IMPORT_CANCEL_PX = (970, 616)      # "Cancel" (default) on "Import UI Settings"
WORLD = "Wuoshi"

Log = Callable[[str], None]


def load_accounts(profile_dir: str | Path | None = None) -> tuple[dict, str]:
    """EQ2 account credentials keyed by VM domain, from accounts.yaml in the owner
    data dir (gitignored — never in the repo). Shared by forge + healer. Shape:
        world: Wuoshi
        accounts: { iksar_buddy2: {user: .., password: ..}, iksar_buddy: {...}, ... }
    Missing/garbage -> ({}, WORLD) so callers log a clear 'no creds' error."""
    base = Path(profile_dir or os.environ.get(
        "IB_FORGE_DIR", str(Path.home() / "ib-data" / "forge")))
    try:
        data = yaml.safe_load((base / "accounts.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    return (data.get("accounts") or {}), (data.get("world") or WORLD)


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
    # EQ2's login fields are a custom widget that IGNORES Ctrl+A select-all, so the old
    # "^a + Delete" clear silently did nothing — a pre-filled account (meatwad33w) stuck and
    # any new user was just appended/rejected. Clear by jumping to End and hard-backspacing.
    def raw(s: str) -> str:
        return s.replace('"', '""')
    CLR = 'Send("{End}")\nSleep 100\nSend("{BackSpace 40}")\nSleep 200\n'
    return (
        '#Requires AutoHotkey v2.0\n'
        'SendMode "Event"\n'
        'SetKeyDelay 55, 45\n'
        'SetTitleMatchMode 2\n'
        # Target the client by EXECUTABLE, not window title — the EQ2Emu client window title
        # doesn't match "EverQuest II", so title-based WinActivate failed and keystrokes went
        # nowhere (logins only ever "worked" off the pre-saved account). ahk_exe is reliable.
        'WinActivate "ahk_exe EverQuest2.exe"\n'
        'WinWaitActive "ahk_exe EverQuest2.exe", , 5\n'
        'Sleep 700\n'
        'Send("+{Tab}")\n'                       # password -> username
        'Sleep 300\n'
        + CLR +
        f'Send("{{Raw}}{raw(user)}")\n'
        'Sleep 300\nSend("{Tab}")\nSleep 300\n'  # -> password
        + CLR +
        f'Send("{{Raw}}{raw(password)}")\n'
        'Sleep 300\nSend("{Tab}")\nSleep 300\n'  # -> character
        + CLR +
        f'Send("{{Raw}}{raw(character)}")\n'
        'Sleep 300\nSend("{Tab}")\nSleep 300\n'  # -> world (blank on some clients
        + CLR +                                  # -> char-select w/o it)
        f'Send("{{Raw}}{raw(world)}")\n'
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
    def __init__(self, g: Guest, log: Log = lambda m: None, form_typer=None) -> None:
        self.g = g
        self.log = log
        # How the game LOGIN FORM gets filled. Default = AutoHotkey Send (works on the crafter
        # VMs). The harvest VM passes an agent keybd_event typer (AHK Send doesn't land on its
        # fullscreen form). Signature: (user, password, character, world) -> None.
        self.form_typer = form_typer or (
            lambda u, p, c, w: self.g.run_ahk(_login_ahk(u, p, c, w)))

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

    def _char_select_present(self) -> bool:
        # Char-select shows a "Select Character" header at the top-left list panel.
        return "select" in self._ocr(40, 612, 240, 34, grab=False)

    # --- public: power-on -> desktop -> in world (the Launch button) --------
    def boot_and_login(self, user: str, password: str, character: str,
                       world: str = WORLD) -> bool:
        """Full cold path: start the VM, wait for the agent + interactive desktop
        (auto-login), then log in. If EQ2 is already in world (same account, other
        toon), /camp-switch instead of relaunching the client. Shared by forge +
        healer so both behave identically."""
        g = self.g
        if not (user and password):
            self.log("no credentials — set accounts.yaml"); return False
        if not g.start_vm():
            self.log("VM start failed"); return False
        self.log("waiting for guest agent")
        for _ in range(40):
            if g.agent_ready():
                break
            time.sleep(3)
        else:
            self.log("guest agent never came up"); return False
        # The agent answers at the Windows LOGIN screen — before the interactive
        # desktop. Firing AHK then lands in session 0 (invisible). Wait for explorer.
        self.log("waiting for desktop (auto-login)")
        for _ in range(25):
            out = g.exec_ps("if (Get-Process explorer -ErrorAction SilentlyContinue) {'Y'}")
            if out and "Y" in out:
                break
            time.sleep(3)
        time.sleep(10)                              # let the shell settle
        if character and g.eq2_running():
            self.log("EQ2 already up — /camp switch")
            return self.camp_to(character)
        return self.login(user, password, character, world)

    # --- public: full login (LaunchPad+game form; VM at desktop) -------------
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

        # 1b) FORCE LOGGING ON before the game reads its config — EQ2 defaults /log off each
        # session, which blinds the bots (no eq2log_<char>.txt for completion/counter reads).
        self.log("logging: " + ("forced ON (eq2_recent.ini)" if g.set_logging_on()
                                 else "could NOT set eq2_recent.ini (will rely on in-game /log)"))

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
        self.form_typer(user, password, character, world)
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
            if self._login_form_present():           # grabs a fresh frame
                if "rejected" in self._ocr(560, 380, 360, 80, grab=False):
                    self.log("LOGIN REJECTED (bad credentials?)")
                    return False
                continue                             # still at the form
            if self._char_select_present():
                # account login OK but didn't go in-world (e.g. blank World field).
                self.log("landed at CHAR-SELECT, not in world (World field set?)")
                return False
            # neither form nor char-select -> zoning/in-world. Clear any first-login
            # "Import UI Settings" dialog and call it done.
            self._dismiss_ui_import()
            self.log(f"in world as {character}")
            return True
        self.log("did not reach world within timeout")
        return False

    def _dismiss_ui_import(self) -> None:
        # The first-login "Import UI Settings" modal pops up DURING zone-load, so it
        # races a one-shot check — poll for it for a while. Enter accepts it (AHK
        # Click and Escape do NOT work on it).
        for _ in range(6):
            if "import" in self._ocr(640, 270, 360, 40):
                self.log("dismissing Import-UI dialog")
                # ibgclick FOCUSES the (default) Cancel button; Enter activates it.
                # Click alone or Enter alone is unreliable on this modal — the combo
                # is what dismisses it (validated live).
                self.g.click(*UI_IMPORT_CANCEL_PX)
                time.sleep(0.6)
                self.g.run_ahk(_KEY_AHK % "Enter")
                time.sleep(3)
                return
            time.sleep(3)

    # --- public: same-account switch via /camp ------------------------------
    def camp_to(self, character: str, *, timeout: int = 60) -> bool:
        if not self.g.eq2_running():
            self.log("camp_to: EQ2 not running"); return False
        self.log(f"/camp {character}")
        self.g.run_ahk(_camp_ahk(character))
        return self._await_world(character, timeout)
