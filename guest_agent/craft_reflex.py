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
    def run(self) -> bool:
        """React until the craft is DONE (repeat/Begin/Create reappears) or stop/timeout.
        Returns True on a clean completion. The host already confirmed the craft is
        RUNNING before handing off, so we start in the active state and watch for done."""
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
                    if self._cnt_resolved is None and safe and now - self._cnt_last_press >= press_interval:
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
                    if not saw_active:
                        # Never saw a counter -> the craft never went active (recipe couldn't
                        # start: missing materials / Begin disabled). Don't treat the loaded
                        # 'Create' button as 'done' (the false-complete that railed chemistry).
                        if now - t0 >= active_grace:
                            self.log(f"reflex: no counter in {active_grace:.0f}s — craft not "
                                     f"active (missing mats / didn't start), bailing")
                            return False
                    elif self._done(sct):
                        self.done = True
                        self.log(f"reflex: done ({self.reactions} success, {self.fails} fail)")
                        return True
                time.sleep(loop_sleep)
        self.log(f"reflex: stopped/timeout ({self.reactions} success, {self.fails} fail)")
        return False
