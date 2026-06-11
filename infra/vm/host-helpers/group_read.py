#!/usr/bin/env python3
"""Self-locating GROUP HP/power reader (color-scan, no eyeballed coords).

Reads every member frame in the stacked group window (left column). Geometry is
derived, not hardcoded-magic: each slot's frame sits at a fixed 75px pitch, and
within a slot we still FIND the exact bar row by color (so a 1-2px UI drift
doesn't break it). fill% = bright pixels across the fixed track / track width;
the depleted span goes dark and drops out, so partial bars read correctly.

Measured live from a 2-member group (Jenskin slot0 + Foxyman slot1):
  track x = 33..139 (w 106), HP row y=120+75*slot, power row y=128+75*slot.

A slot whose HP/power rows have too few colored pixels is reported absent (empty
group slot) rather than 0% — distinguishes "no member" from "dead member".
Run: python3 group_read.py   (folds into agent/capture.py for the heal loop).
"""
from __future__ import annotations
import re
import subprocess

DOM = "iksar_buddy"
PPM, PNG = "/tmp/grp.ppm", "/tmp/grp.png"

X0, X1 = 33, 139          # fixed bar track (same for HP + power)
HP_BASE, PWR_BASE = 120, 128
PITCH = 75
SLOTS = 6
SEARCH = 4                # +/- rows to hunt for the true bar row within a slot


def grab_column() -> dict[tuple[int, int], tuple[int, int, int]]:
    """Pull the whole left member-frame column once (one magick call)."""
    subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM],
                   capture_output=True)
    top, h = HP_BASE - SEARCH, PITCH * SLOTS
    r = subprocess.run(["magick", PPM, "-crop", f"{X1 - X0}x{h}+{X0}+{top}", "+repage", "txt:-"],
                       capture_output=True, text=True)
    pix = {}
    for line in r.stdout.splitlines():
        m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", line)
        if m:
            x, y, v = int(m.group(1)), int(m.group(2)), int(m.group(3), 16)
            pix[(x + X0, y + top)] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return pix


HP_PWR_GAP = PWR_BASE - HP_BASE       # power bar sits this many px below HP (8)

def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90   # lit bar vs dark-empty track


def fill_pct(pix, y) -> int:
    """fill% = bright pixels across the fixed track at row y. Hue-agnostic, so it
    counts the FILLED span whether it's green/yellow/red (EQ2 recolors HP bars as
    they drop). The empty span is dark (black backdrop) and drops out."""
    width = X1 - X0
    filled = sum(1 for x in range(X0, X1) if is_bright(pix.get((x, y), (0, 0, 0))))
    return round(100 * filled / width)


def find_power_row(pix, y_hint) -> int | None:
    """Locate the slot's POWER bar row by blue. Power is hue-STABLE (never
    recolors), so it's the reliable anchor even when a member is near death and
    their HP bar has gone red. HP row is then a fixed offset above it."""
    best_y, best_n = None, 0
    for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
        n = sum(1 for x in range(X0, X1) if is_blue(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def read_group(pix=None) -> list[dict]:
    if pix is None:
        pix = grab_column()
    out = []
    for slot in range(SLOTS):
        pwr_y = find_power_row(pix, PWR_BASE + PITCH * slot)
        if pwr_y is None:                       # no blue power bar => empty slot
            out.append({"slot": slot, "present": False})
            continue
        hp_y = pwr_y - HP_PWR_GAP               # HP is the fixed-offset row above
        hp = fill_pct(pix, hp_y)
        out.append({"slot": slot, "present": True, "hp": hp,
                    "power": fill_pct(pix, pwr_y), "dead": hp <= 1})
    return out


def main() -> None:
    for m in read_group():
        if not m["present"]:
            print(f"slot {m['slot']}: --")
        else:
            print(f"slot {m['slot']}: HP {m['hp']}%  PWR {m['power']}%"
                  + ("  DEAD" if m["dead"] else ""))


if __name__ == "__main__":
    main()
