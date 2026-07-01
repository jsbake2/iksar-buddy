#!/usr/bin/env python3
"""Self-locating trade-window accept. OCR the screen, confirm a TRADE window is up,
find the 'Accept' word box, click its center. Gated so it never clicks a stray
Accept: requires a trade keyword ('trade'/'trading') present alongside the Accept.

NOTE: the gate keywords + button word are a first pass — validate against a real
trade-window screenshot (owner to supply) and tune GATE_WORDS / the button regex if
EQ2's trade dialog labels differ (e.g. the button reads 'Trade' or is a gold button
that needs pixel detection like quest_accept.py). Diagnostics are printed either way.
Pass --dry to OCR + report WITHOUT clicking."""
import csv, io, os, re, subprocess, sys

DOM = "iksar_buddy"; PPM = "/tmp/tr.ppm"; PNG = "/tmp/tr.png"; OCRP = "/tmp/tr_o.png"
DRY = "--dry" in sys.argv
# any one of these present => it's plausibly a trade window (tune with a screenshot)
GATE_WORDS = ("trade", "trading")
ACCEPT_RE = r"Accept"          # the accept button word (tune if EQ2 differs)


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)


sh("sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM)
sh("magick", PPM, PNG)
sh("magick", PNG, "-colorspace", "Gray", "-threshold", "55%", "-negate", OCRP)
r = sh("tesseract", OCRP, "stdout", "--psm", "11", "tsv")
words = []
for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t"):
    try:
        if float(row["conf"]) > 30 and len(row["text"].strip()) > 1:
            words.append((row["text"].strip(), int(row["left"]), int(row["top"]),
                          int(row["width"]), int(row["height"])))
    except (ValueError, KeyError):
        pass
allt = " ".join(w[0] for w in words).lower()
gate = any(g in allt for g in GATE_WORDS)
acc = [w for w in words if re.fullmatch(ACCEPT_RE, w[0], re.I)]
print("gate(trade):", gate)
print("accept words:", [(w[0], w[1], w[2]) for w in acc])
if gate and acc and not DRY:
    w = acc[0]; cx = w[1] + w[3] // 2; cy = w[2] + w[4] // 2
    sh("python3", os.path.expanduser("~/ib-build/gexec.py"),
       f"Set-Content C:\\ib\\click.txt '{cx} {cy}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")
    print(f"CLICKED Accept @ {cx},{cy}")
else:
    print("no action (gate or accept not found)" if not DRY else "dry run — no click")
