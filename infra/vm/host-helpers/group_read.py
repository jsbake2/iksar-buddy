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


def is_green(c): r, g, b = c; return g > 85 and g > r + 20 and g > b + 20
def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90


def read_bar(pix, pred, y_hint) -> tuple[int, int] | None:
    """Find the strongest `pred` row near y_hint, then fill% across the fixed
    track. Returns (y, fill%) or None if the row never has enough color."""
    width = X1 - X0
    best_y, best_n = None, 0
    for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
        n = sum(1 for x in range(X0, X1) if pred(pix.get((x, y), (0, 0, 0))))
        if n > best_n:
            best_y, best_n = y, n
    if best_y is None or best_n < 20:
        return None
    filled = sum(1 for x in range(X0, X1) if is_bright(pix.get((x, best_y), (0, 0, 0))))
    return best_y, round(100 * filled / width)


def read_group(pix=None) -> list[dict]:
    if pix is None:
        pix = grab_column()
    out = []
    for slot in range(SLOTS):
        hp = read_bar(pix, is_green, HP_BASE + PITCH * slot)
        pw = read_bar(pix, is_blue, PWR_BASE + PITCH * slot)
        if hp is None and pw is None:
            out.append({"slot": slot, "present": False})
        else:
            out.append({"slot": slot, "present": True,
                        "hp": hp[1] if hp else None,
                        "power": pw[1] if pw else None,
                        "dead": hp is not None and hp[1] <= 1})
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
