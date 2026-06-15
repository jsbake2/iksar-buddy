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


def match_recipe_row(guest: Guest, cfg: dict, name: str) -> tuple[int, int] | None:
    """After a recipe search, the wanted result can land in one of N candidate rows
    (recipe_select.result_rows). OCR each row's region, match against the searched
    `name` (token overlap, tolerant of an OCR slip and of extra words like 'pristine'),
    and return the CLICK point of the best-matching row — or None if none clears the
    threshold (so we never click/craft the wrong recipe). Owner-confirmed approach."""
    rs = cfg.get("recipe_select", {})
    rows = rs.get("result_rows") or []
    want = [t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) >= 3]
    if not rows or not want:
        return None
    best, best_words, best_score = None, [], 0.0
    for row in rows:
        words = _ocr_words(guest, row.get("region", {}))
        blob = _alpha(" ".join(w["text"] for w in words))
        if not blob:
            continue
        score = sum(1 for t in want if _contains(blob, t)) / len(want)
        log.debug("recipe row %s score=%.2f blob=%r", row.get("click"), score, blob)
        if score > best_score:
            best_score, best, best_words = score, row, words
    if not best or best_score < float(rs.get("match_threshold", 0.6)):
        return None
    # Click the row's ICON COLUMN (x from config) at the ACTUAL row Y where the name
    # OCR'd — the rows shift vertically (name wraps to 1-2 lines), so a fixed Y misses.
    # The caller DOUBLE-clicks this to load the recipe (single only highlights).
    clk = best.get("click")
    if not clk:
        return None
    ys = [w["y"] + w["h"] // 2 for w in best_words]
    y = sum(ys) // len(ys) if ys else int(clk[1])
    return (int(clk[0]), y)


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
