"""Guest(dom) — the reusable host-side I/O core for one libvirt guest (FORGE.md §2).

This is the healer's host-helpers (gexec / gclick / gtype / virsh screenshot +
magick / keys.txt+ibkey) generalized to take a DOMAIN instead of hardcoding
`iksar_buddy`, so one process can drive two guests. Everything is host-side: the
guest only ever receives injected input and is sensed by screenshot.

All methods are SYNCHRONOUS subprocess calls — run them via
`loop.run_in_executor` from async code (as the worker does) so the event loop
never blocks on a guest round-trip.

Reused by the healer eventually too; for now it's Forge's input/sense substrate.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time

VIRSH = ["sudo", "-n", "virsh", "-c", "qemu:///system"]

# Char -> (linux key name, needs-shift) for virsh send-key typing. Ported verbatim
# from infra/vm/host-helpers/gtype.py (handles () via shift+9/0 for EQ2 search).
_SHIFT = "KEY_LEFTSHIFT"
_TYPE_MAP: dict[str, tuple[str, bool]] = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _TYPE_MAP[_c] = ("KEY_" + _c.upper(), False)
for _d in "0123456789":
    _TYPE_MAP[_d] = ("KEY_" + _d, False)
_TYPE_MAP.update({
    ' ': ("KEY_SPACE", False), '\n': ("KEY_ENTER", False), '\t': ("KEY_TAB", False),
    '.': ("KEY_DOT", False), ',': ("KEY_COMMA", False), '/': ("KEY_SLASH", False),
    '\\': ("KEY_BACKSLASH", False), '-': ("KEY_MINUS", False), '_': ("KEY_MINUS", True),
    ';': ("KEY_SEMICOLON", False), ':': ("KEY_SEMICOLON", True),
    "'": ("KEY_APOSTROPHE", False), '"': ("KEY_APOSTROPHE", True),
    '(': ("KEY_9", True), ')': ("KEY_0", True),
    '[': ("KEY_LEFTBRACE", False), ']': ("KEY_RIGHTBRACE", False),
    '{': ("KEY_LEFTBRACE", True), '}': ("KEY_RIGHTBRACE", True),
    '=': ("KEY_EQUAL", False), '+': ("KEY_EQUAL", True), '|': ("KEY_BACKSLASH", True),
    '*': ("KEY_8", True), '&': ("KEY_7", True), '^': ("KEY_6", True), '%': ("KEY_5", True),
    '$': ("KEY_4", True), '#': ("KEY_3", True), '@': ("KEY_2", True), '!': ("KEY_1", True),
    '~': ("KEY_GRAVE", True), '`': ("KEY_GRAVE", False),
    '<': ("KEY_COMMA", True), '>': ("KEY_DOT", True), '?': ("KEY_SLASH", True),
})


class Guest:
    def __init__(self, dom: str, width: int = 1920, height: int = 1080) -> None:
        self.dom = dom
        self.width = width
        self.height = height
        # per-domain scratch file so two guests don't clobber each other's frame
        self.ppm = f"/tmp/ib_forge_{dom}.ppm"

    # -- low level ---------------------------------------------------------
    def _virsh(self, *args, timeout: float = 8.0) -> subprocess.CompletedProcess:
        return subprocess.run(VIRSH + list(args), capture_output=True, text=True,
                              timeout=timeout)

    def _monitor(self, cmd: dict, timeout: float = 6.0) -> bool:
        r = self._virsh("qemu-monitor-command", self.dom, json.dumps(cmd), timeout=timeout)
        return r.returncode == 0

    # -- VM lifecycle ------------------------------------------------------
    def state(self) -> str:
        return (self._virsh("domstate", self.dom).stdout or "").strip()

    def is_running(self) -> bool:
        return self.state() == "running"

    def start_vm(self) -> bool:
        if self.is_running():
            return True
        return self._virsh("start", self.dom).returncode == 0

    def shutdown_vm(self) -> bool:
        return self._virsh("shutdown", self.dom).returncode == 0

    def agent_ready(self) -> bool:
        r = self._virsh("qemu-agent-command", self.dom,
                        '{"execute":"guest-ping"}', timeout=6)
        return r.returncode == 0 and '"return"' in (r.stdout or "")

    def eq2_running(self) -> bool:
        """True if the EQ2 client is up in the guest. Crafting (and clicks) require
        it — firing input at a closed game errors the guest-side AHK. The PS ALWAYS
        emits Y/N so an empty result means a guest-exec hiccup (not 'closed'); retry a
        couple times so a transient hiccup doesn't false-bail the whole job."""
        ps = "if (Get-Process EverQuest2 -ErrorAction SilentlyContinue) {'Y'} else {'N'}"
        for _ in range(3):
            out = (self.exec_ps(ps) or "").strip()
            if "Y" in out:
                return True
            if "N" in out:
                return False
            time.sleep(0.5)                       # empty == hiccup -> retry
        return False

    # -- capture (virsh screenshot -> magick crop) -------------------------
    def grab(self) -> bool:
        return self._virsh("screenshot", self.dom, self.ppm).returncode == 0

    def crop(self, x: int, y: int, w: int, h: int) -> dict[tuple[int, int], tuple[int, int, int]]:
        """Pixel dict {(absx,absy): (r,g,b)} for a region of the last grab()."""
        r = subprocess.run(["magick", self.ppm, "-crop", f"{w}x{h}+{x}+{y}",
                            "+repage", "txt:-"], capture_output=True, text=True)
        pix: dict = {}
        for line in r.stdout.splitlines():
            m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", line)
            if m:
                px, py, v = int(m.group(1)), int(m.group(2)), int(m.group(3), 16)
                pix[(px + x, py + y)] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        return pix

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """Single pixel (r,g,b) from the last grab(); (0,0,0) on failure."""
        return self.crop(x, y, 1, 1).get((x, y), (0, 0, 0))

    def region_png(self, x: int, y: int, w: int, h: int) -> bytes:
        """Cropped region of the last grab() as PNG bytes (for template match)."""
        r = subprocess.run(["magick", self.ppm, "-crop", f"{w}x{h}+{x}+{y}",
                            "+repage", "png:-"], capture_output=True)
        return r.stdout

    def grab_region_png(self, x: int, y: int, w: int, h: int) -> bytes:
        """One fresh screenshot cropped to a region — for the fast reaction poll
        (avoids a full-frame magick decode when only the small region matters)."""
        if not self.grab():
            return b""
        return self.region_png(x, y, w, h)

    # -- input: mouse (GUEST-SIDE click, the healer-proven path) -----------
    def click(self, x: int, y: int, wait: bool = False) -> bool:
        """Left-click at guest pixel (x,y). Uses the SAME mechanism the healer's
        accept-dialog helpers use (validated live): write the pixel coords to
        C:\\ib\\click.txt and fire the 'ibgclick' scheduled task, so an AHK script
        clicks natively INSIDE the guest at true 1920x1080 coords. The host
        qemu input-send-event path mis-registered in EQ2's UI, so we don't use it.

        wait=True BLOCKS until the ibgclick task actually finishes (task State back to
        'Ready'), so the click is GUARANTEED landed before the caller proceeds — use it
        before typing into a field, where a not-yet-landed focus-click would let the
        keystrokes leak into the game world."""
        ps = (f"Set-Content C:\\ib\\click.txt '{int(x)} {int(y)}' -NoNewline; "
              f"Start-ScheduledTask -TaskName ibgclick")
        if wait:
            ps += ("; $n=0; while((Get-ScheduledTask -TaskName ibgclick).State "
                   "-eq 'Running' -and $n -lt 50){Start-Sleep -Milliseconds 80; $n++}")
        return self.exec_ps(ps, wait=True if wait else False) is not None

    def double_click(self, x: int, y: int) -> bool:
        """DOUBLE-click at guest pixel (x,y). EQ2 recipe rows need a double-click to
        LOAD the recipe into the craft pane (a single click only highlights the row);
        verified live. Event-mode AHK Click("x y 2") via ibrun (same substrate as
        gclick_ev.ahk: activate EQ2, move, click), waited."""
        script = ('CoordMode "Mouse", "Screen"\n'
                  'SendMode "Event"\n'
                  'SetMouseDelay 40\n'
                  'SetTitleMatchMode 2\n'
                  'if !WinExist("EverQuest II")\n'
                  '    ExitApp\n'
                  'WinActivate("EverQuest II")\n'
                  'Sleep 180\n'
                  f'MouseMove({int(x)}, {int(y)})\n'
                  'Sleep 100\n'
                  f'Click("{int(x)} {int(y)} 2")\n')
        return self.run_ahk(script)

    # -- input: typing into the GAME WORLD (virsh send-key scancodes) ------
    def type_text(self, s: str, enter: bool = False) -> None:
        """Send key SCANCODES to the guest. EQ2's game WORLD reads these (as hotkeys),
        but its UI text WIDGETS do NOT register them — for a focused EQ2 text field
        (recipe search, login form) use type_field() instead."""
        for ch in s:
            spec = _TYPE_MAP.get(ch)
            if spec is None and ch.isupper():
                spec = ("KEY_" + ch, True)
            if spec is None:
                continue
            key, shift = spec
            keys = [_SHIFT, key] if shift else [key]
            self._virsh("send-key", self.dom, "--codeset", "linux", *keys)
            time.sleep(0.04)
        if enter:
            self._virsh("send-key", self.dom, "--codeset", "linux", "KEY_ENTER")

    # -- input: typing into a FOCUSED EQ2 UI FIELD (AHK Event-mode {Raw}) ---
    def type_field(self, text: str, enter: bool = True,
                   focus_xy: tuple[int, int] | None = None) -> bool:
        """Type into a FOCUSED EQ2 UI text field (recipe search, etc.). EQ2 widgets
        only register real Windows key messages — virsh send-key/SendText do NOT land
        in them (only the game world reads those scancodes). So drive AHK Event-mode
        Send("{Raw}…") via ibrun, the SAME proven mechanism as the login form
        (forge/login.py).

        focus_xy=(x,y): ATOMICALLY activate EQ2 + click the field + type, all in ONE
        AHK script with no foreground gap. This is the safe path — a separate
        ibgclick-then-type leaves a window-focus race where the keystrokes can land in
        the GAME WORLD (recipe letters = w/a/s/d movement). When focus_xy is given the
        click and the typing can't be split apart by anything stealing focus between them.
        focus_xy=None keeps the old behavior (caller focused the field) for login.py."""
        esc = (text or "").replace('"', '""')
        # NO Ctrl+A clear here — the modifier races the fast key delay and types a literal
        # 'a' ("aleather backpack"). The caller already clicks the clear-X to empty the box.
        # 40,25 is reliable typing (22,12 dropped chars) and faster than the login's 55,45.
        head = 'SendMode "Event"\nSetKeyDelay 40, 25\n'
        if focus_xy:
            x, y = int(focus_xy[0]), int(focus_xy[1])
            head += ('CoordMode "Mouse", "Screen"\n'
                     'SetMouseDelay 40\n'
                     'SetTitleMatchMode 2\n'
                     'if !WinExist("EverQuest II")\n'
                     '    ExitApp\n'
                     'WinActivate("EverQuest II")\n'
                     'WinWaitActive("EverQuest II",, 2)\n'
                     'Sleep 150\n'
                     f'MouseMove({x}, {y})\n'
                     'Sleep 80\n'
                     f'Click("{x} {y}")\n'
                     'Sleep 160\n')
        else:
            head += 'Sleep 120\n'
        script = head + f'Send("{{Raw}}{esc}")\n'
        if enter:
            script += 'Sleep 150\nSend("{Enter}")\n'
        return self.run_ahk(script)

    # -- input: hotkeys (keys.txt + ibkey AHK task, per guest) -------------
    def press_keys(self, seq: str) -> bool:
        """Inject an AHK key SEQUENCE (comma-separated specs, e.g. "1,2,3" or
        "F2,4" or "pause_1.5"). Writes C:\\ib\\keys.txt in the guest and fires the
        'ibkey' scheduled task (Event-mode AHK, infra/vm/ahk/key_ev.ahk). Same
        mechanism the healer uses; the task name is per-guest (separate installs)."""
        seq = seq.replace("'", "")  # keys.txt is single-quoted in PS; no quotes in specs
        ps = (f"Set-Content C:\\ib\\keys.txt '{seq}' -NoNewline; "
              f"Start-ScheduledTask -TaskName ibkey")
        return self.exec_ps(ps, wait=False) is not None

    # -- run an AHK script (ibrun scheduled task, session 1 = interactive) --
    def run_ahk(self, script: str) -> bool:
        """Write an AHK v2 script to C:\\ib\\launcher.ahk and fire the 'ibrun'
        scheduled task (runs it in the INTERACTIVE session, so windows/keystrokes
        are visible and register — guest-exec runs in session 0 and can't). The
        script is shipped base64 so passwords/symbols survive PowerShell quoting.
        This is the single substrate for the whole login flow (LaunchPad creds,
        game login, /camp switch). Fire-and-forget; the host gates on screenshots."""
        import base64 as _b64
        b = _b64.b64encode(script.encode("utf-8")).decode("ascii")
        ps = (f"$b=[Convert]::FromBase64String('{b}');"
              f"[IO.File]::WriteAllBytes('C:\\ib\\launcher.ahk',$b);"
              f"Start-ScheduledTask -TaskName ibrun")
        return self.exec_ps(ps, wait=True) is not None

    # -- push a host file into the guest (base64 -> WriteAllBytes) ----------
    def push_file(self, local_path, guest_path: str) -> bool:
        """Copy a host-side file into the guest, no shared folder. Used to keep the
        in-guest reflex agent (craft_reflex.py) in lockstep with the repo so guests
        never run stale counter logic. Returns True on a confirmed write.

        The base64 is written in CHUNKS: one guest-exec command has a hard length limit
        (~a few KB), so a large file (craft_reflex.py is ~47KB -> ~63KB b64) must be
        appended to a staging .b64 in pieces then decoded — inlining it all in a single
        command silently fails (which stopped sync_reflex from ever landing)."""
        import base64 as _b64
        from pathlib import Path as _Path
        try:
            data = _Path(local_path).read_bytes()
        except Exception:
            return False
        b = _b64.b64encode(data).decode("ascii")   # b64 alphabet has no quotes -> safe inline
        gp = guest_path.replace("\\", "\\\\")
        b64p = gp + ".b64"
        self.exec_ps(f"Remove-Item '{gp}','{b64p}' -ErrorAction SilentlyContinue")
        for i in range(0, len(b), 6000):
            self.exec_ps(f"Add-Content -Path '{b64p}' -Value '{b[i:i + 6000]}' -NoNewline")
        out = self.exec_ps(
            f"[IO.File]::WriteAllBytes('{gp}',[Convert]::FromBase64String((Get-Content -Raw '{b64p}')));"
            f"Remove-Item '{b64p}' -ErrorAction SilentlyContinue;(Get-Item '{gp}').Length")
        return bool(out and out.strip().isdigit())

    def sync_reflex(self) -> bool:
        """Push the canonical guest_agent/craft_reflex.py into C:\\ib\\agent and
        bounce the 'ibagent' task so the new code is loaded. Host-side canonical
        copy lives next to the app (../guest_agent/craft_reflex.py)."""
        from pathlib import Path as _Path
        src = _Path(__file__).resolve().parent.parent / "guest_agent" / "craft_reflex.py"
        if not src.exists() or not self.push_file(src, r"C:\ib\agent\craft_reflex.py"):
            return False
        self.exec_ps("schtasks /End /TN ibagent; Start-Sleep 1; schtasks /Run /TN ibagent")
        return True

    def set_logging_on(self) -> bool:
        """Force EQ2 chat/combat logging ON for the NEXT session by writing the cvars into
        eq2_recent.ini BEFORE the game starts. EQ2 reads this file at startup and REWRITES it
        on exit, so a session that left /log off would otherwise carry that forward and the
        bots run blind (no eq2log_<char>.txt to read for craft-completion / counter resolution).
        Idempotent: replaces only cl_logchat + cl_use_default_log_file, every other setting is
        preserved. Must run while the guest is up but BEFORE EverQuest2.exe launches."""
        p = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
             r"\EverQuest II\eq2_recent.ini").replace("\\", "\\\\")
        ps = (
            f"$p='{p}'; $keys='cl_logchat','cl_use_default_log_file'; "
            "$lines = if (Test-Path $p) { @(Get-Content $p | "
            "Where-Object { $keys -notcontains (($_ -split '\\s+')[0]) }) } else { @() }; "
            "$lines += 'cl_logchat true','cl_use_default_log_file true'; "
            "Set-Content -Path $p -Value $lines -Encoding ASCII; 'OK'"
        )
        ok = (self.exec_ps(ps) or "").strip().endswith("OK")
        return ok

    # -- guest PowerShell (guest-exec; gexec.py pattern) -------------------
    def exec_ps(self, ps: str, wait: bool = True, poll: int = 60) -> str | None:
        """Run PowerShell in the guest. wait=True polls for completion and returns
        stdout; wait=False fires and forgets (returns "" on successful launch)."""
        try:
            r = self._virsh("qemu-agent-command", self.dom, json.dumps({
                "execute": "guest-exec", "arguments": {
                    "path": "powershell.exe",
                    "arg": ["-NoProfile", "-NonInteractive", "-Command", ps],
                    "capture-output": True}}))
            pid = json.loads(r.stdout)["return"]["pid"]
        except Exception:
            return None
        if not wait:
            return ""
        for _ in range(poll):
            try:
                s = self._virsh("qemu-agent-command", self.dom, json.dumps({
                    "execute": "guest-exec-status", "arguments": {"pid": pid}}))
                st = json.loads(s.stdout)["return"]
            except Exception:
                return None
            if st.get("exited"):
                return base64.b64decode(st.get("out-data", "")).decode(errors="replace")
            time.sleep(0.2)
        return None

    def read_file(self, path: str, tail: int | None = None) -> str | None:
        """Read a guest text file (optionally last N lines) via PowerShell."""
        if tail:
            ps = f"if(Test-Path '{path}'){{Get-Content '{path}' -Tail {tail}}}"
        else:
            ps = f"if(Test-Path '{path}'){{Get-Content '{path}'}}"
        return self.exec_ps(ps)
