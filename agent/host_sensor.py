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

from shared import tunables

DOM = "iksar_buddy"
PPM = "/tmp/ib_sensor.ppm"

# ---- geometry (derived live; see session-2026-06-11.md work blocks 2-3) -----
# FALLBACK defaults — config/calibration.yaml (healer_dom + sensor:) overrides
# them at import below (REFACTOR P1.3); edit THERE after a UI move.
SELF_FRAME = (0, 30, 160, 70)      # top-left own-player frame: x,y,w,h
SELF_TRACK = (19, 128)             # own HP/power bar track x-range
GRP_TRACK = (33, 139)              # group member bar track x-range (fill measure)
PWR_BASE, PITCH, SLOTS = 128, 75, 6
SEARCH = 4                          # +/- rows to hunt for a bar within its slot
HP_PWR_GAP = 8                      # HP row sits this many px above power
ROW_DY = 32                         # detriment row center below the power row
CELL_XC = [43, 66, 88, 112, 135]    # 5 detriment cell centers (x)
INSET = 6
BLUE_MIN_PX = 12                    # min blue px in a row to call it a power bar
BRIGHT_SUM = 90                     # r+g+b above this = lit bar px
ICON_SUM = 120                      # r+g+b above this = lit icon px
ICON_FRAC = 0.4                     # fraction of a cell lit => detriment

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
# idle/empty = 0 bright px, a line of typed text ~150. Threshold 25 catches a few
# characters and the cursor's lit phase. A clear (black) line => not active.
# The blinking-cursor gap (an active-but-EMPTY input is dark between blinks) is
# covered by blink HYSTERESIS in host_agent: any hit latches "busy" for ~3s, so a
# cursor blinking ~1Hz keeps the line latched busy the whole time it's open.
CHAT_INPUT = (50, 1019, 208, 22)        # x, y, w, h
CHAT_BRIGHT_THRESH = 25                  # bright-px count above which = active


def _overlay_calibration() -> None:
    """Overlay config/calibration.yaml onto the module constants (P1.3/P1.5).
    self_scan (y0,y1) maps onto SELF_FRAME's y/h. Missing keys keep the baked-in
    fallback; a broken YAML degrades to all-fallbacks (tunables.load -> {})."""
    global DOM, SELF_FRAME
    cal = tunables.calibration()
    DOM = cal.get("healer_dom") or DOM
    s = cal.get("sensor") or {}
    table = {"self_track": ("SELF_TRACK", tuple), "grp_track": ("GRP_TRACK", tuple),
             "pwr_base_y": ("PWR_BASE", int), "pitch": ("PITCH", int),
             "slots": ("SLOTS", int), "search": ("SEARCH", int),
             "hp_pwr_gap": ("HP_PWR_GAP", int), "row_dy": ("ROW_DY", int),
             "cell_xc": ("CELL_XC", list), "inset": ("INSET", int),
             "chat_input": ("CHAT_INPUT", tuple),
             "chat_bright_thresh": ("CHAT_BRIGHT_THRESH", int),
             "blue_min_px": ("BLUE_MIN_PX", int), "bright_sum": ("BRIGHT_SUM", int),
             "icon_sum": ("ICON_SUM", int), "icon_frac": ("ICON_FRAC", float)}
    for key, (name, cast) in table.items():
        if s.get(key) is not None:
            try:
                globals()[name] = cast(s[key])
            except (TypeError, ValueError):
                pass
    if s.get("self_scan"):
        y0, y1 = s["self_scan"]
        SELF_FRAME = (SELF_FRAME[0], int(y0), SELF_FRAME[2], int(y1) - int(y0))


_overlay_calibration()


def _sh(*a) -> subprocess.CompletedProcess:
    return subprocess.run(list(a), capture_output=True, text=True)


def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > BRIGHT_SUM   # lit bar vs dark track
def is_icon(c):  r, g, b = c; return (r + g + b) > ICON_SUM   # lit icon vs black cell


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
        return best_y if best_n >= BLUE_MIN_PX else None

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
        return best_y if best_n >= BLUE_MIN_PX else None

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
            if len(lit) > ICON_FRAC * len(box):
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
        """RAW chat-focus signals. The final `safe` verdict + blink-hysteresis live
        in host_agent (stateful). Here we only report:
        - game_present: own power bar located => the in-world HUD is showing.
        - chat_active : the chat INPUT line holds text/cursor (bright pixels in the
          CHAT_INPUT region). False when the line is clear (black). None on a read
          failure (-> the agent treats that as busy, fail-closed)."""
        return {"game_present": power is not None, "chat_active": self._chat_active()}

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
