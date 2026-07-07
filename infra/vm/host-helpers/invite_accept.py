#!/usr/bin/env python3
"""Self-locating group-invite accept. OCR the screen, confirm it's a group
invite, find the 'Accept' word box, click its center. Gated so it never clicks a
stray Accept.

The gate keys on the tokens EQ2's group-invite popup contains. It was originally
a guess ("invited" + "group"); if the real wording differs the gate stays False
and nothing clicks. So on a failed gate this now DUMPS the joined OCR text as its
final line -> telemetry shows exactly what was on screen, and the owner can hand
the real wording back to tune INVITE_TOKENS. Retries a few frames like
revive_accept.py, since a single screenshot can miss the button mid-redraw.

Run: python3 invite_accept.py [--dry]
"""
import csv, io, os, re, subprocess, sys, time

DOM = "iksar_buddy"
PPM, PNG, OCRP = "/tmp/iv.ppm", "/tmp/iv.png", "/tmp/iv_o.png"
RETRIES = 6
RETRY_GAP_S = 0.4
# Gate: ANY of these token-groups present => it's a group invite. Each inner list
# is an AND set. Verified 2026-07-07 against the real popup, which OCRs as
# "<name> has invited you to join a group" -> the ["invited","group"] set. Kept
# tight on purpose (the "Accept" button must ALSO be present to click, but a loose
# gate like ["join","group"] false-matches the post-join mentoring chat line).
INVITE_TOKENS = [["invited", "group"]]


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)


def ocr_words():
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
    return words


def gate_ok(allt: str) -> bool:
    return any(all(tok in allt for tok in grp) for grp in INVITE_TOKENS)


def main():
    dry = "--dry" in sys.argv
    last_text = ""
    for attempt in range(1, RETRIES + 1):
        words = ocr_words()
        allt = " ".join(w[0] for w in words).lower()
        last_text = allt
        gate = gate_ok(allt)
        acc = [w for w in words if re.fullmatch(r"Accept", w[0], re.I)]
        print(f"try {attempt}: gate={gate} accept={[(w[0], w[1], w[2]) for w in acc]}")
        if gate and acc:
            w = acc[0]
            cx, cy = w[1] + w[3] // 2, w[2] + w[4] // 2
            if dry:
                print(f"[dry] would click Accept @ {cx},{cy}")
                return
            sh("python3", os.path.expanduser("~/ib-build/gexec.py"),
               "Enable-ScheduledTask -TaskName ibgclick -ErrorAction SilentlyContinue | Out-Null; "
               f"Set-Content C:\\ib\\click.txt '{cx} {cy}' -NoNewline; Start-ScheduledTask -TaskName ibgclick")
            print(f"CLICKED Accept @ {cx},{cy}")
            return
        if attempt < RETRIES:
            time.sleep(RETRY_GAP_S)
    # Failed. Make the LAST line diagnostic so telemetry captures what OCR saw.
    print(f"no action -- OCR saw: {last_text[:120]!r}")


if __name__ == "__main__":
    main()
