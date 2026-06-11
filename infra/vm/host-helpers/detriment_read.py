#!/usr/bin/env python3
"""Per-member detriment (curable effect) detector for the group window.

A Defiler's other job is curing. Each member frame has a row of 5 detriment
cells (the 5 categories: trauma/arcane/noxious/elemental/curse) under the bars;
a hostile effect lights one up. We detect presence by sampling each cell's
INTERIOR (not its tan border) against the black window backdrop: empty cell
interior = near-black, filled = a bright icon.

Geometry (derived live, anchored on the hue-stable blue power bar so it tracks
the frame even when HP has gone red):
  detriment row y-center = power_y + ROW_DY
  5 cell centers at CELL_XC, sample a 12px interior box at each.

CURE POLICY (owner, current level): a single GENERIC cure clears any detriment
type, so PRESENCE is the whole signal -> any lit cell on a member => cure that
member. Type/cell index is NOT acted on yet; we still report it because the
future "death curse" (a higher-level type cured separately) will key off it.
No color->type map is needed until then. Run: python3 detriment_read.py
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


# UNCURABLE effects that still light a detriment cell (must NOT trigger a cure,
# or the heal loop spins forever casting on something it can't clear). Revive
# sickness after death is the known one -- it looks like a curse but won't cure.
# Matched by nearest-color within IGNORE_TOL; fill the RGB from a capture pass.
IGNORE_SIGNATURES: dict[str, tuple[int, int, int]] = {
    # Red-cross icon on a dark purple bg, appears in a detriment cell after a
    # death/revive. Captured live (avg interior RGB); distinctly DARK vs the
    # bright active curses seen so far (e.g. pink curse ~(210,86,144)), which the
    # 40-tol cleanly separates. If a real dark detriment ever collides, upgrade
    # to icon-template matching (also needed for the future "death curse").
    "revive_sickness": (103, 26, 61),
}
IGNORE_TOL = 40   # per-channel-ish distance (sqrt of sum-sq) for an ignore match


def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 120   # lit icon vs black backdrop


def is_ignored(rgb) -> str | None:
    for name, ref in IGNORE_SIGNATURES.items():
        if sum((a - b) ** 2 for a, b in zip(rgb, ref)) <= IGNORE_TOL ** 2:
            return name
    return None


def power_row(pix, slot) -> int | None:
    y_hint = PWR_BASE + PITCH * slot
    best_y, best_n = None, 0
    for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
        n = sum(1 for x in range(X0, X1) if is_blue(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def read_detriments(pix=None) -> list[dict]:
    """Per present member: lit detriment cells, each flagged curable or ignored.
    `cure` = True if the member has at least one CURABLE lit cell (the heal loop's
    trigger). Ignored effects (revive sickness) are reported but don't set cure."""
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
                ignored = is_ignored(avg)
                cells.append({"cell": ci, "rgb": avg, "ignored": ignored})
        cure = any(c["ignored"] is None for c in cells)
        out.append({"slot": slot, "detriments": cells, "cure": cure})
    return out


def main() -> None:
    for m in read_detriments():
        if not m["detriments"]:
            print(f"slot {m['slot']}: clean")
            continue
        for d in m["detriments"]:
            tag = f"IGNORE({d['ignored']})" if d["ignored"] else "CURABLE"
            print(f"slot {m['slot']} cell {d['cell']}: rgb={d['rgb']} {tag}")
        print(f"  -> cure needed: {m['cure']}")


if __name__ == "__main__":
    main()
