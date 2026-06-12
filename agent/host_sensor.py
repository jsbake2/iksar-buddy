"""Host-side sensor — one screenshot, all sensors (PROJECT.md §4, host variant).

The agent that senses EQ2 runs on the CachyOS HOST, not in the guest: it reads
the VM framebuffer with `virsh screenshot` and does all color/OCR work with the
host's magick + tesseract (the guest has neither tesseract nor a fast capture
path). The guest only ever receives injected input. This module is the sense
half; agent/host_agent.py streams it to the brain.

All detection here is the LIVE-VALIDATED math from the calibration scripts in
infra/vm/host-helpers/ (group_read / bar_read / detriment_read), consolidated to
share ONE frame per cycle instead of three screenshots:

  - bars: locate each by its hue-STABLE blue power row, HP row = 8px above, and
    measure fill by BRIGHTNESS not hue (EQ2 recolors HP green->yellow->red as it
    drops, so a green-detector misses a low/red bar).
  - detriments: 5 cells/member, sample each cell INTERIOR vs the black window
    backdrop; a lit interior = an effect. Generic cure clears any type, so any
    CURABLE lit cell => that member needs a cure. Revive sickness lights a cell
    but is uncurable and is excluded by color signature.
"""
from __future__ import annotations

import re
import subprocess

DOM = "iksar_buddy"
PPM = "/tmp/ib_sensor.ppm"

# ---- geometry (derived live; see session-2026-06-11.md work blocks 2-3) -----
SELF_FRAME = (0, 30, 160, 70)      # top-left own-player frame: x,y,w,h
SELF_TRACK = (19, 128)             # own HP/power bar track x-range
GRP_TRACK = (33, 139)              # group member bar track x-range (fill measure)
PWR_BASE, PITCH, SLOTS = 128, 75, 6
SEARCH = 4                          # +/- rows to hunt for a bar within its slot
HP_PWR_GAP = 8                      # HP row sits this many px above power
ROW_DY = 32                         # detriment row center below the power row
CELL_XC = [43, 66, 88, 112, 135]    # 5 detriment cell centers (x)
INSET = 6

# Uncurable effects detectable by a STABLE color signature. Revive sickness is
# NOT here: its icon average color + cell vary wildly per death (software:103,26,61
# / gpu:191,87,71 cell2 / gpu:141,40,91 cell1 -- likely an animated icon), so
# color matching is unreliable. Rez sickness is handled CONTEXTUALLY in
# host_agent (a member that just died->revived has cure suppressed for the rez
# window). Add entries here only for uncurables with a genuinely stable color.
IGNORE_SIGNATURES: dict[str, tuple[int, int, int]] = {}
IGNORE_TOL = 40

# Chat-input TEXT area (bottom-left): tightened to exclude the left chat icon and
# the gold hotbar border that contaminated a wider box. Measured in this region:
# idle/empty = 0 bright px, a line of typed text ~150 -> clean. Threshold 25 also
# catches a few characters and likely the cursor's lit phase.
# CALIBRATION GAP (why the guard is still fail-closed): an active-but-EMPTY input
# is only a blinking cursor -- caught when lit, missed when dark, so the owner
# must (a) confirm the empty-active look and (b) we add blink HYSTERESIS (latch
# unsafe for ~3s after any hit) before flipping CHAT_GUARD_CALIBRATED true.
CHAT_INPUT = (50, 1019, 208, 22)        # x, y, w, h
CHAT_BRIGHT_THRESH = 25                  # bright-px count above which = active
CHAT_GUARD_CALIBRATED = False


def _sh(*a) -> subprocess.CompletedProcess:
    return subprocess.run(list(a), capture_output=True, text=True)


def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90    # lit bar vs dark track
def is_icon(c):  r, g, b = c; return (r + g + b) > 120    # lit icon vs black cell


def is_ignored(rgb):
    for name, ref in IGNORE_SIGNATURES.items():
        if sum((a - b) ** 2 for a, b in zip(rgb, ref)) <= IGNORE_TOL ** 2:
            return name
    return None


class HostSensor:
    """One virsh screenshot per read_world(); crops regions off the saved PPM."""

    def grab(self) -> bool:
        r = _sh("sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM)
        return r.returncode == 0

    def _crop(self, x, y, w, h) -> dict:
        r = _sh("magick", PPM, "-crop", f"{w}x{h}+{x}+{y}", "+repage", "txt:-")
        pix = {}
        for line in r.stdout.splitlines():
            m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", line)
            if m:
                px, py, v = int(m.group(1)), int(m.group(2)), int(m.group(3), 16)
                pix[(px + x, py + y)] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        return pix

    # -- bars --------------------------------------------------------------
    def _power_row(self, pix, track, y_hint):
        tx0, tx1 = track
        best_y, best_n = None, 0
        for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
            n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y), (0, 0, 0))))
            if n > best_n:
                best_y, best_n = y, n
        return best_y if best_n >= 12 else None

    def _fill(self, pix, track, y) -> int:
        tx0, tx1 = track
        filled = sum(1 for x in range(tx0, tx1) if is_bright(pix.get((x, y), (0, 0, 0))))
        return round(100 * filled / (tx1 - tx0))

    def read_self(self, pix):
        """(hp%, power%) for the top-left own-player frame, or (None, None).
        Scan the whole frame for the blue power row; HP is 8px above it."""
        x, y, w, h = SELF_FRAME
        pwr_y = self._power_row_scan(pix, SELF_TRACK, y, y + h)
        if pwr_y is None:
            return None, None
        return self._fill(pix, SELF_TRACK, pwr_y - HP_PWR_GAP), self._fill(pix, SELF_TRACK, pwr_y)

    def _power_row_scan(self, pix, track, y0, y1):
        tx0, tx1 = track
        best_y, best_n = None, 0
        for y in range(y0, y1):
            n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y), (0, 0, 0))))
            if n > best_n:
                best_y, best_n = y, n
        return best_y if best_n >= 12 else None

    def read_members(self, pix) -> list[dict]:
        """Per present group slot: hp%, power%, dead, detriments, cure-needed."""
        out = []
        for slot in range(SLOTS):
            pwr_y = self._power_row(pix, GRP_TRACK, PWR_BASE + PITCH * slot)
            if pwr_y is None:
                continue
            hp = self._fill(pix, GRP_TRACK, pwr_y - HP_PWR_GAP)
            power = self._fill(pix, GRP_TRACK, pwr_y)
            dets, cure = self._detriments(pix, pwr_y + ROW_DY)
            out.append({"slot": slot, "hp": hp, "power": power,
                        "dead": hp <= 1, "detriments": dets, "cure": cure})
        return out

    def _detriments(self, pix, row_y):
        cells = []
        for ci, xc in enumerate(CELL_XC):
            box = [pix.get((x, y), (0, 0, 0))
                   for x in range(xc - INSET, xc + INSET + 1)
                   for y in range(row_y - INSET, row_y + INSET + 1)]
            lit = [c for c in box if is_icon(c)]
            if len(lit) > 0.4 * len(box):
                avg = tuple(sum(c[i] for c in lit) // len(lit) for i in range(3))
                cells.append({"cell": ci, "rgb": list(avg), "ignored": is_ignored(avg)})
        cure = any(c["ignored"] is None for c in cells)
        return cells, cure

    # -- top-level ---------------------------------------------------------
    def read_world(self) -> dict | None:
        """One screenshot -> full sensed world, or None if the grab failed."""
        if not self.grab():
            return None
        # one crop covering the whole left column (self frame + all group slots)
        pix = self._crop(0, 26, 160, PWR_BASE + PITCH * SLOTS + ROW_DY + 20)
        hp, power = self.read_self(pix)
        members = self.read_members(pix)
        safety = self.chat_safety(pix, power)
        return {"own": {"hp": hp, "power": power}, "members": members,
                "chat_safety": safety}

    # -- chat-safety guard (PROJECT.md §Chat-Safety; the INVIOLABLE invariant) --
    def chat_safety(self, pix, power) -> dict:
        """Fail-CLOSED focus check. `safe` is True ONLY when we can prove focus is
        on the game world AND the chat input is not active -- otherwise injection
        must never happen (a stray macro typed into chat is the dead giveaway).

        - game_present: the player's own power bar was located, i.e. the in-world
          HUD is showing (not desktop/login/loading/alt-tabbed). Fully automatic.
        - chat_active: whether the chat INPUT line is focused for typing. Needs a
          calibrated fingerprint of the active chat bar (owner activates chat once
          so we capture CHAT_INPUT region + active color). UNTIL calibrated this
          returns None, which forces safe=False -- correct fail-closed behavior.

        safe = game_present AND chat_active is False. None (uncalibrated) -> unsafe.
        """
        game_present = power is not None
        chat_active = self._chat_active()           # True / False / None
        # safe ONLY if proven: game showing, chat not active, AND the chat-active
        # detector validated for the empty-cursor case. Fail closed otherwise.
        safe = bool(game_present) and chat_active is False and CHAT_GUARD_CALIBRATED
        return {"game_present": game_present, "chat_active": chat_active, "safe": safe}

    def _chat_active(self, ppm: str = PPM):
        """True if the chat input line holds content (typed text reliably; the
        blinking-cursor-only case is the calibration gap noted at CHAT_INPUT).
        Counts bright pixels in the input region. None on read failure."""
        x, y, w, h = CHAT_INPUT
        r = _sh("magick", ppm, "-crop", f"{w}x{h}+{x}+{y}", "+repage",
                "-colorspace", "Gray", "-threshold", "60%",
                "-format", "%[fx:mean*w*h]", "info:")
        try:
            return int(float(r.stdout.strip())) > CHAT_BRIGHT_THRESH
        except (ValueError, AttributeError):
            return None


if __name__ == "__main__":
    import json
    w = HostSensor().read_world()
    print(json.dumps(w, indent=2) if w else "grab failed")
