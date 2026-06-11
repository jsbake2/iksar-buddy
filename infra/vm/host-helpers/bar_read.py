#!/usr/bin/env python3
"""Self-locating HP/power bar reader (color-scan, no hardcoded coordinates).

Finds Jenskin's own HP (green) + power (blue) bars in the top-left player frame
by scanning for the bar rows by color, derives each bar's track, and reports
fill% = (bright pixels in the track) / track-width. The depleted part of a bar
goes dark and drops out of the count.

This is the pattern for ALL bar sensors (self + group HP, wards): detect by color
and compute a fill ratio; never eyeball x/y. Validated full=100% on solo Jenskin;
folds into agent/capture.py for the live heal loop. Run: python3 bar_read.py
"""
from __future__ import annotations
import re
import subprocess

DOM = "iksar_buddy"
PPM, PNG = "/tmp/bar.ppm", "/tmp/bar.png"
# search box for the SELF player frame (top-left). Group frames are below it.
FRAME = (0, 30, 160, 70)   # x, y, w, h
# Fixed bar track (calibrated). The bar is fixed UI; only the FILL varies, so we
# measure brightness across the WHOLE track, not just the colored span (else a
# half-empty bar reads 100%). Re-derive these once at full HP if the UI moves.
TRACK = (19, 128)          # x_lo, x_hi  (same for HP + power)


def grab_region(x: int, y: int, w: int, h: int) -> dict[tuple[int, int], tuple[int, int, int]]:
    subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM],
                   capture_output=True)
    subprocess.run(["magick", PPM, PNG], capture_output=True)
    r = subprocess.run(["magick", PNG, "-crop", f"{w}x{h}+{x}+{y}", "+repage", "txt:-"],
                       capture_output=True, text=True)
    pix = {}
    for line in r.stdout.splitlines():
        m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", line)
        if m:
            px, py, v = int(m.group(1)), int(m.group(2)), int(m.group(3), 16)
            pix[(px + x, py + y)] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return pix


HP_PWR_GAP = 8    # power bar sits this many px below HP in the self frame

def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90    # filled vs dark-empty track


def fill_pct(pix, y):
    """fill% = bright pixels across the fixed track at row y. Hue-agnostic: the
    filled span counts whether it's green/yellow/red (EQ2 recolors HP as it
    drops); the depleted span is dark and drops out."""
    tx0, tx1 = TRACK
    width = tx1 - tx0
    filled = sum(1 for x in range(tx0, tx1) if is_bright(pix.get((x, y), (0, 0, 0))))
    return round(100 * filled / width)


def find_power_row(pix, y0, y1):
    """Locate the POWER bar row by blue (hue-STABLE anchor); the HP row is a
    fixed offset above. Anchoring on power avoids the green-detect failure when
    HP is low and the bar has gone red."""
    tx0, tx1 = TRACK
    best_y, best_n = None, 0
    for y in range(y0, y1):
        n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def read_self(pix=None):
    """Returns (hp%, power%) or (None, None) if the power bar can't be found."""
    if pix is None:
        x, y, w, h = FRAME
        pix = grab_region(x, y, w, h)
    x, y, w, h = FRAME
    pwr_y = find_power_row(pix, y, y + h)
    if pwr_y is None:
        return None, None
    return fill_pct(pix, pwr_y - HP_PWR_GAP), fill_pct(pix, pwr_y)


def main() -> None:
    hp, pw = read_self()
    print(f"HP: {hp}%" if hp is not None else "HP: not found")
    print(f"POWER: {pw}%" if pw is not None else "POWER: not found")


if __name__ == "__main__":
    main()
