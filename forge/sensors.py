"""Host-side crafting sensors (FORGE.md §4). Pure read functions over a Guest:
durability/progress mode, power gate, begin/retry detection, chat-safety, reaction
template match, and quest-journal OCR. The LOGIC is ported from the dino; the
geometry/colors come from the craft.yaml calibration profile (placeholders until
in-game capture). Fail-safe: if a region can't be read or coords are uncalibrated,
detectors return "nothing happening" so the worker idles rather than mis-acts.

Optional deps (only needed when actually sensing a live game):
  - opencv (cv2) + numpy for reaction template matching
  - the `tesseract` binary for journal OCR (the host already has it; the healer uses it)
Both are imported/called lazily and degrade gracefully if absent.
"""
from __future__ import annotations

import difflib
import logging
import re
import subprocess
import time
from pathlib import Path

from .guest import Guest
from .recipes import parse_ocr_items

log = logging.getLogger("forge.sensors")


# ---- color helpers ----------------------------------------------------------
def matches(rgb, expected, tol: int) -> bool:
    return all(abs(int(rgb[i]) - int(expected[i])) <= tol for i in range(3))


# ---- mode / power / begin-retry (single-pixel fingerprints) -----------------
def durability_mode(guest: Guest, cfg: dict) -> str | None:
    """'progress' if the mode pixel matches the progress color, else 'durability'.
    None if the pixel can't be read."""
    d = cfg.get("durability_mode", {})
    loc = d.get("location")
    if not loc:
        return None
    rgb = guest.pixel(loc[0], loc[1])
    if rgb == (0, 0, 0):
        return None
    return "progress" if matches(rgb, d.get("progress_color", [0, 0, 0]),
                                 d.get("tolerance", 12)) else "durability"


def power_ok(guest: Guest, cfg: dict) -> bool:
    """True if power is sufficient (mana-gate pixel matches the ok color).
    Defaults to True if uncalibrated so we don't get stuck waiting forever."""
    p = cfg.get("power", {})
    loc = p.get("location")
    if not loc:
        return True
    rgb = guest.pixel(loc[0], loc[1])
    return matches(rgb, p.get("ok_color", [0, 0, 0]), p.get("tolerance", 14))


def begin_or_retry(guest: Guest, cfg: dict) -> str | None:
    """'retry' if the retry button fingerprint is present, 'begin' if the begin
    button is, else None. Retry is checked first (mid-batch repeat)."""
    for which in ("retry", "begin"):
        spec = (cfg.get(which) or {}).get("pixel")
        if not spec or not spec.get("location"):
            continue
        loc = spec["location"]
        rgb = guest.pixel(loc[0], loc[1])
        if rgb != (0, 0, 0) and matches(rgb, spec.get("color", [0, 0, 0]),
                                        spec.get("tolerance", 12)):
            return which
    return None


# ---- chat safety (the inviolable invariant, PROJECT.md §6.2) ----------------
def chat_safe(guest: Guest, cfg: dict) -> bool:
    """True only if the chat input line is CLEAR (no typed text / cursor). Counts
    bright pixels in the chat-input region; fail-closed (False) on a read error."""
    c = cfg.get("chat_input", {})
    reg = c.get("region")
    if not reg:
        return False                              # uncalibrated -> fail closed
    try:
        r = subprocess.run(["magick", guest.ppm, "-crop",
                            f"{reg['w']}x{reg['h']}+{reg['x']}+{reg['y']}", "+repage",
                            "-colorspace", "Gray", "-threshold", "60%",
                            "-format", "%[fx:mean*w*h]", "info:"],
                           capture_output=True, text=True, timeout=4)
        bright = int(float(r.stdout.strip()))
    except (ValueError, OSError, subprocess.SubprocessError):
        return False
    return bright <= c.get("bright_threshold", 25)


# ---- reaction matching (opencv, IN-MEMORY references) -----------------------
# No saved per-class template library. Instead, at the start of every craft the
# worker grabs the 3 reaction buttons FRESH from their fixed calibrated positions
# (reaction.button_regions) into memory, and we match the active-reaction region
# against those. This works for any class — even a quest craft for an undefined
# class — because the reference is captured live from whatever's on screen.
def _cv2():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError:
        log.info("opencv not installed — reaction matching disabled (install later)")
        return None, None


def capture_buttons(guest: Guest, cfg: dict) -> list:
    """Grab the 3 reference reaction-button images NOW (in memory) from their fixed
    positions. Returns [cv2 BGR array, ...] in counter order, or [] if cv2/regions
    are missing. Called by the worker at each craft start."""
    cv2, np = _cv2()
    if cv2 is None:
        return []
    boxes = (cfg.get("reaction", {}) or {}).get("button_regions") or []
    if not boxes or not guest.grab():
        return []
    out = []
    for b in boxes:
        png = guest.region_png(int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"]))
        if not png:
            out.append(None)
            continue
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        out.append(arr)
    return out


def reaction_event(guest: Guest, cfg: dict, templates: list) -> int | None:
    """Match the active-reaction watch region against the in-memory reference button
    templates. Returns the counter NUMBER (1-based) of the best match, or None."""
    if not templates:
        return None
    reg = (cfg.get("reaction", {}) or {}).get("region")
    if not reg:
        return None
    png = guest.grab_region_png(int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
    if not png:
        return None
    cv2, np = _cv2()
    if cv2 is None:
        return None
    try:
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None
        thresh = float((cfg.get("reaction", {}) or {}).get("confidence", 0.80))
        best, best_val = None, 0.0
        for i, tmpl in enumerate(templates):
            if tmpl is None or tmpl.shape[0] > arr.shape[0] or tmpl.shape[1] > arr.shape[1]:
                continue
            res = cv2.matchTemplate(arr, tmpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            if mx > thresh and mx > best_val:
                best, best_val = i + 1, mx
        return best
    except Exception as e:                        # never let sensing crash the loop
        log.debug("reaction match error: %s", e)
        return None


# ---- char-select: find a character row by name (OCR-and-click) --------------
def _ocr_words(guest: Guest, region: dict) -> list[dict]:
    """OCR a region -> [{text,x,y,w,h,conf}] in GUEST coords. [] on failure."""
    r = region or {}
    if not r or not guest.grab():
        return []
    try:
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop", f"{r['w']}x{r['h']}+{r['x']}+{r['y']}",
             "+repage", "-colorspace", "Gray", "-threshold", "50%", "png:-"],
            capture_output=True, timeout=6).stdout
        if not pre:
            return []
        out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                             input=pre, capture_output=True, timeout=10).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("char-select OCR failed: %s", e)
        return []
    words = []
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12:
            continue
        try:
            conf = float(f[10]); x, y, w, h = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        except ValueError:
            continue
        text = f[11].strip()
        if conf < 35 or len(text) < 2:
            continue
        words.append({"text": text, "x": r["x"] + x, "y": r["y"] + y,
                      "w": w, "h": h, "conf": conf})
    return words


def find_character(guest: Guest, cfg: dict, target: str) -> tuple[int, int] | None:
    """Click point for the `target` character row in the char-select list.

    Names repeat across servers (e.g. two Robskins) — the live one is on the
    owner's server (cfg char_select.server, e.g. 'Wuoshi'), which the EQ2 list
    sorts toward the BOTTOM. So: of the rows whose name ~matches target, prefer
    those with the server name detected just below; among those, pick the
    BOTTOM-MOST (the owner's hint). Returns (x,y) in guest coords or None.
    """
    cs = cfg.get("char_select", {})
    words = _ocr_words(guest, cs.get("list_region", {}))
    if not words:
        return None
    server = (cs.get("server") or "").lower()
    tl = target.lower()
    matches = [w for w in words if difflib.SequenceMatcher(None, w["text"].lower(), tl).ratio() >= 0.6]
    if not matches:
        return None

    def has_server_below(w):
        if not server:
            return False
        for o in words:
            if abs(o["x"] - w["x"]) < 60 and 0 < (o["y"] - w["y"]) < 45 \
                    and difflib.SequenceMatcher(None, o["text"].lower(), server).ratio() >= 0.55:
                return True
        return False

    preferred = [w for w in matches if has_server_below(w)] or matches
    pick = max(preferred, key=lambda w: w["y"])    # bottom-most = the Wuoshi one
    # CLICK THE ROW-LEFT (portrait), not the name text — the name column isn't the
    # selectable hotspot (validated live: x~100 selects, x~182 does nothing).
    row_x = int(cs.get("row_click_x", 100))
    return (row_x, pick["y"] + pick["h"] // 2)


def _alpha(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def panel_name(guest: Guest, region: dict) -> str:
    """OCR the selected-character name shown in the char-select detail panel (above
    Play). Light gold-on-textured text, so: grayscale + upscale + normalize (no hard
    threshold). Returns lowercased letters only ('' on failure)."""
    r = region or {}
    if not r or not guest.grab():
        return ""
    try:
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop", f"{r['w']}x{r['h']}+{r['x']}+{r['y']}",
             "+repage", "-colorspace", "Gray", "-resize", "300%", "-normalize", "png:-"],
            capture_output=True, timeout=6).stdout
        if not pre:
            return ""
        out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "7"],
                             input=pre, capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return _alpha(out.decode(errors="replace"))


def _stable_panel(guest: Guest, region: dict) -> str:
    """Read the detail-panel name twice ~0.4s apart and return it only if the two
    reads agree (the panel lags the click; this rejects a mid-update frame)."""
    a = panel_name(guest, region)
    time.sleep(0.4)
    b = panel_name(guest, region)
    return b if a == b else b or a


def select_character(guest: Guest, cfg: dict, target: str,
                     log=lambda _m: None, play: bool = True,
                     attempts: int = 8) -> bool:
    """Closed-loop char-select pick used by BOTH login and forge (FORGE.md §5.5).

    The list has no portrait column (centered text) and the click coordinate space is
    offset/noisy vs the screenshot, so an open-loop click picked the wrong toon. So:
    click the target row, READ the detail-panel name to VALIDATE, and steer the click
    Y proportionally toward the target from whoever we actually hit. Confirmation is
    SUBSTRING-only (never fuzzy — 'Croolst'/'Croalst' differ by one letter), so the
    wrong twin can't be confirmed. Play is pressed ONLY after the panel confirms
    `target`; an unconfirmed selection returns False and never Plays.
    """
    cs = cfg.get("char_select", cfg) or {}
    list_region = cs.get("list_region", {})
    name_region = cs.get("name_region", {"x": 1610, "y": 772, "w": 250, "h": 30})
    offset = int(cs.get("row_click_offset_y", 0))         # clicking AT the name selects it
    settle = float(cs.get("select_settle_s", 2.5))        # panel lags; short reads are stale
    play_click = cs.get("play_click")
    ctl = _alpha(target)
    if len(ctl) < 5:
        log(f"char-select: target '{target}' too short to disambiguate"); return False

    # Build a name->position map by MERGING several grabs (per-frame list OCR is flaky,
    # the names garble — 'Croalst' reads as 'crjst'/'croalstg'). Keep ALL candidate
    # positions per name so a single clean read locates the row.
    def _strong(c: str, k: str) -> bool:
        # exact, or length-guarded substring — catches same-name OCR variants
        # ('jenskin'/'jensking', 'croalst'/'croalstg') WITHOUT matching the near-twin
        # 'croolst' vs 'croalst' (neither is a substring of the other).
        return c == k or (len(k) >= 5 and (c in k or k in c) and abs(len(k) - len(c)) <= 2)

    cands: dict[str, list[tuple[int, int]]] = {}
    for _ in range(8):
        for w in _ocr_words(guest, list_region):
            a = _alpha(w["text"])
            if len(a) >= 4:
                cands.setdefault(a, []).append((w["x"] + w["w"] // 2, w["y"] + w["h"] // 2))
        if any(_strong(ctl, k) for k in cands):            # got a clean read of the target
            break
        time.sleep(0.8)

    def _pos(name: str):
        c = _alpha(name)
        # Prefer strong (exact/substring) matches; only if NONE exist fall back to a
        # fuzzy floor. This is what makes single-letter OCR errors (jenskin->jenrkin,
        # ratio 0.857) recoverable WITHOUT averaging in the croolst/croalst twin (also
        # 0.857): when a clean read is present it wins; when not, fuzzy still locates it.
        pts = [p for k, v in cands.items() if _strong(c, k) for p in v]
        if not pts:
            pts = [p for k, v in cands.items()
                   if difflib.SequenceMatcher(None, k, c).ratio() >= 0.85 for p in v]
        if not pts:
            return None
        pts.sort(key=lambda p: p[1])
        return pts[len(pts) // 2]                          # median (robust to stray reads)

    tpos = _pos(target)
    if not tpos:
        log(f"char-select: '{target}' not found in list ({sorted(cands)})"); return False
    tx, ty = tpos
    lo, hi = ty - 55, ty + 55                              # never click into the void

    guess_y = ty + offset
    for attempt in range(attempts):
        guess_y = max(lo, min(hi, guess_y))
        guest.click(tx, guess_y)
        time.sleep(settle)                                 # selection + panel update
        sel = _stable_panel(guest, name_region)
        if ctl in sel:
            log(f"confirmed {target} (panel '{sel}')")
            if play and play_click:
                guest.click(int(play_click[0]), int(play_click[1]))
            return True
        # steer: map the panel name back to a list row to learn the click error
        hitpos = _pos(sel) if len(sel) >= 5 else None
        if hitpos and abs(hitpos[1] - ty) > 4:             # proportional steer (gain 1)
            err = hitpos[1] - ty                           # +ve: selected too low
            log(f"panel '{sel}' -> y{hitpos[1]} err={err}; click_y {guess_y}->{guess_y - err}")
            guess_y -= err
        else:
            log(f"panel '{sel}' unresolved; nudge up")
            guess_y -= 14
        time.sleep(0.3)
    log(f"char-select: could NOT confirm {target} — not pressing Play")
    return False


# ---- journal OCR (writs) ----------------------------------------------------
def ocr_journal(guest: Guest, cfg: dict, trade_class: str = "") -> dict[str, int]:
    """Screenshot the journal region, preprocess with magick, OCR with tesseract,
    parse -> {recipe: count}. Returns {} if the tools/region are unavailable."""
    j = cfg.get("journal_ocr", {})
    reg = j.get("region")
    if not reg or not guest.grab():
        return {}
    scale = j.get("scale_percent", 150)
    psm = j.get("tesseract_psm", 4)
    try:
        # magick: crop -> upscale -> grayscale -> equalize -> blur -> otsu -> PNG
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop", f"{reg['w']}x{reg['h']}+{reg['x']}+{reg['y']}",
             "+repage", "-resize", f"{scale}%", "-colorspace", "Gray",
             "-equalize", "-blur", "0x0.5", "-threshold", "55%", "png:-"],
            capture_output=True, timeout=6).stdout
        if not pre:
            return {}
        ocr = subprocess.run(["tesseract", "stdin", "stdout", "--psm", str(psm)],
                             input=pre, capture_output=True, timeout=10)
        text = ocr.stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("journal OCR failed (magick/tesseract): %s", e)
        return {}
    items = parse_ocr_items(text, trade_class)
    log.info("journal OCR: %d items", len(items))
    return items
