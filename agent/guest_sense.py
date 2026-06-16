"""Guest-side sensing — the host_sensor logic over an in-guest mss frame.

host_sensor.py reads self/members/detriments off a `pix` lookup (it never cares
whether the pixels came from virsh or mss). So we wrap the mss numpy frame in a
tiny accessor (FramePix) and run the SAME geometry/logic, then replicate
host_agent's event processing (combat from HP-drop, rez suppression, chat blink
hysteresis) so the brain receives an identical STATE_EVENT — just at 12 Hz from
inside the VM instead of ~1 Hz host-side. The host path is left untouched.

Geometry constants are copied verbatim from agent/host_sensor.py (keep in sync).
"""
from __future__ import annotations

import time

import numpy as np

# ---- geometry (verbatim from host_sensor.py) --------------------------------
SELF_TRACK = (19, 128)
GRP_TRACK = (33, 139)
PWR_BASE, PITCH, SLOTS = 128, 75, 6
SEARCH = 4
HP_PWR_GAP = 8
ROW_DY = 32
CELL_XC = [43, 66, 88, 112, 135]
INSET = 6
IGNORE_SIGNATURES: dict[str, tuple[int, int, int]] = {}
IGNORE_TOL = 40
CHAT_INPUT = (50, 1019, 208, 22)
CHAT_BRIGHT_THRESH = 25
SELF_SCAN = (30, 100)                 # y-range to find the own power row

# ---- event processing constants (from host_agent.py) ------------------------
NAMES = {0: "Jenskin", 1: "Robskin"}
REZ_WINDOW = 240.0
COMBAT_HP_DROP = 0.02
COMBAT_DECAY_S = 5.0
CHAT_HYSTERESIS_S = 3.0


def is_blue(c):  r, g, b = c; return b > 100 and b > r + 20 and b > g
def is_bright(c): r, g, b = c; return (r + g + b) > 90
def is_icon(c):  r, g, b = c; return (r + g + b) > 120


def is_ignored(rgb):
    for name, ref in IGNORE_SIGNATURES.items():
        if sum((a - b) ** 2 for a, b in zip(rgb, ref)) <= IGNORE_TOL ** 2:
            return name
    return None


class FramePix:
    """`pix.get((x,y), default)` over a full RGB numpy frame — the same interface
    host_sensor's sensing functions use, so they run unchanged."""
    __slots__ = ("f", "h", "w")

    def __init__(self, frame) -> None:
        self.f = frame
        self.h, self.w = frame.shape[:2]

    def get(self, xy, default=(0, 0, 0)):
        x, y = xy
        if 0 <= x < self.w and 0 <= y < self.h:
            px = self.f[y, x]
            return (int(px[0]), int(px[1]), int(px[2]))
        return default


# ---- sensing (ported from HostSensor; pix-only, so identical math) ----------
def _power_row(pix, track, y_hint):
    tx0, tx1 = track
    best_y, best_n = None, 0
    for y in range(y_hint - SEARCH, y_hint + SEARCH + 1):
        n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def _power_row_scan(pix, track, y0, y1):
    tx0, tx1 = track
    best_y, best_n = None, 0
    for y in range(y0, y1):
        n = sum(1 for x in range(tx0, tx1) if is_blue(pix.get((x, y))))
        if n > best_n:
            best_y, best_n = y, n
    return best_y if best_n >= 12 else None


def _fill(pix, track, y) -> int:
    tx0, tx1 = track
    filled = sum(1 for x in range(tx0, tx1) if is_bright(pix.get((x, y))))
    return round(100 * filled / (tx1 - tx0))


def _detriments(pix, row_y):
    cells = []
    for ci, xc in enumerate(CELL_XC):
        box = [pix.get((x, y))
               for x in range(xc - INSET, xc + INSET + 1)
               for y in range(row_y - INSET, row_y + INSET + 1)]
        lit = [c for c in box if is_icon(c)]
        if len(lit) > 0.4 * len(box):
            avg = tuple(sum(c[i] for c in lit) // len(lit) for i in range(3))
            cells.append({"cell": ci, "rgb": list(avg), "ignored": is_ignored(avg)})
    cure = any(c["ignored"] is None for c in cells)
    return cells, cure


def read_self(pix):
    pwr_y = _power_row_scan(pix, SELF_TRACK, SELF_SCAN[0], SELF_SCAN[1])
    if pwr_y is None:
        return None, None
    return _fill(pix, SELF_TRACK, pwr_y - HP_PWR_GAP), _fill(pix, SELF_TRACK, pwr_y)


def read_members(pix):
    out = []
    for slot in range(SLOTS):
        pwr_y = _power_row(pix, GRP_TRACK, PWR_BASE + PITCH * slot)
        if pwr_y is None:
            continue
        hp = _fill(pix, GRP_TRACK, pwr_y - HP_PWR_GAP)
        power = _fill(pix, GRP_TRACK, pwr_y)
        dets, cure = _detriments(pix, pwr_y + ROW_DY)
        out.append({"slot": slot, "hp": hp, "power": power,
                    "dead": hp <= 1, "detriments": dets, "cure": cure})
    return out


def _chat_active(frame) -> bool | None:
    x, y, w, h = CHAT_INPUT
    try:
        reg = frame[y:y + h, x:x + w].astype(np.int32)
        gray = reg.mean(axis=2)
        return int(np.sum(gray > 0.6 * 255)) > CHAT_BRIGHT_THRESH
    except Exception:                  # noqa: BLE001
        return None


def read_world(frame) -> dict:
    """Full sensed world from one mss frame (RGB numpy), host_sensor-shaped."""
    pix = FramePix(frame)
    hp, power = read_self(pix)
    members = read_members(pix)
    return {"own": {"hp": hp, "power": power}, "members": members,
            "chat_safety": {"game_present": power is not None,
                            "chat_active": _chat_active(frame)}}


class EventState:
    """Stateful STATE_EVENT builder — replicates host_agent._to_event (combat from
    HP-drops, rez suppression, chat blink hysteresis)."""

    def __init__(self) -> None:
        self._prev_hp: dict = {}
        self._combat_until = 0.0
        self._chat_busy_until = 0.0
        self._dead_prev: dict = {}
        self._revived_at: dict = {}

    def _rez_suppressed(self, raw) -> set:
        now = time.time()
        sup = set()
        for m in raw:
            slot = m["slot"]
            dead = bool(m.get("dead", False))
            if self._dead_prev.get(slot) and not dead:
                self._revived_at[slot] = now
            self._dead_prev[slot] = dead
            if now - self._revived_at.get(slot, -1e9) < REZ_WINDOW:
                sup.add(slot)
        return sup

    def to_event(self, world: dict, aborted: int = 0) -> dict:
        own = world.get("own") or {}
        raw = world.get("members", [])
        suppressed = self._rez_suppressed(raw)
        members, cure_needed = [], False
        for m in raw:
            slot = m["slot"]
            cure = m.get("cure", False) and slot not in suppressed
            cure_needed = cure_needed or cure
            members.append({
                "slot": slot,
                "hp": (m["hp"] or 0) / 100.0,
                "power": (m["power"] or 0) / 100.0,
                "ward": True,
                "dead": m.get("dead", False),
                "detriments": m.get("detriments", []),
                "cure": cure,
                "rez_sick": slot in suppressed and bool(m.get("detriments")),
            })
        safety = world.get("chat_safety") or {}
        game_present = bool(safety.get("game_present"))
        raw_active = safety.get("chat_active")
        now = time.time()
        cur_hp = {m["slot"]: (m["hp"] or 0) / 100.0 for m in raw}
        cur_hp["own"] = (own.get("hp") or 0) / 100.0
        for k, hp in cur_hp.items():
            prev = self._prev_hp.get(k)
            if prev is not None and prev - hp >= COMBAT_HP_DROP and hp > 0.01:
                self._combat_until = now + COMBAT_DECAY_S
        self._prev_hp = cur_hp
        in_combat = now < self._combat_until
        if raw_active is True or raw_active is None:
            self._chat_busy_until = now + CHAT_HYSTERESIS_S
        chat_busy = now < self._chat_busy_until
        chat_safe = game_present and not chat_busy
        return {
            "members": members,
            "names": {str(k): v for k, v in NAMES.items()},
            "own_power": (own.get("power") or 0) / 100.0,
            "own_hp": (own.get("hp") or 0) / 100.0,
            "casting": False,
            "in_combat": in_combat,
            "pending_cures": ["generic"] if cure_needed else [],
            "chat_safe": chat_safe,
            "chat_focus": {"game_present": game_present, "chat_active": chat_busy},
            "aborted_injections": aborted,
        }
