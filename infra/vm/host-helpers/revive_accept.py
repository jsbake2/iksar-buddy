#!/usr/bin/env python3
"""Self-locating REVIVE accept. When Jenskin is dead and another player casts a
rez on her, EQ2 shows a consent prompt -- OCR the screen, confirm it's the cast
offer, click the Yes button's center. Host-side OCR+click, NOT a keybind.

Validated prompt wording (2026-06-12 capture):
    "<Caster> would like to cast '<Spell>' on you. Do you accept?"
    "Time remaining: NN seconds"      [ Yes ]   [ No ]
Note it does NOT contain "revive"/"resurrect" -- the spell name varies
('Spirit Guide' here). So the gate keys on the cast-consent structure
("...cast... Do you accept?"), with "resurrect" kept as an alternate.

The accept button is "Yes". We NEVER click "Revive": that is the death
window's respawn-at-spawnpoint button (wrong action -- it forfeits the rez).
One-shot; run it on a poll while dead. Run: python3 revive_accept.py [--dry]
"""
import csv, io, os, re, subprocess, sys, time

DOM = "iksar_buddy"
# The Yes button only OCRs cleanly on some frames (the countdown redraws over it),
# so one screenshot can miss it. Retry a handful of times within one invocation
# so a single dashboard "accept revive" press reliably lands the click.
RETRIES = 8
RETRY_GAP_S = 0.4
PPM, PNG, OCRP = "/tmp/rv.ppm", "/tmp/rv.png", "/tmp/rv_o.png"
# Accept-button labels, in preference order. "Revive" is deliberately excluded.
ACCEPT_WORDS = [r"Yes", r"Accept"]


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)


def is_offer(text: str) -> bool:
    """True if the screen shows an incoming rez/beneficial-cast consent prompt."""
    t = text.lower()
    if "resurrect" in t:
        return True
    # "<name> would like to cast '<spell>' on you. Do you accept?"
    if "accept" in t and "cast" in t:
        return True
    return False


def ocr_words():
    sh("sudo", "-n", "virsh", "-c", "qemu:///system", "screenshot", DOM, PPM)
    sh("magick", PPM, PNG)
    sh("magick", PNG, "-colorspace", "Gray", "-threshold", "55%", "-negate", OCRP)
    r = sh("tesseract", OCRP, "stdout", "--psm", "11", "tsv")
    out = []
    for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t"):
        try:
            if float(row["conf"]) > 30 and len(row["text"].strip()) > 1:
                out.append((row["text"].strip(), int(row["left"]), int(row["top"]),
                            int(row["width"]), int(row["height"])))
        except (ValueError, KeyError):
            pass
    return out


def detect():
    """One OCR pass -> (is_offer, sorted accept-button words)."""
    words = ocr_words()
    allt = " ".join(w[0] for w in words)
    offer = is_offer(allt)
    # Match accept buttons by exact label (so body text like "accept?" or the
    # "Revive" respawn button can never be picked). Prefer "Yes" over "Accept".
    accepts = [w for w in words if any(re.fullmatch(p, w[0], re.I) for p in ACCEPT_WORDS)]
    accepts.sort(key=lambda w: 0 if re.fullmatch(r"Yes", w[0], re.I) else 1)
    return offer, accepts


def main():
    dry = "--dry" in sys.argv
    saw_offer = False
    for attempt in range(1, RETRIES + 1):
        offer, accepts = detect()
        saw_offer = saw_offer or offer
        print(f"try {attempt}: offer={offer} buttons={[(w[0], w[1], w[2]) for w in accepts]}")
        if offer and accepts:
            w = accepts[0]
            cx, cy = w[1] + w[3] // 2, w[2] + w[4] // 2
            if dry:
                print(f"[dry] would click {w[0]} @ {cx},{cy}")
                return
            sh("python3", os.path.expanduser("~/ib-build/gexec.py"),
               "Enable-ScheduledTask -TaskName ibgclick -ErrorAction SilentlyContinue | Out-Null; "
               f"Set-Content C:\\ib\\click.txt '{cx} {cy}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")
            print(f"CLICKED {w[0]} @ {cx},{cy}")
            return
        if attempt < RETRIES:
            time.sleep(RETRY_GAP_S)
    print("no action: " + ("offer seen but Yes never OCR'd" if saw_offer
                            else "no rez-consent offer on screen"))


if __name__ == "__main__":
    main()
