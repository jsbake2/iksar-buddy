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

    # -- input: mouse (qemu input-send-event, RESOLUTION-CORRECT) ----------
    def click(self, x: int, y: int) -> bool:
        """Left-click at guest pixel (x,y). The abs axis is 0..32767 normalized to
        the guest resolution — the healer's gclick.py hardcoded 1024x768, which
        misplaced clicks on a 1920x1080 guest; we divide by the REAL resolution."""
        ax = int(max(0, min(x, self.width - 1)) / self.width * 32767)
        ay = int(max(0, min(y, self.height - 1)) / self.height * 32767)
        ev = {"execute": "input-send-event", "arguments": {"events": [
            {"type": "abs", "data": {"axis": "x", "value": ax}},
            {"type": "abs", "data": {"axis": "y", "value": ay}},
            {"type": "btn", "data": {"button": "left", "down": True}},
            {"type": "btn", "data": {"button": "left", "down": False}}]}}
        return self._monitor(ev)

    # -- input: typing (virsh send-key) ------------------------------------
    def type_text(self, s: str, enter: bool = False) -> None:
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
