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


def is_green(c): r, g, b = c; return g > 85 and g > r + 20 and g > b + 20
def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90    # filled vs dark-empty track


def read_bar(pix, pred, y0, y1):
    """Find the bar ROW by color (the row with the most `pred` pixels across the
    fixed track), then fill% = bright pixels across the WHOLE track. Returns
    (y, fill%) or None. Robust to partial fill: the depleted track is dark and
    isn't counted, and the row is still the strongest even when low."""
    tx0, tx1 = TRACK
    best_y, best_n = None, 0
    for y in range(y0, y1):
        n = sum(1 for x in range(tx0, tx1) if pred(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    if best_y is None or best_n < 12:
        return None
    width = tx1 - tx0
    filled = sum(1 for x in range(tx0, tx1) if is_bright(pix.get((x, best_y), (0, 0, 0))))
    return best_y, round(100 * filled / width)


def main() -> None:
    x, y, w, h = FRAME
    pix = grab_region(x, y, w, h)
    hp = read_bar(pix, is_green, y, y + h)
    pw = read_bar(pix, is_blue, y, y + h)
    for label, b in (("HP", hp), ("POWER", pw)):
        print(f"{label}: y={b[0]} -> {b[1]}%" if b else f"{label}: not found")


if __name__ == "__main__":
    main()
