"""heal_reflex — the Defiler healer loop, run LOCALLY in the guest (the fast path).

A faithful port of the dino's two_box/server_execute.py monitor(): read the group
HP / mana / cure-indicator pixels with mss (~2-5ms each), decide heals/wards/cures
by the same priority ladder, and press the configured ability keys with
pydirectinput — so a heal lands in tens of ms instead of the host's ~170ms virsh
round-trip. Same agent + comms as Forge; this is just a second ruleset.

Decision ladder (per tick, dino order):
  1. group CRITICAL heal  if (all damaged) or (>=3 damaged), group>1
  2. group STANDARD heal  if mana ok and >=2 damaged, group>1
  3. single CRITICAL heals (per member whose critical pixel shows critical)
  4. single STANDARD heals (per damaged member, if mana ok)
  5. CURES (nox/ele/tra per member; tank first)

Ruleset (sent by the host 'heal' command, or loaded from C:\\ib\\agent\\heal.json):
  pixels.self_mana / group_check / standard[i] / critical[i]  (loc + clr [+ alt_clr],
  standard members also carry nox/ele/tra cure-indicator locs)
  actions.{name}.action  — comma list of tokens: "5,4" / "Alt#1,4" / "pause_7.25"
  tick, tol, cure_present_clr  — loop period, pixel-match tolerance, ailment color test

Chat-safety invariant: like Forge, never press unless in-world + chat clear (optional,
enabled when game_present/chat_input regions are provided).
"""
from __future__ import annotations

import time

import mss
import numpy as np
import pydirectinput

try:
    import cv2
except Exception:                      # noqa: BLE001
    cv2 = None
try:
    import pygetwindow as gw
except Exception:                      # noqa: BLE001
    gw = None

pydirectinput.PAUSE = 0
pydirectinput.FAILSAFE = False

_MOD = {"alt": "alt", "ctrl": "ctrl", "control": "ctrl", "shift": "shift"}


def _pixel(sct, x, y):
    raw = sct.grab({"left": int(x), "top": int(y), "width": 1, "height": 1})
    px = np.asarray(raw)[0, 0]            # BGRA
    return (int(px[2]), int(px[1]), int(px[0]))


def _near(rgb, expected, tol):
    return all(abs(int(rgb[i]) - int(expected[i])) <= tol for i in range(3))


class HealReflex:
    def __init__(self, ruleset: dict, log, should_stop) -> None:
        self.r = ruleset or {}
        self.log = log
        self.should_stop = should_stop
        self.px = self.r.get("pixels", {}) or {}
        self.actions = self.r.get("actions", {}) or {}
        self.tol = int(self.r.get("tol", 12))
        self.tick = float(self.r.get("tick", 0.1))
        self.heals = 0
        self.cures = 0

    # -- pixel predicates --------------------------------------------------
    def _is(self, sct, info) -> bool:
        """True if the pixel at info.loc matches info.clr (or alt_clr) — the dino's
        check_pixel_state. For a health bar: True = HEALTHY; for group_check: True = EMPTY."""
        loc = info.get("loc")
        if not loc:
            return True
        rgb = _pixel(sct, loc[0], loc[1])
        if _near(rgb, info.get("clr", [0, 0, 0]), self.tol):
            return True
        alt = info.get("alt_clr")
        return bool(alt and _near(rgb, alt, self.tol))

    def _group_size(self, sct) -> int:
        gc = self.px.get("group_check", {}) or {}
        # a slot is OCCUPIED when its pixel is NOT the empty color (dino: not check_pixel_state)
        return sum(1 for k in gc if not self._is(sct, gc[k]))

    def _ailment(self, sct, loc) -> bool:
        """Cure indicator present = the nox/ele/tra pixel is NOT the 'clear' color."""
        if not loc:
            return False
        clr = self.r.get("cure_present_clr", [0, 0, 0])
        return not _near(_pixel(sct, loc[0], loc[1]), clr, self.tol)

    # -- chat safety (optional, same as craft) -----------------------------
    def _safe(self, sct) -> bool:
        gp = self.r.get("game_present", {}) or {}
        ci = self.r.get("chat_input", {}) or {}
        if not gp.get("region") and not ci.get("region"):
            return True                   # not configured -> don't block (dino had none)
        if cv2 is None:
            return True
        reg = gp.get("region")
        if reg:
            raw = sct.grab({"left": reg["x"], "top": reg["y"], "width": reg["w"], "height": reg["h"]})
            arr = np.asarray(raw)[:, :, :3].reshape(-1, 3)[:, ::-1]
            blue = gp.get("blue", [115, 115, 230]); tol = int(gp.get("tolerance", 45))
            if int(np.sum(np.all(np.abs(arr.astype(int) - blue) <= tol, axis=1))) < int(gp.get("min_pixels", 20)):
                return False
        creg = ci.get("region")
        if creg:
            raw = sct.grab({"left": creg["x"], "top": creg["y"], "width": creg["w"], "height": creg["h"]})
            g = cv2.cvtColor(np.asarray(raw)[:, :, :3], cv2.COLOR_BGR2GRAY)
            if int(np.sum(g > 0.6 * 255)) > int(ci.get("bright_threshold", 25)):
                return False
        return True

    # -- action execution (dino execute_command) ---------------------------
    def execute(self, action: str) -> None:
        for tok in str(action).replace("\n", "").split(","):
            tok = tok.strip()
            if not tok or tok == "exit":
                continue
            if tok.startswith("pause_"):
                try:
                    time.sleep(float(tok.split("_")[1]))
                except (ValueError, IndexError):
                    pass
            elif "#" in tok:                          # modifier combo, e.g. Alt#1
                mod, _, key = tok.partition("#")
                m = _MOD.get(mod.lower())
                if m:
                    pydirectinput.keyDown(m); time.sleep(0.02)
                    pydirectinput.press(key.lower()); time.sleep(0.02)
                    pydirectinput.keyUp(m)
                else:
                    pydirectinput.press(key.lower())
            elif tok.startswith("key_"):
                pydirectinput.press(tok.split("_", 1)[1].lower())
            else:
                pydirectinput.press(tok.lower())
            time.sleep(0.05)

    def _act(self, name: str) -> bool:
        a = self.actions.get(name)
        if a and a.get("action"):
            self.execute(a["action"])
            return True
        return False

    # -- the monitor tick (dino logic) -------------------------------------
    def _monitor(self, sct) -> None:
        std = self.px.get("standard", {}) or {}
        crit = self.px.get("critical", {}) or {}
        size = self._group_size(sct)
        mana_ok = self._is(sct, self.px.get("self_mana", {}))
        idxs = sorted(int(k) for k in std)
        damaged = [i for i in idxs if i < max(1, size) and not self._is(sct, std[str(i)])]

        # 1/2: group heals
        if size > 1 and (len(damaged) == size or len(damaged) >= 3):
            if self._act("heal_group_cri"):
                self.heals += 1; return
        if mana_ok and size > 1 and len(damaged) >= 2:
            if self._act("heal_group_std"):
                self.heals += 1; return

        healed = set()
        # 3: single critical heals
        for i in sorted(int(k) for k in crit):
            if i < max(1, size) and not self._is(sct, crit[str(i)]):
                if self._act(crit[str(i)].get("action", "")):
                    self.heals += 1; healed.add(i)
        # 4: single standard heals (mana-gated)
        if mana_ok:
            for i in damaged:
                if i not in healed and self._act(std[str(i)].get("action", "")):
                    self.heals += 1
        # 5: cures (tank=0 first)
        cure_list = []
        for i in idxs:
            if i >= max(1, size):
                continue
            s = std[str(i)]
            for ail, base in (("nox", "cure_nox"), ("ele", "cure_ele"), ("tra", "cure_tra")):
                if self._ailment(sct, s.get(ail)):
                    cure_list.append((i, f"{base}_{i}"))
        cure_list.sort(key=lambda c: c[0] != 0)
        for _i, name in cure_list:
            if self._act(name):
                self.cures += 1

    # -- loop --------------------------------------------------------------
    def run(self) -> bool:
        if gw is not None:
            try:
                w = gw.getWindowsWithTitle("EverQuest II")
                if w:
                    w[0].activate()
            except Exception:                # noqa: BLE001
                pass
        self.log(f"heal_reflex: monitoring (tick={self.tick}s)")
        with mss.mss() as sct:
            while not self.should_stop():
                try:
                    if self._safe(sct):
                        self._monitor(sct)
                except Exception as e:       # noqa: BLE001 — never die mid-fight
                    self.log(f"heal tick error: {e}")
                time.sleep(self.tick)
        self.log(f"heal_reflex: stopped ({self.heals} heals, {self.cures} cures)")
        return True
