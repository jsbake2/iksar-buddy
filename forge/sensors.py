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


def progress_full(guest: Guest, cfg: dict) -> bool:
    """OCR-FREE completion: the BLUE progress bar (owner: progress=blue, durability=green)
    reaching its right end = the craft finished ('You created'). We sample the last few
    px of the bar row; mostly-blue there => progress ~100%. Reliable (no chat OCR)."""
    p = cfg.get("progress_bar")
    if not p:
        return False
    y = int(p.get("y", 277))
    x0 = int(p.get("full_x0", 873)); x1 = int(p.get("full_x1", 881))
    try:
        px = guest.crop(x0, y, max(1, x1 - x0), 1)
    except Exception:
        return False
    if not px:
        return False
    blue = p.get("blue", [40, 54, 242]); tol = int(p.get("tolerance", 70))
    n = sum(1 for c in px.values() if matches(c, blue, tol))
    return n / len(px) >= float(p.get("full_frac", 0.6))


def craft_running(guest: Guest, cfg: dict) -> bool:
    """True if the RED STOP-SIGN is showing in the art-bar's right slot — owner's signal
    that a craft is actually RUNNING. (Same slot is GREEN ↻ when done.) Used to confirm
    the start; if it's not running we click Begin again."""
    r = (cfg.get("running_detect") or {}).get("region")
    if not r:
        return False
    try:
        px = guest.crop(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"]))
    except Exception:
        return False
    if not px:
        return False
    rd = (cfg.get("running_detect") or {})
    red = rd.get("red", [147, 62, 37]); tol = int(rd.get("tolerance", 45))
    n = sum(1 for c in px.values() if matches(c, red, tol))
    return n >= int(rd.get("min_pixels", 60))


def panel_loaded(guest: Guest, cfg: dict) -> bool:
    """After selecting a recipe, the component panel should show SOMETHING (not a solid
    color). True if the panel region has real variance/content — a cheap load check."""
    r = (cfg.get("recipe_select") or {}).get("panel_region")
    if not r:
        return True                              # not configured -> don't block
    try:
        px = list(guest.crop(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])).values())
    except Exception:
        return True
    if not px:
        return True
    # variance: count distinct-ish colors; a solid panel is ~1 color
    base = px[0]
    diff = sum(1 for c in px if abs(c[0] - base[0]) + abs(c[1] - base[1]) + abs(c[2] - base[2]) > 40)
    return diff >= int((cfg.get("recipe_select") or {}).get("panel_min_diff", 30))


def craft_done(guest: Guest, cfg: dict) -> bool:
    """A craft has ENDED (success OR fail) when ANY of these reappears in the bottom
    bar (owner): the green REPEAT ↻, the Begin button, or the Create button. During an
    active craft the bar shows the reaction-art icons instead, so none are present.
    Reads the current screenshot (caller grabs first)."""
    d = cfg.get("done_detect", {}) or {}
    # Create button gold (mid-craft this spot is grey, not gold)
    cr = d.get("create")
    if cr and cr.get("location"):
        loc = cr["location"]
        if matches(guest.pixel(loc[0], loc[1]), cr.get("color", [248, 213, 126]),
                   int(cr.get("tolerance", 40))):
            return True
    # Begin / Retry gold @ its calibrated spot
    if begin_or_retry(guest, cfg):
        return True
    # green REPEAT ↻ arrow
    rp = d.get("repeat")
    if rp and rp.get("region"):
        reg = rp["region"]
        try:
            px = guest.crop(int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
            if px and sum(1 for c in px.values()
                          if matches(c, rp.get("green", [114, 167, 60]),
                                     int(rp.get("tolerance", 55)))) >= int(rp.get("min_pixels", 30)):
                return True
        except Exception:
            pass
    return False


def craft_complete_chat(guest: Guest, cfg: dict) -> bool:
    """AUTHORITATIVE craft-complete signal: the game prints 'You gain tradeskill XP!'
    / 'You created <item>.' on completion. The button states vary too much to detect
    reliably (Begin vs Create vs a green retry arrow vs the art bar), so we OCR the top
    chat lines instead. Returns True if a completion line is currently showing."""
    reg = (cfg.get("complete_chat", {}) or {}).get("region")
    if not reg or not guest.grab():
        return False
    try:
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop",
             f"{reg['w']}x{reg['h']}+{reg['x']}+{reg['y']}", "+repage",
             "-colorspace", "Gray", "-threshold", "45%", "png:-"],
            capture_output=True, timeout=5).stdout
        if not pre:
            return False
        txt = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"],
                             input=pre, capture_output=True, timeout=8
                             ).stdout.decode(errors="replace").lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return ("tradeskill xp" in txt) or ("you created" in txt) or ("you made" in txt)


# ---- chat safety (the inviolable invariant, PROJECT.md §6.2) ----------------
def game_present(guest: Guest, cfg: dict) -> bool:
    """In-world check: the self power bar (blue) is rendered, proving we're in the
    game world (not login/char-select/loading) — a precondition for injecting. Same
    bar the healer reads. Fail-closed (False) on a read error / not enough blue."""
    g = cfg.get("game_present", {})
    reg = g.get("region")
    if not reg:
        return False                              # uncalibrated -> fail closed
    try:
        px = guest.crop(int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
    except Exception:
        return False
    blue = g.get("blue", [115, 115, 230])
    tol = int(g.get("tolerance", 45))
    n = sum(1 for rgb in px.values() if matches(rgb, blue, tol))
    return n >= int(g.get("min_pixels", 20))


def chat_safe(guest: Guest, cfg: dict) -> bool:
    """Fail-closed keypress gate. True only if we're provably in the game world AND
    the chat input line is CLEAR (no typed text / cursor). Reads the current
    screenshot (the caller grabs first)."""
    if not game_present(guest, cfg):
        return False                              # not in-world -> never inject
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


def reaction_event(guest: Guest, cfg: dict, templates: list, fresh: bool = True) -> int | None:
    """Match the active-reaction watch region against the in-memory reference button
    templates. Returns the counter NUMBER (1-based) of the best match, or None.

    fresh=True grabs a new screenshot; fresh=False crops the LAST grab() — let the
    craft loop take ONE screenshot per iteration and read every sensor (counter,
    running, done, durability) off that single frame instead of grabbing 4-6x (each
    virsh screenshot is ~170ms; that latency is what made counter reactions sluggish)."""
    if not templates:
        return None
    reg = (cfg.get("reaction", {}) or {}).get("region")
    if not reg:
        return None
    if fresh:
        png = guest.grab_region_png(int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
    else:
        png = guest.region_png(int(reg["x"]), int(reg["y"]), int(reg["w"]), int(reg["h"]))
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
_OCR_SCALE = 2          # upscale before OCR: recipe/char rows are thin light text on a
#                         near-black bg; at 1x tesseract reads NOTHING. 2x + a lower
#                         threshold makes them legible. Coords are scaled back to guest px.


def _ocr_words(guest: Guest, region: dict) -> list[dict]:
    """OCR a region -> [{text,x,y,w,h,conf}] in GUEST coords. [] on failure."""
    r = region or {}
    if not r or not guest.grab():
        return []
    try:
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop", f"{r['w']}x{r['h']}+{r['x']}+{r['y']}",
             "+repage", "-colorspace", "Gray", "-resize", f"{_OCR_SCALE * 100}%",
             "-threshold", "43%", "png:-"],
            capture_output=True, timeout=6).stdout
        if not pre:
            return []
        out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                             input=pre, capture_output=True, timeout=10).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("region OCR failed: %s", e)
        return []
    words = []
    s = _OCR_SCALE
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
        words.append({"text": text, "x": r["x"] + x // s, "y": r["y"] + y // s,
                      "w": w // s, "h": h // s, "conf": conf})
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


def _ocr_line(guest: Guest, region: dict) -> str:
    """OCR a result-row box, trying BOTH polarities and keeping the longer result. Recipe
    rows zebra-stripe (fixed threshold leaves some white-on-black, unreadable in isolation —
    negating recovers them), and a long recipe name WRAPS to 2 lines, so we OCR as a block
    (psm 6) over a box tall enough for 2 lines and concat. Returns the alpha blob
    ('aggressivedefenseiijourneyman')."""
    r = region or {}
    if not r or not guest.grab():
        return ""
    base = ["magick", guest.ppm, "-crop", f"{r['w']}x{r['h']}+{r['x']}+{r['y']}", "+repage",
            "-colorspace", "Gray", "-resize", f"{_OCR_SCALE * 100}%", "-threshold", "43%"]
    best = ""
    for neg in ([], ["-negate"]):
        try:
            pre = subprocess.run(base + neg + ["png:-"], capture_output=True, timeout=6).stdout
            if not pre:
                continue
            txt = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"],
                                 input=pre, capture_output=True, timeout=8).stdout.decode(errors="replace")
            blob = _alpha(txt)
            if len(blob) > len(best):
                best = blob
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("row OCR failed: %s", e)
    return best


def _recipe_rows(guest: Guest, cfg: dict) -> list[list]:
    """OCR the recipe-list region -> rows (each a list of word dicts), name column only.
    Rows render at a variable Y (result count / 2-line name wrap), so we group words by Y."""
    rs = cfg.get("recipe_select", {})
    region = rs.get("list_region") or {"x": 225, "y": 195, "w": 320, "h": 485}
    words = [w for w in _ocr_words(guest, region) if w["x"] < region["x"] + 230]  # name col, skip difficulty
    if not words:
        return []
    words.sort(key=lambda w: w["y"])
    rows: list[list] = []
    for w in words:
        if rows and w["y"] - rows[-1][-1]["y"] <= 26:   # wrapped 2-line name ~16px
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _row_candidates(guest: Guest, cfg: dict) -> list[tuple[str, tuple[int, int]]]:
    """[(name_blob, click_xy)] for each result row. Prefers EXPLICIT per-row OCR boxes +
    click points (recipe_select.result_rows — owner-calibrated, one box/click per fixed row
    slot); falls back to OCRing one tall region and grouping words by Y (click = icon_x at
    the row's center). The per-row mode is deterministic: fixed box per slot, fixed click."""
    rs = cfg.get("recipe_select", {})
    slots = rs.get("result_rows") or []
    if slots:
        out = []
        for s in slots:
            ocr, click = s.get("ocr"), s.get("click")
            if not ocr or not click:
                continue
            out.append((_ocr_line(guest, ocr), (int(click[0]), int(click[1]))))
        return out
    icon_x = int(rs.get("icon_x", 244))
    out = []
    for ws in _recipe_rows(guest, cfg):
        blob = _alpha(" ".join(w["text"] for w in ws))
        y = sum(w["y"] + w["h"] // 2 for w in ws) // len(ws)
        out.append((blob, (icon_x, y)))
    return out


def recipe_row_blobs(guest: Guest, cfg: dict) -> list[str]:
    """Diagnostic: the OCR'd text of each result row, as seen by the matcher. Logged on a
    failed match so 'not found' is never silent (wrong name? unfiltered list? focus race?)."""
    return [blob for blob, _ in _row_candidates(guest, cfg)]


def match_recipe_row(guest: Guest, cfg: dict, name: str) -> tuple[int, int] | None:
    """Click point of the result row whose name matches `name`: full token-coverage AND no
    surplus WORD (a row with an extra word — 'Tranquil'/'Imbued'/'Blessed' Burlap Pantaloons,
    or '… of Power' — is a DIFFERENT recipe and is rejected, never crafted in place of the
    plain target). Tiebreak to the most-exact row. Per-row OCR boxes when calibrated, else one
    grouped region. None if nothing qualifies (skip beats crafting the wrong item)."""
    rs = cfg.get("recipe_select", {})
    want = [t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) >= 3]
    if not want:
        return None
    # A row carrying a variant modifier ("Imbued"/"Blessed") the TARGET lacks is never our
    # recipe (owner rule) — the surplus word is invisible to coverage scoring otherwise.
    modifiers = [m.lower() for m in rs.get("variant_modifiers", ["imbued", "blessed"])]
    name_l = (name or "").lower()
    forbidden = [m for m in modifiers if m not in name_l]
    want_len = sum(len(t) for t in want)
    # Surplus-letter budget beyond the target tokens: OCR noise (a stray glyph or two) is fine,
    # but a whole extra WORD (~5+ chars) means a different recipe -> reject. Roman/quality tails
    # are already part of `want` for tier'd names, so this targets prefix/suffix variants.
    max_extra = int(rs.get("max_extra_chars", 4))
    scored = []   # (score, extra, click)
    for blob, click in _row_candidates(guest, cfg):
        if not blob:
            continue
        if any(_contains(blob, f) for f in forbidden):
            log.debug("recipe row click=%s REJECT (variant) blob=%r", click, blob)
            continue
        score = sum(1 for t in want if _contains(blob, t)) / len(want)
        extra = max(0, len(blob) - want_len)
        if extra > max_extra:                  # extra word -> different recipe (Tranquil/of-X)
            log.debug("recipe row click=%s REJECT (extra word, %d) blob=%r", click, extra, blob)
            continue
        log.debug("recipe row click=%s score=%.2f extra=%d blob=%r", click, score, extra, blob)
        scored.append((score, extra, click))
    if not scored:
        return None
    scored.sort(key=lambda s: (-s[0], s[1]))   # best coverage, then most-exact
    best_score, _, click = scored[0]
    threshold = float(rs.get("match_threshold", 0.6))
    # Sole-result fast path: one non-rejected row that shares SOMETHING -> take it even
    # below threshold (a lone obvious match shouldn't be dismissed over OCR noise).
    sole = len(scored) == 1 and best_score >= float(rs.get("sole_result_floor", 0.34))
    if best_score < threshold and not sole:
        return None
    return click


def search_box_text(guest: Guest, cfg: dict) -> str:
    """OCR the recipe SEARCH FIELD and return its lowercased text. Used to PROVE the
    field is focused and our query actually landed there (vs leaking to the game world
    as movement keys). Region defaults are derived from search_click/clear_click so it
    works before the owner calibrates `recipe_select.search_region`."""
    rs = cfg.get("recipe_select", {}) or {}
    reg = rs.get("search_region")
    if not reg:
        sc = rs.get("search_click") or [349, 180]
        cc = rs.get("clear_click") or [443, 181]
        left = max(0, int(sc[0]) - 110)
        reg = {"x": left, "y": int(sc[1]) - 11, "w": max(60, int(cc[0]) - 5 - left), "h": 24}
    words = _ocr_words(guest, reg)
    return _alpha(" ".join(w["text"] for w in words))


def search_landed(guest: Guest, cfg: dict, query: str) -> bool:
    """True if the OCR'd search box shows our query (proof the field was focused and
    the keystrokes did NOT leak to the world). Tolerant: any query token (>=3 chars)
    appearing in the box counts — OCR of a small field is noisy, but an EMPTY/garbage
    box (the leak case) shares no tokens, so we fail closed and abort instead of
    re-typing into the world."""
    box = search_box_text(guest, cfg)
    if not box:
        log.info("search box reads empty — query did not land (likely leaked to world)")
        return False
    want = [t for t in re.findall(r"[a-z]+", (query or "").lower()) if len(t) >= 3]
    if not want:
        return bool(box)
    hit = sum(1 for t in want if _contains(box, t))
    log.info("search box=%r query tokens=%s hit=%d", box, want, hit)
    return hit >= 1


def _alpha(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _contains(blob: str, word: str) -> bool:
    """Is `word` in `blob`, tolerant of one OCR slip (e.g. 'wuoshi' in 'wiuoshiserver')?"""
    if not word:
        return True
    if word in blob:
        return True
    n = len(word)
    return any(difflib.SequenceMatcher(None, blob[i:i + n + 1], word).ratio() >= 0.8
               for i in range(max(1, len(blob) - n)))


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
        # psm 6 (block): the detail panel shows NAME then a class line; a tall region
        # can catch either, so OCR both lines and join — the caller checks the target
        # name as a substring, which still tells the near-identical twins apart.
        out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"],
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
                     log=lambda _m: None, play: bool = True) -> bool:
    """Char-select pick used by BOTH iksar (login) and forge (FORGE.md §5.5).

    The list ORDER changes every login, so positions can't be hardcoded. Method:
      1. OCR the list to LOCATE candidate rows for the target NAME (1-2; e.g. two Robskins).
      2. For each candidate, click it (sweeping a few px to absorb the guest click offset)
         and read the DETAIL PANEL ("verify area").
      3. Confirm ONLY when the panel shows the target NAME (strict) AND the owner's SERVER
         (char_select.server = Wuoshi; Maj'Dul is wrong). The list highlight is unreliable
         (the selected name just brightens), so the panel is the only source of truth.
      4. Play only on a confirmed match; otherwise never Play.
    """
    cs = cfg.get("char_select", cfg) or {}
    vr = cs.get("verify_region", {"x": 1605, "y": 745, "w": 280, "h": 155})
    settle = float(cs.get("select_settle_s", 2.2))        # panel lags the click
    name_x = int(cs.get("name_click_x", 190))
    play_click = cs.get("play_click")
    server = _alpha(cs.get("server", ""))                 # 'wuoshi'
    ctl = _alpha(target)
    if len(ctl) < 4:
        log(f"char-select: target '{target}' too short"); return False

    # Find the list's NAME-row vertical SPAN via OCR (exclude header/footer/button text),
    # so we can scan every row without clicking the buttons below the list.
    _STOP = {"select", "character", "slots", "available", "veteran", "bonus", "server",
             "wuoshi", "majdul", "create", "heroic", "standard", "purchase", "exit",
             "play", "delete", "account", "transfer", "shop", "now", "options"}
    lr = cs.get("list_region", {}) or {"x": 80, "y": 380, "w": 420, "h": 560}
    ys = []
    for _ in range(3):
        for w in _ocr_words(guest, lr):
            a = _alpha(w["text"])
            if len(a) >= 4 and a not in _STOP:
                ys.append(w["y"] + w["h"] // 2)
        if ys:
            break
        time.sleep(0.7)
    if ys:
        top, bot = min(ys) - 14, max(ys) + 14
    else:
        top, bot = lr["y"] + 40, lr["y"] + lr["h"] - 80
    top = max(top, lr["y"]); bot = min(bot, lr["y"] + lr["h"])

    # Scan every row, click it, read the detail panel (the only reliable truth), and
    # stop at the target NAME on the right SERVER. ~26px step < a row's height, so every
    # row gets selected at some click regardless of the click offset / list order.
    log(f"scanning rows y{top}..{bot} for {target} on {cs.get('server','')}")
    y = top
    while y <= bot:
        guest.click(name_x, y)
        # settled read: wait, then poll until the panel stops changing (kills the
        # lag where a too-early read returns the PREVIOUS selection).
        time.sleep(max(0.8, settle - 0.8))
        blob = panel_name(guest, vr)
        for _ in range(4):
            time.sleep(0.45)
            nb = panel_name(guest, vr)
            if nb == blob:
                break
            blob = nb
        has_name = ctl in blob                            # STRICT (croolst != croalst)
        has_server = _contains(blob, server)              # tolerant (wuoshi vs majdul)
        if has_name and has_server:
            log(f"confirmed {target} on {cs.get('server','')} @ y{y} -> Play")
            if play and play_click:
                guest.click(int(play_click[0]), int(play_click[1]))
            return True
        log(f"  y{y}: '{blob[:40]}' name={has_name} {cs.get('server','')}={has_server}")
        y += 26
    log(f"char-select: {target} on {cs.get('server','')} NOT found — NOT pressing Play")
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
        # magick: crop -> upscale -> grayscale -> NEGATE (writ text is light-on-dark; tesseract
        # wants dark-on-light) -> blur -> threshold -> PNG.
        neg = ["-negate"] if j.get("negate", True) else []
        pre = subprocess.run(
            ["magick", guest.ppm, "-crop", f"{reg['w']}x{reg['h']}+{reg['x']}+{reg['y']}",
             "+repage", "-resize", f"{scale}%", "-colorspace", "Gray", *neg,
             "-blur", "0x0.5", "-threshold", f"{j.get('threshold_pct', 60)}%", "png:-"],
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
