#!/usr/bin/env python3
"""One-shot quest accept — SELF-LOCATING, no hardcoded coordinates.

Run once; it either accepts/declines a quest-offer dialog (wherever it is on
screen) or exits if there isn't one. No continuous polling, no grids.

How it finds things:
  - OCR (threshold+negate -> tesseract TSV with word boxes) reads the quest
    TITLE + NAME + DESCRIPTION reliably and tells us exactly where the dialog is.
    -> used for the allow/deny POLICY GATE and to bound the dialog.
  - The gold Accept/Decline BUTTONS don't OCR well, so they're found by GOLD-
    PIXEL detection: scan rows below the dialog text for the first row with two
    gold clusters (the two buttons); the left cluster is Accept, the right is
    Decline. Click the chosen one with a native (Event-mode) click in the guest.

FAIL-SAFE: empty OCR / no quest title / no allowlist match => never accepts.
"""
from __future__ import annotations
import csv
import io
import os
import re
import subprocess
import sys

DOM = "iksar_buddy"
PPM, PNG, OCRP = "/tmp/qa.ppm", "/tmp/qa.png", "/tmp/qa_ocr.png"
GEXEC = os.path.expanduser("~/ib-build/gexec.py")

# --- policy (OWNER-OWNED) --------------------------------------------------
# Matched case-insensitively against the OCR'd "title name level description".
# Two modes:
#   DEFAULT_ACTION="ignore"  -> ALLOWLIST mode: accept only ACCEPT_PATTERNS matches
#                               (safest; leave everything else).
#   DEFAULT_ACTION="accept"  -> accept everything EXCEPT DECLINE_PATTERNS
#                               (handy while leveling; denylist still wins).
# Verified live: matched "Seaside" -> accepted "Seaside Stew".
ACCEPT_PATTERNS = [r"Norrath", r"Tradeskill", r"Writ", r"Collection"]   # allowlist
DECLINE_PATTERNS = [r"\bPvP\b", r"Battlegrounds", r"Duel", r"betray"]    # denylist (always wins)
DEFAULT_ACTION = "ignore"


def sh(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(a), capture_output=True, text=True)


def grab() -> None:
    sh("sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM)
    sh("magick", PPM, PNG)


def ocr_words() -> list[tuple[str, int, int, int, int]]:
    """(text, left, top, w, h) for each readable word on the whole screen."""
    sh("magick", PNG, "-colorspace", "Gray", "-threshold", "45%", "-negate", OCRP)
    r = sh("tesseract", OCRP, "stdout", "--psm", "11", "tsv")
    words = []
    for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t"):
        try:
            if float(row["conf"]) > 35 and len(row["text"].strip()) > 1:
                words.append((row["text"].strip(), int(row["left"]), int(row["top"]),
                              int(row["width"]), int(row["height"])))
        except (ValueError, KeyError):
            pass
    return words


def row_gold(x0: int, x1: int, y: int) -> list[int]:
    """X positions of gold (EQ2 button) pixels along a horizontal line."""
    r = sh("magick", PNG, "-crop", f"{x1 - x0}x1+{x0}+{y}", "+repage", "txt:-")
    xs = []
    i = 0
    for line in r.stdout.splitlines():
        m = re.search(r"#([0-9A-Fa-f]{6})", line)
        if m:
            v = int(m.group(1), 16)
            r8, g8, b8 = (v >> 16) & 255, (v >> 8) & 255, v & 255
            if r8 > 130 and r8 - g8 > 25 and g8 - b8 > 12:
                xs.append(x0 + i)
            i += 1
    return xs


def clusters(xs: list[int], gap: int = 22) -> list[tuple[int, int]]:
    """Group sorted X positions into (start,end) clusters separated by > gap."""
    if not xs:
        return []
    out, s, p = [], xs[0], xs[0]
    for x in xs[1:]:
        if x - p > gap:
            out.append((s, p)); s = x
        p = x
    out.append((s, p))
    return [c for c in out if c[1] - c[0] >= 12]   # drop specks; keep button-width runs


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


def main() -> None:
    dry = "--dry" in sys.argv
    # --accept = MANUAL mode (the dashboard button). The owner explicitly chose to
    # accept whatever quest is on screen, so bypass the allow/deny policy gate.
    # The policy only governs autonomous use (none wired right now).
    force_accept = "--accept" in sys.argv
    grab()
    words = ocr_words()

    # locate the dialog by its "Quest" title (with "New" just left, same line)
    titles = [w for w in words if re.fullmatch(r"Quest!?", w[0])
              and any(o[0] == "New" and abs(o[2] - w[2]) < 12 and 0 < w[1] - o[1] < 120 for o in words)]
    if not titles:
        print("no quest dialog on screen")
        return
    tx, ty = titles[0][1], titles[0][2]

    # dialog body = words in a column band starting under the title
    band = [w for w in words if abs(w[1] - tx) < 280 and ty - 5 <= w[2] < ty + 430]
    left = min(w[1] for w in band) - 6
    gate_text = " ".join(w[0] for w in sorted(band, key=lambda w: (w[2], w[1])))
    last_y = max(w[2] + w[4] for w in band)

    action, rule = ("accept", "manual button") if force_accept else decide(gate_text)

    # find the Accept/Decline button row by gold detection below the text
    bx_accept = bx_decline = by = None
    for y in range(last_y + 6, last_y + 170, 2):
        cl = clusters(row_gold(left, left + 280, y))
        if len(cl) >= 2:                       # two gold clusters = the two buttons
            by = y
            bx_accept = (cl[0][0] + cl[0][1]) // 2
            bx_decline = (cl[1][0] + cl[1][1]) // 2
            break

    print(f"[quest] name/desc = {gate_text[:80]!r}")
    print(f"        decision = {action} (rule={rule})")
    print(f"        dialog_left={left} last_text_y={last_y} "
          f"accept=({bx_accept},{by}) decline=({bx_decline},{by})")

    if dry or by is None:
        if by is None:
            print("        (!) could not locate the gold button row")
        return
    if action == "accept":
        click(bx_accept, by); print(f"        CLICKED Accept @ {bx_accept},{by}")
    elif action == "decline":
        click(bx_decline, by); print(f"        CLICKED Decline @ {bx_decline},{by}")
    else:
        print("        ignored (no allowlist match)")


if __name__ == "__main__":
    main()
