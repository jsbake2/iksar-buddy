#!/usr/bin/env python3
"""Quest watcher — OCR-gated auto-accept for EQ2 quest-offer dialogs.

The dialog's top-left is FIXED (owner anchors it once), so the name/description
sit at known offsets. Accept/Decline are left-aligned at a fixed X but their Y
DRIFTS with content (longer descriptions / more reward lines make a taller
dialog) -> we SCAN the button column for the gold button row instead of
hardcoding Y.

Pipeline (per poll):
  virsh screenshot -> OCR name (white-on-maroon: threshold+negate) + description
  (dark-on-parchment: plain) -> allow/deny policy -> locate buttons by scanning
  the gold column -> Event-click via the in-guest AHK helper (ibgclick).

FAIL-SAFE: empty/garbage OCR or no allowlist match => never accept.

Prototype: runs host-side (tesseract + virsh + the AHK click task live here).
Folds into the in-guest agent later (PROJECT.md §4 OCR sensor).
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import time

DOM = "iksar_buddy"
PPM, PNG, CROP = "/tmp/qw.ppm", "/tmp/qw.png", "/tmp/qw_crop.png"
GEXEC = os.path.expanduser("~/ib-build/gexec.py")

# --- calibrated geometry (1920x1080, dialog anchored upper-left) ------------
NAME = (218, 244, 138, 36)          # x,y,w,h  name+level (white on maroon); tall to absorb drift
DESC = (212, 318, 234, 62)          # description body (dark on parchment); tall to absorb drift
ACCEPT_X, DECLINE_X = 285, 420      # button COLUMN x (fixed; left-aligned)
SCAN_Y = (440, 560)                 # scan this Y span for the button row
TITLE_FP = (260, 232)               # a maroon dialog-bg spot (present => dialog up)

# --- policy (OWNER-OWNED: edit these) --------------------------------------
# Patterns are regex, matched case-insensitively against "name + description".
# The description OCRs more cleanly than the white-on-maroon name, so prefer
# matching on a distinctive description phrase or the quest name.
# Verified working live on "Welcome to Norrath" (matched r"Norrath" / r"Tayil").
ACCEPT_PATTERNS = [r"Norrath", r"Tradeskill", r"Writ", r"Collection"]   # allowlist -> accept
DECLINE_PATTERNS = [r"\bPvP\b", r"Battlegrounds", r"Duel"]              # denylist (wins over allow)
DEFAULT_ACTION = "ignore"           # no allowlist match -> "ignore" (leave dialog) or "decline"


def sh(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(a), capture_output=True, text=True)


def grab() -> None:
    sh("sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM)
    sh("convert", PPM, PNG)


def px(x: int, y: int) -> tuple[int, int, int]:
    r = sh("convert", PNG, "-format", f"%[pixel:p{{{x},{y}}}]", "info:")
    m = re.search(r"(\d+),(\d+),(\d+)", r.stdout)
    return tuple(int(v) for v in m.groups()) if m else (0, 0, 0)


def column(x: int, y0: int, y1: int) -> list[tuple[int, int, int]]:
    """RGB of a 1px-wide vertical strip in one ImageMagick call (fast)."""
    r = sh("convert", PNG, "-crop", f"1x{y1 - y0}+{x}+{y0}", "+repage", "txt:-")
    out = []
    for line in r.stdout.splitlines():
        m = re.search(r"#([0-9A-Fa-f]{6})", line)
        if m:
            v = int(m.group(1), 16)
            out.append(((v >> 16) & 255, (v >> 8) & 255, v & 255))
    return out


def is_gold(c) -> bool:
    r, g, b = c
    return r > 130 and r - g > 25 and g - b > 20


def is_maroon(c) -> bool:
    r, g, b = c
    return 20 < r < 120 and g < 35 and b < 35 and r - g > 10


def ocr(crop, invert: bool) -> str:
    x, y, w, h = crop
    a = ["convert", PNG, "-crop", f"{w}x{h}+{x}+{y}", "+repage", "-colorspace", "Gray"]
    if invert:
        a += ["-threshold", "58%", "-negate"]
    a += ["-resize", "350%", "-normalize", CROP]
    sh(*a)
    return sh("tesseract", CROP, "-", "--psm", "6").stdout.strip()


def find_button_y() -> int | None:
    """Scan the Accept column for gold; return the center Y of the LOWEST cluster
    (the Accept/Decline row is the bottom-most gold element)."""
    col = column(ACCEPT_X, *SCAN_Y)
    ys = [SCAN_Y[0] + i for i, c in enumerate(col) if is_gold(c)]
    if not ys:
        return None
    clusters, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 4:
            cur.append(y)
        else:
            clusters.append(cur)
            cur = [y]
    clusters.append(cur)
    low = clusters[-1]                 # bottom-most gold run = the button row
    return (low[0] + low[-1]) // 2


def click(x: int, y: int) -> None:
    sh("python3", GEXEC,
       f"Set-Content C:\\ib\\click.txt '{x} {y}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")


def decide(text: str) -> tuple[str, str | None]:
    for p in DECLINE_PATTERNS:
        if re.search(p, text, re.I):
            return "decline", p
    for p in ACCEPT_PATTERNS:
        if re.search(p, text, re.I):
            return "accept", p
    return DEFAULT_ACTION, None


def check(dry: bool = False) -> str | None:
    grab()
    if not is_maroon(px(*TITLE_FP)):
        return None  # no dialog -> cheap exit
    name = ocr(NAME, invert=True)
    desc = ocr(DESC, invert=False)
    if len((name + desc).strip()) < 5:
        return None  # nothing readable; don't act
    action, rule = decide(f"{name} {desc}")
    by = find_button_y()
    print(f"[quest] name={name!r}")
    print(f"        desc={desc!r}")
    print(f"        -> {action} (rule={rule}) button_y={by}")
    if dry or by is None:
        return action
    if action == "accept":
        click(ACCEPT_X, by)
        print(f"        clicked Accept @ {ACCEPT_X},{by}")
    elif action == "decline":
        click(DECLINE_X, by)
        print(f"        clicked Decline @ {DECLINE_X},{by}")
    return action


def main() -> None:
    dry = "--dry" in sys.argv
    once = "--once" in sys.argv
    print(f"quest watcher up (dry={dry} once={once})")
    while True:
        try:
            check(dry=dry)
        except Exception as e:  # never die on a transient sensor error
            print("err:", e)
        if once:
            break
        time.sleep(1.5)


if __name__ == "__main__":
    main()
