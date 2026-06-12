#!/usr/bin/env python3
"""Self-locating REVIVE accept. When Jenskin is dead and another player offers a
revive, EQ2 shows an accept prompt -- OCR the screen, confirm it's a revive offer,
find the Accept (or Yes) button, click its center. Host-side OCR+click, NOT a
keybind. Same model as invite_accept.py.

Gate: the screen text mentions revive/resurrect AND an accept-style button is
present, so it never clicks a stray Accept. One-shot; run it on a poll while dead.

NEEDS VALIDATION: the exact revive-prompt wording + button label vary -- run the
capture watcher (revive_watch) when Jenskin actually gets a rez offered to confirm
ACCEPT_WORDS / the gate, then tighten. Run: python3 revive_accept.py [--dry]
"""
import csv, io, os, re, subprocess, sys

DOM = "iksar_buddy"
PPM, PNG, OCRP = "/tmp/rv.ppm", "/tmp/rv.png", "/tmp/rv_o.png"
# Words that mark a revive OFFER (any one is enough, case-insensitive).
REVIVE_WORDS = [r"revive", r"resurrect", r"reviv\w*"]
# Acceptable button labels for accepting the revive.
ACCEPT_WORDS = [r"Accept", r"Yes", r"Revive"]


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)


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


def main():
    dry = "--dry" in sys.argv
    words = ocr_words()
    allt = " ".join(w[0] for w in words).lower()
    is_revive = any(re.search(p, allt) for p in REVIVE_WORDS)
    accepts = [w for w in words if any(re.fullmatch(p, w[0], re.I) for p in ACCEPT_WORDS)]
    print(f"gate(revive offer): {is_revive}")
    print(f"accept buttons: {[(w[0], w[1], w[2]) for w in accepts]}")
    if not (is_revive and accepts):
        print("no action (not a revive offer / no accept button)")
        return
    # prefer an "Accept"/"Yes" over the word "Revive" (which may also be body text)
    accepts.sort(key=lambda w: 0 if re.fullmatch(r"Accept|Yes", w[0], re.I) else 1)
    w = accepts[0]
    cx, cy = w[1] + w[3] // 2, w[2] + w[4] // 2
    if dry:
        print(f"[dry] would click {w[0]} @ {cx},{cy}")
        return
    sh("python3", os.path.expanduser("~/ib-build/gexec.py"),
       f"Set-Content C:\\ib\\click.txt '{cx} {cy}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")
    print(f"CLICKED {w[0]} @ {cx},{cy}")


if __name__ == "__main__":
    main()
