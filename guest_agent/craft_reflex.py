"""craft_reflex — the Forge counter loop, run LOCALLY in the guest (the fast path).

This is the dino's tight counter loop (eq2/craft_bot/craft.py) reborn in-VM: mss
grab (~2-5ms) + cv2 template match + pydirectinput keypress, so a counter lands in
tens of ms instead of the host's ~170ms virsh round-trip. The HOST still does the
careful slow work (recipe select, Begin/Create, confirming the craft is running),
then hands off "react until done" to this loop via the agent command channel.

Everything it needs comes in the `ruleset` (sent by the host worker from craft.yaml):
geometry, colors, confidence, the art keys per durability mode, and the chat-safety
regions. No state is shared with the host in the hot path.

Chat-safety invariant (PROJECT.md §6.2) is enforced HERE too: never press unless the
chat input line is clear and we're in-world.
"""
from __future__ import annotations

import glob
import os
import re
import time
from pathlib import Path

import cv2
import mss
import numpy as np
import pydirectinput

try:
    import pygetwindow as gw
except Exception:                      # noqa: BLE001
    gw = None

pydirectinput.PAUSE = 0                 # no built-in delay; we pace the loop ourselves
pydirectinput.FAILSAFE = False


def _grab(sct, x, y, w, h):
    """BGR numpy array of an absolute-coord region (one fast mss BitBlt)."""
    raw = sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
    return cv2.cvtColor(np.asarray(raw), cv2.COLOR_BGRA2BGR)


# The EQ2 log writes exactly one of these per FINISHED craft (success OR crit-fail both
# still create an item), e.g.  You created \aITEM -966331653 281835275:Boiled Leather
# Bandolier\/a.   — the authoritative "this craft actually completed" signal. Pixels
# (a Create/Begin button on screen) are NOT: they show before a craft starts too, which
# is what produced false DONEs. The item name is the text after the last ':' before '\/a'.
_CREATED_RE = re.compile(r"You (?:created|made)\b(?: \d+)?\s+\\aITEM[^:]*:([^\\]+?)\\/a", re.I)
_LOG_ROOT_DEFAULT = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
                     r"\EverQuest II\logs")


def _norm_item(s: str) -> str:
    """Lowercase, drop the (Quality) tag + punctuation for loose recipe<->created match."""
    s = re.sub(r"\([^)]*\)", "", s or "").lower()
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def _pixel(sct, x, y):
    px = _grab(sct, x, y, 1, 1)
    b, g, r = (int(v) for v in px[0, 0])
    return (r, g, b)


def _match_color(rgb, expected, tol):
    return all(abs(int(rgb[i]) - int(expected[i])) <= tol for i in range(3))


class CraftReflex:
    def __init__(self, ruleset: dict, log, should_stop) -> None:
        self.r = ruleset or {}
        self.log = log
        self.should_stop = should_stop          # () -> bool : host said idle/stop
        self.reactions = 0
        self.done = False
        # arts per durability mode (from the host keymap)
        arts = self.r.get("arts", {}) or {}
        self.arts = {"durability": list(arts.get("durability") or ["1", "2", "3"]),
                     "progress": list(arts.get("progress") or ["4", "5", "6"])}
        self._templates: list = []
        self._filler_i = 0
        self._last_counter = None
        self._cnt_baseline = None        # (mean_r, mean_g) when the counter icon appeared
        self._cnt_resolved = None         # None | "green" (success) | "red" (fail)
        self._cnt_last_press = 0.0
        self._cnt_key = None              # the art key pressed for the current counter (debug)
        self.fails = 0
        self.crafts_done = 0              # craft_run (in-guest list loop) progress
        # -- log-based completion (authoritative) --
        self._log_root = self.r.get("eq2_log_root") or _LOG_ROOT_DEFAULT
        self._log_via = bool(self.r.get("done_via_log", True))
        self._log_path: str | None = None
        self._log_base = 0               # byte offset captured at each craft start
        self._expect = _norm_item(self.r.get("expected_item", ""))

    # -- log completion ---------------------------------------------------
    def _find_log(self) -> str | None:
        """Newest eq2log_*.txt under the logs root = the active character's log (EQ2
        writes it continuously while playing). Cached; re-discovered if it vanishes."""
        if self._log_path and os.path.exists(self._log_path):
            return self._log_path
        try:
            files = glob.glob(os.path.join(self._log_root, "**", "eq2log_*.txt"),
                              recursive=True)
            self._log_path = max(files, key=os.path.getmtime) if files else None
        except OSError:
            self._log_path = None
        return self._log_path

    def _log_size(self) -> int:
        p = self._find_log()
        try:
            return os.path.getsize(p) if p else 0
        except OSError:
            return 0

    def _new_creations(self) -> list[str]:
        """Item names from 'You created …' lines written since self._log_base; advances
        the offset to end-of-file. [] if no log / nothing new."""
        p = self._find_log()
        if not p:
            return []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_base)
                chunk = f.read()
                self._log_base = f.tell()
        except OSError:
            return []
        return [m.group(1).strip() for m in _CREATED_RE.finditer(chunk)]

    def _craft_completed(self) -> bool:
        """True once the log shows this craft finished. The bot crafts serially (one
        recipe per hand-off, one 'You created …' line per finished craft), so ANY new
        creation since the craft started means THIS craft completed. We log whether it
        matched the expected item for visibility, but don't gate on it — created-line
        wording can vary by trade and we never want to hang waiting for an exact string."""
        for name in self._new_creations():
            cn = _norm_item(name)
            match = bool(self._expect) and cn and \
                (cn == self._expect or cn in self._expect or self._expect in cn)
            tag = "" if (not self._expect or match) else f" (expected '{self._expect}')"
            self.log(f"reflex: log completion — created '{name}'{tag}")
            return True
        return False

    # -- sensors (mss, local) ---------------------------------------------
    def _capture_templates(self, sct) -> int:
        boxes = (self.r.get("reaction", {}) or {}).get("button_regions") or []
        self._templates = []
        for b in boxes:
            try:
                self._templates.append(_grab(sct, b["x"], b["y"], b["w"], b["h"]))
            except Exception:                    # noqa: BLE001
                self._templates.append(None)
        return sum(1 for t in self._templates if t is not None)

    @staticmethod
    def _mean_rgb(arr):
        if arr is None:
            return (0.0, 0.0, 0.0)
        m = arr.reshape(-1, 3).mean(axis=0)     # BGR
        return (float(m[2]), float(m[1]), float(m[0]))

    def _counter(self, sct):
        """Return (best_counter_n_or_None, [score0,score1,score2], watch_array)."""
        reg = (self.r.get("reaction", {}) or {}).get("region")
        if not reg or not self._templates:
            return None, [], None
        try:
            arr = _grab(sct, reg["x"], reg["y"], reg["w"], reg["h"])
        except Exception:                        # noqa: BLE001
            return None, [], None
        thresh = float((self.r.get("reaction", {}) or {}).get("confidence", 0.45))
        scores = []
        best, best_val = None, 0.0
        for i, t in enumerate(self._templates):
            if t is None or t.shape[0] > arr.shape[0] or t.shape[1] > arr.shape[1]:
                scores.append(0.0)
                continue
            res = cv2.matchTemplate(arr, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            scores.append(round(float(mx), 3))
            if mx > thresh and mx > best_val:
                best, best_val = i + 1, mx
        return best, scores, arr

    def _mode(self, sct) -> str:
        """Durability mode from the OWNER-MARKED pixel on the green durability bar:
        GREEN [34,205,46] = good durability => progress arts (4/5/6); not green = low
        durability => durability arts (1/2/3). One pixel — the simple, reliable read."""
        d = self.r.get("durability_mode", {}) or {}
        loc = d.get("location")
        if not loc:
            return "progress"
        rgb = _pixel(sct, loc[0], loc[1])
        return "progress" if _match_color(rgb, d.get("progress_color", [0, 0, 0]),
                                          d.get("tolerance", 45)) else "durability"

    def _chat_safe(self, sct) -> bool:
        """In-world + chat line clear. Fail-closed (uncalibrated -> not safe)."""
        gp = self.r.get("game_present", {}) or {}
        reg = gp.get("region")
        if reg:
            try:
                px = _grab(sct, reg["x"], reg["y"], reg["w"], reg["h"]).reshape(-1, 3)
            except Exception:                    # noqa: BLE001
                return False
            blue = gp.get("blue", [115, 115, 230]); tol = int(gp.get("tolerance", 45))
            n = int(np.sum(np.all(np.abs(px[:, ::-1].astype(int) - blue) <= tol, axis=1)))
            if n < int(gp.get("min_pixels", 20)):
                return False                     # not in-world
        ci = self.r.get("chat_input", {}) or {}
        creg = ci.get("region")
        if not creg:
            return bool(reg)                     # no chat region -> rely on in-world only
        try:
            g = _grab(sct, creg["x"], creg["y"], creg["w"], creg["h"])
        except Exception:                        # noqa: BLE001
            return False
        gray = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        bright = int(np.sum(gray > 0.6 * 255))
        return bright <= int(ci.get("bright_threshold", 25))

    def _done(self, sct) -> bool:
        d = self.r.get("done_detect", {}) or {}
        cr = d.get("create")
        if cr and cr.get("location"):
            loc = cr["location"]
            if _match_color(_pixel(sct, loc[0], loc[1]), cr.get("color", [248, 213, 126]),
                            int(cr.get("tolerance", 40))):
                return True
        for which in ("retry", "begin"):
            spec = (self.r.get(which) or {}).get("pixel")
            if spec and spec.get("location"):
                loc = spec["location"]
                if _match_color(_pixel(sct, loc[0], loc[1]), spec.get("color", [0, 0, 0]),
                                int(spec.get("tolerance", 45))):
                    return True
        rp = d.get("repeat")
        if rp and rp.get("region"):
            reg = rp["region"]
            try:
                px = _grab(sct, reg["x"], reg["y"], reg["w"], reg["h"]).reshape(-1, 3)
                green = rp.get("green", [114, 167, 60]); tol = int(rp.get("tolerance", 55))
                n = int(np.sum(np.all(np.abs(px[:, ::-1].astype(int) - green) <= tol, axis=1)))
                if n >= int(rp.get("min_pixels", 30)):
                    return True
            except Exception:                    # noqa: BLE001
                pass
        return False

    # -- input ------------------------------------------------------------
    def _press(self, key: str) -> None:
        pydirectinput.press(key)

    def _activate_eq2(self) -> None:
        if gw is None:
            return
        try:
            w = gw.getWindowsWithTitle("EverQuest II")
            if w:
                w[0].activate()
        except Exception:                        # noqa: BLE001
            pass

    # -- the loop ---------------------------------------------------------
    # -- in-guest LOCAL click — MIRROR the proven AHK ibgclick exactly --
    def _click(self, x, y) -> None:
        """EQ2 only registers Event-mode (mouse_event) clicks AND needs a real cursor
        MOVE event before the press — a SetCursorPos teleport emits no WM_MOUSEMOVE, so the
        button never highlights and the down/up misfires (on an icon it GRABS it onto the
        cursor — the 'recipe on the pointer'). So we do what gclick_ev.ahk does: absolute
        MOVE event -> settle 200ms -> down -> 40ms -> up."""
        try:
            import ctypes
            u = ctypes.windll.user32
            sw = u.GetSystemMetrics(0) or 1920
            sh = u.GetSystemMetrics(1) or 1080
            ax = int(int(x) * 65535 / max(1, sw - 1))
            ay = int(int(y) * 65535 / max(1, sh - 1))
            MOVE, ABS, LDOWN, LUP = 0x0001, 0x8000, 0x0002, 0x0004
            u.mouse_event(MOVE | ABS, ax, ay, 0, 0)  # real move so EQ2 registers the cursor
            time.sleep(0.18)                          # AHK Sleep 200 after MouseMove
            u.mouse_event(LDOWN, 0, 0, 0, 0)
            time.sleep(0.05)                          # AHK SetMouseDelay 40
            u.mouse_event(LUP, 0, 0, 0, 0)
            time.sleep(0.05)
        except Exception as e:                       # noqa: BLE001
            self.log(f"reflex: click failed: {e}")

    def _region_count(self, sct, reg, rgb, tol) -> int:
        try:
            arr = _grab(sct, reg["x"], reg["y"], reg["w"], reg["h"]).reshape(-1, 3)  # BGR
        except Exception:                            # noqa: BLE001
            return 0
        want = np.array(rgb, dtype=int)[::-1]        # RGB -> BGR to match arr
        return int(np.sum(np.all(np.abs(arr.astype(int) - want) <= tol, axis=1)))

    def _running(self, sct) -> bool:
        """Red STOP-SIGN in the art-bar's right slot = a craft is RUNNING."""
        rd = self.r.get("running_detect", {}) or {}
        reg = rd.get("region")
        if not reg:
            return False
        n = self._region_count(sct, reg, rd.get("red", [147, 62, 37]), int(rd.get("tolerance", 45)))
        return n >= int(rd.get("min_pixels", 300))

    def _begin_lit(self, sct) -> bool:
        bp = (self.r.get("begin", {}) or {}).get("pixel", {}) or {}
        return bool(bp.get("location")) and _match_color(
            _pixel(sct, bp["location"][0], bp["location"][1]),
            bp.get("color", [0, 0, 0]), int(bp.get("tolerance", 45)))

    def _repeat_lit(self, sct) -> bool:
        rep = self.r.get("repeat", {}) or {}
        dd = self.r.get("done_detect", {}) or {}
        reg = rep.get("region") or (dd.get("repeat") or {}).get("region")
        if not reg:
            return False
        g = rep.get("green") or (dd.get("repeat") or {}).get("green", [114, 167, 60])
        tol = int(rep.get("tolerance", (dd.get("repeat") or {}).get("tolerance", 55)))
        mn = int(rep.get("min_pixels", (dd.get("repeat") or {}).get("min_pixels", 30)))
        return self._region_count(sct, reg, g, tol) >= mn

    def _started(self, sct) -> bool:
        """Craft is GENUINELY running: red stop-sign up AND no start button on screen.
        The 'and not _done' guards running_detect false-positives — the jeweler 'Jugular
        Slice' prep window false-confirmed running while Begin was still showing, so the
        react loop then bailed as 'missing mats' (0/6). A really-running craft has no
        Begin/Create/Repeat button left."""
        return self._running(sct) and not self._done(sct)

    def _start_craft(self, first: bool) -> bool:
        """Start one craft LOCALLY and confirm it's RUNNING. Ground truth (sampled live):
        the continuation button is the green REPEAT ↻ (Begin goes dark after a craft); the
        FIRST craft uses Begin (lit after select). The ONE reliable 'it started' signal is
        running_detect (red stop-sign: ~990px running vs ~43 idle) — the 'button-gone' and
        Create-pixel checks were noisy and caused both wasted retries and false bails.
        Returns False only when no start button ever appears (list done / Begin disabled)."""
        st = self.r.get("start", {}) or {}
        attempts = int(st.get("attempts", 4))
        confirm_t = float(st.get("confirm_timeout", 6.0))   # stop-sign renders a few s after click
        poll = float(st.get("poll", 0.12))
        post_begin = float(st.get("post_begin", 0.25))
        begin_detect = float(st.get("begin_detect", 2.0))
        bclick = (self.r.get("begin", {}) or {}).get("click")
        rclick = (self.r.get("repeat", {}) or {}).get("click")
        with mss.mss() as sct:
            for attempt in range(attempts):
                if self.should_stop():
                    return False
                if self._started(sct):               # a prior click already started it
                    return True
                if not self._chat_safe(sct):         # gate the click too (fail-closed)
                    time.sleep(0.2); continue
                # BEGIN is the reliable button for BOTH first and continuation: the prep
                # window's Begin reappears after each craft (after a brief ↻ flash that is
                # NOT actually clickable). Wait for Begin; fall to REPEAT only if it never
                # shows. (Clicking the ↻ wastes the whole confirm window every transition.)
                click, name = None, None
                t0 = time.time()
                while time.time() - t0 < begin_detect and not self.should_stop():
                    if self._begin_lit(sct):
                        click, name = bclick, "begin"; break
                    if self._started(sct):
                        return True
                    time.sleep(poll)
                if not click and self._repeat_lit(sct):
                    click, name = rclick, "repeat"
                if not click:
                    return False                     # nothing to start
                self.log(f"reflex: start '{name}' -> click {click}")
                self._click(click[0], click[1])
                time.sleep(post_begin)
                t1 = time.time()
                while time.time() - t1 < confirm_t and not self.should_stop():
                    if self._started(sct):           # red stop-sign up AND start button gone
                        return True
                    time.sleep(poll)
                self.log(f"reflex: '{name}' didn't confirm running in {confirm_t:.0f}s — retrying")
            return False

    def _focus_safe(self) -> None:
        """Park the cursor on the mouse-safe spot AFTER the craft has started, so the art
        keys (1-6) land in the focused craft window and the cursor isn't sitting on a
        button. Owner's order: click Begin FIRST, then mouse to the safe spot."""
        loc = self.r.get("safe_click")
        if loc:
            self._click(loc[0], loc[1])
            time.sleep(float(self.r.get("post_focus", 0.15)))

    def run_list(self) -> bool:
        """Do the WHOLE list IN-GUEST: start -> react until done -> repeat, `count`
        times, all local (no host round-trip per craft). The host only selected the
        recipe and handed off the count; we report crafts_done via the agent telemetry."""
        count = int(self.r.get("count", 1))
        self.crafts_done = 0
        self._activate_eq2()
        self.log(f"reflex: craft_run START — {count} craft(s) in-guest")
        while self.crafts_done < count and not self.should_stop():
            if not self._start_craft(first=(self.crafts_done == 0)):
                self.log(f"reflex: no craft to start (done {self.crafts_done}/{count}) — stopping")
                break
            self._focus_safe()                       # click start THEN mouse to safe spot
            if not self._react_until_done():
                self.log(f"reflex: craft {self.crafts_done + 1}/{count} didn't complete — stopping")
                break
            self.crafts_done += 1
            self.log(f"reflex: craft {self.crafts_done}/{count} complete")
        self.log(f"reflex: craft_run END — {self.crafts_done}/{count} done")
        return self.crafts_done >= count

    def run(self) -> bool:
        """Single craft: react until done. The host started + confirmed running."""
        return self._react_until_done()

    def _react_until_done(self) -> bool:
        """React until the craft is DONE (repeat/Begin/Create reappears) or stop/timeout.
        Returns True on a clean completion. The host (or _start_craft) already confirmed
        the craft is RUNNING, so we start in the active state and watch for done."""
        loop_sleep = float(self.r.get("loop_sleep", 0.04))
        art_interval = float(self.r.get("art_interval", 1.0))
        press_interval = float(self.r.get("counter_press_interval", 0.12))   # mash cadence
        green_delta = float(self.r.get("green_delta", 16.0))                 # tint thresholds
        red_delta = float(self.r.get("red_delta", 16.0))
        done_every = float(self.r.get("done_check_interval", 0.5))
        max_t = float(self.r.get("max_craft_time", 90.0))
        debug = bool(self.r.get("debug"))
        self._debug = debug
        self._dur_dbg = 0
        dbg_dir = Path(r"C:\ib\agent\dbg")
        dbg_n = 0
        self._activate_eq2()
        with mss.mss() as sct:
            got = self._capture_templates(sct)
            self.log(f"reflex: captured {got} counter templates; reacting (debug={debug})")
            if debug:
                try:
                    dbg_dir.mkdir(parents=True, exist_ok=True)
                    for f in dbg_dir.glob("*.png"):
                        f.unlink()
                    full = _grab(sct, 0, 0, 1920, 1080)
                    cv2.imwrite(str(dbg_dir / "full.png"), full)
                    for i, t in enumerate(self._templates):
                        if t is not None:
                            cv2.imwrite(str(dbg_dir / f"tmpl_{i}.png"), t)
                    self.log(f"reflex: dumped full.png + {got} templates to {dbg_dir}")
                except Exception as e:           # noqa: BLE001
                    self.log(f"reflex: debug dump failed: {e}")
            t0 = time.time()
            last_done = 0.0
            last_filler = 0.0
            saw_active = False                                   # have we seen a real counter yet?
            active_grace = float(self.r.get("active_grace", 12.0))
            # Authoritative completion = a new "You created …" line in the EQ2 log. Snapshot
            # the log offset NOW (the craft is already running, so the previous craft's line
            # is already behind us). Falls back to pixel _done only if no log is found.
            use_log = self._log_via and self._find_log() is not None
            self._log_base = self._log_size() if use_log else 0
            if use_log:
                self.log(f"reflex: completion via log {os.path.basename(self._log_path)} "
                         f"@{self._log_base}")
            while not self.should_stop() and time.time() - t0 < max_t:
                safe = self._chat_safe(sct)
                mode = self._mode(sct)
                n, scores, watch = self._counter(sct)
                mr, mg, mb = self._mean_rgb(watch)
                # COUNTER: MASH the matching art (owner: push more than once). The icon
                # tints GREEN when the counter SUCCEEDS, RED when it FAILS, no change while
                # still uncountered. So we keep pressing until we see green/red, then stop.
                if n:
                    saw_active = True                            # a counter = the craft is really running
                    if n != self._last_counter:                  # new counter -> baseline
                        self._last_counter = n
                        self._cnt_baseline = (mr, mg)
                        self._cnt_resolved = None
                        self._cnt_last_press = 0.0
                        self._cnt_key = None
                        # DEBUG: dump the durability/progress bars + mode pixel at onset so we
                        # can VERIFY the mode the agent picked (the failing counters are
                        # progress-mode; is the bar actually progress, or misread?).
                        if debug:
                            d = self.r.get("durability_mode", {}) or {}
                            loc = d.get("location") or [857, 261]
                            mpx = _pixel(sct, loc[0], loc[1])
                            ck = self.arts["durability"]
                            self.log(f"counter#{n} ONSET mode={mode} durpx@{loc}={mpx} "
                                     f"counterkey={ck[n-1] if 1<=n<=len(ck) else '?'}")
                            try:
                                cv2.imwrite(str(dbg_dir / f"bars_{dbg_n:02d}_{mode}_n{n}.png"),
                                            _grab(sct, 560, 250, 340, 40))
                            except Exception:        # noqa: BLE001
                                pass
                    # STATS ONLY (not load-bearing): note the first green/red tint for the
                    # dashboard. This does NOT gate pressing — see below.
                    if self._cnt_resolved is None and self._cnt_baseline:
                        dg = mg - self._cnt_baseline[1]
                        dr = mr - self._cnt_baseline[0]
                        if dg >= green_delta and dg > dr:
                            self._cnt_resolved = "green"; self.reactions += 1
                            self.log(f"counter#{n} ({mode}) key={self._cnt_key} SUCCESS (green, dg={dg:.0f} dr={dr:.0f})")
                        elif dr >= red_delta and dr > dg:
                            self._cnt_resolved = "red"; self.fails += 1
                            self.log(f"counter#{n} ({mode}) key={self._cnt_key} FAILED (red, dr={dr:.0f} dg={dg:.0f})")
                    now = time.time()
                    # MASH the art the ENTIRE time the icon is visible. Do NOT stop on a
                    # color-resolved green/red: the pink #3 icon has red accents and
                    # false-resolves 'red' on its own onset frame, which (when it gated
                    # pressing) made us press once and quit before the event cleared —
                    # bleeding durability to a ~33% craft death. The icon vanishing
                    # (n -> None) is the real "event over" signal and ends the mash.
                    if safe and now - self._cnt_last_press >= press_interval:
                        # COUNTERS always use the icon's art (1/2/3), NOT mode-dependent.
                        # The 4/5/6 are the progress PUMP (filler), they don't counter
                        # anything — verified: pressing 5 whiffs, pressing 2 clears it.
                        ck = self.arts["durability"]
                        key = ck[n - 1] if 1 <= n <= len(ck) else None
                        if key:
                            self._press(key)
                            self._cnt_key = key
                            self._cnt_last_press = now
                    if debug and dbg_n < 80:
                        base = self._cnt_baseline or (0, 0)
                        self.log(f"  n={n} score={scores[n-1] if scores else 0} rgb=({mr:.0f},{mg:.0f},{mb:.0f}) "
                                 f"dg={mg-base[1]:.0f} dr={mr-base[0]:.0f} res={self._cnt_resolved} safe={safe}")
                        if watch is not None:
                            cv2.imwrite(str(dbg_dir / f"w_{dbg_n:02d}_n{n}_{self._cnt_resolved or 'neu'}.png"), watch)
                            dbg_n += 1
                    time.sleep(loop_sleep)
                    continue
                self._last_counter = None
                self._cnt_resolved = None
                self._cnt_baseline = None
                # FILLER: pump one art every ~art_interval (owner: ~1s between buttons).
                now = time.time()
                if safe and self.arts[mode] and now - last_filler >= art_interval:
                    a = self.arts[mode]
                    self._press(a[self._filler_i % len(a)])
                    self._filler_i += 1
                    last_filler = now
                if now - last_done >= done_every:
                    last_done = now
                    if use_log:
                        # AUTHORITATIVE completion: the log shows a created item. Pixels
                        # (a Create/Begin button on screen) are NOT — they also show before
                        # a craft starts, which is what false-DONE'd a craft that never ran.
                        if self._craft_completed():
                            self.done = True
                            self.log(f"reflex: DONE via log ({self.reactions} success, {self.fails} fail)")
                            return True
                        # Missing-materials bail: no counter EVER fired, a start button is
                        # still up well past the grace window, and nothing was logged -> the
                        # craft never started. Bail now instead of waiting out max_t.
                        if not saw_active and now - t0 >= active_grace and self._done(sct):
                            self.log(f"reflex: no counter in {active_grace:.0f}s, start button up, "
                                     f"nothing created in log — craft didn't start (missing mats), bailing")
                            return False
                    else:
                        # No EQ2 log available -> degraded pixel fallback (the old heuristic).
                        if not saw_active:
                            if now - t0 >= active_grace:
                                if self._done(sct):
                                    self.log(f"reflex: no counter in {active_grace:.0f}s AND start "
                                             f"button present — craft didn't start (missing mats), bailing")
                                    return False
                                self.log(f"reflex: no counter in {active_grace:.0f}s but craft IS "
                                         f"running (no start button) — continuing as active")
                                saw_active = True
                        elif self._done(sct):
                            self.done = True
                            self.log(f"reflex: done ({self.reactions} success, {self.fails} fail)")
                            return True
                time.sleep(loop_sleep)
        self.log(f"reflex: stopped/timeout ({self.reactions} success, {self.fails} fail)")
        return False
