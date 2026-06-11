#!/usr/bin/env python3
"""Per-member detriment (curable effect) detector for the group window.

A Defiler's other job is curing. Each member frame has a row of 5 detriment
cells under the bars; a hostile effect lights one up. We detect presence by
sampling each cell's INTERIOR (not its tan border) against the black window
backdrop: empty cell interior = near-black, filled = a bright icon.

Geometry (derived live, anchored on the hue-stable blue power bar so it tracks
the frame even when HP has gone red):
  detriment row y-center = power_y + ROW_DY
  5 cell centers at CELL_XC, sample a 12px interior box at each.

Type classification (noxious/elemental/trauma/arcane/curse) is deferred: EQ2
keys detriment TYPE to the icon border color, which needs a calibration pass
with the owner applying one known type at a time. For now we report presence +
the cell's dominant interior color so that map can be filled in. The avg color
feeds CURE_COLORS once calibrated. Run: python3 detriment_read.py
"""
from __future__ import annotations
import re
import subprocess

DOM = "iksar_buddy"
PPM = "/tmp/det.ppm"
X0, X1 = 33, 145
PWR_BASE, PITCH, SLOTS = 128, 75, 6
SEARCH = 4
ROW_DY = 32                                   # detriment row center below power bar
CELL_XC = [43, 66, 88, 112, 135]              # 5 cell centers (x)
INSET = 6                                      # half-size of the interior sample box

# CURE_COLORS: fill from a calibration pass (owner applies one type at a time).
# Map a representative interior RGB -> cure type. Empty until calibrated.
CURE_COLORS: dict[str, tuple[int, int, int]] = {}


def grab() -> dict[tuple[int, int], tuple[int, int, int]]:
    subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM],
                   capture_output=True)
    top = PWR_BASE - SEARCH
    h = PITCH * SLOTS
    r = subprocess.run(["magick", PPM, "-crop", f"{X1 - X0}x{h}+{X0}+{top}", "+repage", "txt:-"],
                       capture_output=True, text=True)
    pix = {}
    for line in r.stdout.splitlines():
        m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", line)
        if m:
            x, y, v = int(m.group(1)), int(m.group(2)), int(m.group(3), 16)
            pix[(x + X0, y + top)] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return pix


def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 120   # lit icon vs black backdrop


def power_row(pix, slot) -> int | None:
    y_hint = PWR_BASE + PITCH * slot
    best_y, best_n = None, 0
    for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
        n = sum(1 for x in range(X0, X1) if is_blue(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def read_detriments(pix=None) -> list[dict]:
    if pix is None:
        pix = grab()
    out = []
    for slot in range(SLOTS):
        pwr_y = power_row(pix, slot)
        if pwr_y is None:
            continue
        row_y = pwr_y + ROW_DY
        cells = []
        for ci, xc in enumerate(CELL_XC):
            box = [pix.get((x, y), (0, 0, 0))
                   for x in range(xc - INSET, xc + INSET + 1)
                   for y in range(row_y - INSET, row_y + INSET + 1)]
            lit = [c for c in box if is_bright(c)]
            if len(lit) > 0.4 * len(box):            # interior is a lit icon
                avg = tuple(sum(c[i] for c in lit) // len(lit) for i in range(3))
                cells.append({"cell": ci, "rgb": avg,
                              "type": classify(avg)})
        out.append({"slot": slot, "detriments": cells})
    return out


def classify(rgb) -> str | None:
    """Nearest calibrated cure color, or None until CURE_COLORS is filled."""
    if not CURE_COLORS:
        return None
    best, bd = None, 1e9
    for name, ref in CURE_COLORS.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if d < bd:
            best, bd = name, d
    return best


def main() -> None:
    for m in read_detriments():
        if m["detriments"]:
            for d in m["detriments"]:
                print(f"slot {m['slot']} cell {d['cell']}: detriment rgb={d['rgb']} type={d['type']}")
        else:
            print(f"slot {m['slot']}: clean")


if __name__ == "__main__":
    main()
