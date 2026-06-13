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
import subprocess
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


# ---- reaction template match (opencv) ---------------------------------------
_templates_cache: dict = {}


def _load_templates(cfg: dict, profile_dir: Path):
    """Load reaction templates once -> {event_name: cv2 BGR array}. Empty if cv2
    or the template dir is missing (reaction detection then no-ops, safely)."""
    key = str(profile_dir)
    if key in _templates_cache:
        return _templates_cache[key]
    out: dict = {}
    try:
        import cv2
    except ImportError:
        log.info("opencv not installed — reaction matching disabled (install later)")
        _templates_cache[key] = out
        return out
    tdir = profile_dir / cfg.get("reaction", {}).get("templates_dir", "templates/reactions")
    if tdir.is_dir():
        for png in tdir.glob("*.png"):
            img = cv2.imread(str(png))
            if img is not None:
                out[png.stem] = img
    _templates_cache[key] = out
    return out


def reaction_event(guest: Guest, cfg: dict, profile_dir: Path) -> str | None:
    """Grab the reaction region and template-match. Returns the matched event name
    (-> arts.reactions[name] key) or None. No-op if opencv/templates absent."""
    templates = _load_templates(cfg, profile_dir)
    if not templates:
        return None
    reg = cfg.get("reaction", {}).get("region")
    if not reg:
        return None
    png = guest.grab_region_png(reg["x"], reg["y"], reg["w"], reg["h"])
    if not png:
        return None
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None
        thresh = float(cfg.get("reaction", {}).get("confidence", 0.80))
        best, best_val = None, 0.0
        for name, tmpl in templates.items():
            if tmpl.shape[0] > arr.shape[0] or tmpl.shape[1] > arr.shape[1]:
                continue
            res = cv2.matchTemplate(arr, tmpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            if mx > thresh and mx > best_val:
                best, best_val = name, mx
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
