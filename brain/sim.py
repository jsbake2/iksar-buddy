"""Telemetry simulator — drives the dashboard with believable live data when no
real agent is attached.

This is a pure dev/demo aid: it mutates the Telemetry snapshot on a timer so
`python -m brain --sim` shows an animated, plausible sense->decide->act loop
(a 6-member group, fluctuating HP, ward drops/refreshes, detriments appearing
then being cured, own power drain/regen, mana-gating, combat cycling, agent
health jitter, periodic events). It never touches the transport or the agent.

It deliberately mirrors the Defiler maintenance loop so the demo *looks* like
the bot is working: ward drops get refreshed, detriments get cured, the tank
gets healed when it dips through wards.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any

from .telemetry import CURE_TYPES, SLOT_ROLES, Telemetry

# Plausible iksar/evil-aligned roster for the SK+Defiler duo (+filler group).
_NAMES = ["Vethkar", "Grimscale", "Sythiss", "Korbaxx", "Drennika", "Vurzel"]
_ROLE_LABEL = {"healer": "Defiler", "tank": "SK", "support": "Support", "dps": "DPS"}

_STATES = ["OOC", "IN_COMBAT"]


class Simulator:
    """Owns a synthetic WorldState and writes it into Telemetry each tick."""

    def __init__(self, telemetry: Telemetry, tick: float = 0.4) -> None:
        self.t = telemetry
        self.tick = tick
        self._t0 = time.time()
        self._state = "OOC"
        self._state_until = time.time() + random.uniform(6, 12)
        self._power = 1.0
        self._mana_gated = False
        self._casting = False
        self._cast_until = 0.0
        self._aborted = 0
        self._alarms = 0
        self._chat_unsafe_until = 0.0
        self._next_event = time.time() + 3
        self._members = self._init_members()
        # connected synthetic agent; health jitters around these.
        self._latency = 180.0
        self._hz = 12.0
        self._ocr = 0.94

    def _init_members(self) -> list[dict[str, Any]]:
        ms = []
        for i in range(6):
            m = {
                "slot": i,
                "name": _NAMES[i],
                "role": _ROLE_LABEL[SLOT_ROLES[i]],
                "present": True,
                "hp": random.uniform(0.85, 1.0),
                "critical": False,
                "ward": True,
                "dead": False,
                "detriments": [],
            }
            ms.append(m)
        return ms

    # -- main loop ---------------------------------------------------------
    async def run(self) -> None:
        # Prime: a connected agent + roster, so the dashboard is populated at t=0.
        self.t.update(running=True,
                      agent={"connected": True, "latency_ms": self._latency,
                             "capture_hz": self._hz, "ocr_conf": self._ocr,
                             "log_fresh_s": 0.3},
                      vm={"name": "iksar_buddy", "running": True, "ip": "192.168.122.50"})
        self.t.push_event("control", "simulator armed — synthetic telemetry")
        while True:
            self._step()
            await asyncio.sleep(self.tick)

    # -- one tick ----------------------------------------------------------
    def _step(self) -> None:
        now = time.time()
        in_combat = self._state == "IN_COMBAT"

        self._cycle_state(now)
        self._tick_self(now, in_combat)
        self._tick_members(now, in_combat)
        self._tick_chat(now)
        self._maybe_event(now, in_combat)
        self._publish(now)

    def _cycle_state(self, now: float) -> None:
        if now >= self._state_until:
            self._state = "IN_COMBAT" if self._state == "OOC" else "OOC"
            self._state_until = now + random.uniform(7, 16)
            self.t.push_event("control",
                              "combat start" if self._state == "IN_COMBAT" else "combat end")

    def _tick_self(self, now: float, in_combat: bool) -> None:
        # Power drains in combat (casting wards/cures), regens out of combat.
        if in_combat:
            self._power -= random.uniform(0.005, 0.03)
        else:
            self._power += random.uniform(0.01, 0.04)
        self._power = max(0.0, min(1.0, self._power))
        # Mana-gate latches when power is low; clears once recovered.
        if self._power < 0.18:
            self._mana_gated = True
        elif self._power > 0.4:
            self._mana_gated = False
        # Cast bar: brief casts kicked off by the member loop set _cast_until.
        self._casting = now < self._cast_until

    def _begin_cast(self, dur: float = 0.0) -> None:
        self._cast_until = time.time() + (dur or random.uniform(0.5, 1.2))

    def _tick_members(self, now: float, in_combat: bool) -> None:
        for m in self._members:
            if m["dead"]:
                # someone rezzes after a while; bot auto-accepts / rez-loop.
                if random.random() < 0.02:
                    m["dead"] = False
                    m["hp"] = 0.3
                    m["ward"] = True
                    self.t.push_event("rez", f"{m['name']} revived")
                continue

            is_tank = m["role"] == "SK"
            # HP movement.
            if in_combat:
                # incoming dmg; tank takes the brunt, ward soaks first.
                hit = random.uniform(0.0, 0.12 if is_tank else 0.06)
                if m["ward"]:
                    hit *= 0.25  # ward absorbs most of it
                m["hp"] -= hit
            else:
                m["hp"] += random.uniform(0.01, 0.05)
            m["hp"] = max(0.0, min(1.0, m["hp"]))

            # Ward decay/refresh — the Defiler heartbeat.
            if in_combat and m["ward"] and random.random() < (0.10 if is_tank else 0.05):
                m["ward"] = False
                self.t.push_event("ward", f"{m['name']} ward faded")
            if not m["ward"] and random.random() < 0.45:
                # bot refreshes the ward
                m["ward"] = True
                self._begin_cast()
                role = "ward" if is_tank else "ward"
                self.t.push_event("cast", f"ward -> {m['name']} ward refreshed")

            # Detriments appear in combat, get cured.
            if in_combat and not m["detriments"] and random.random() < 0.06:
                ctype = random.choice(CURE_TYPES)
                m["detriments"].append(ctype)
                self.t.push_event("detriment", f"{m['name']} afflicted: {ctype}")
            elif m["detriments"] and random.random() < 0.5:
                ctype = m["detriments"].pop(0)
                self._begin_cast()
                self.t.push_event("cure", f"cure_{ctype} -> {m['name']}")

            # Direct heal when the tank dips through wards (rare emergency path).
            if is_tank and m["hp"] < 0.4 and random.random() < 0.6:
                m["hp"] = min(1.0, m["hp"] + random.uniform(0.25, 0.4))
                self._begin_cast()
                self.t.push_event("cast", f"direct_heal -> {m['name']} emergency")

            # Rare death (deep combat) -> rez loop fodder.
            if in_combat and m["hp"] <= 0.02 and random.random() < 0.25:
                m["dead"] = True
                m["ward"] = False
                m["hp"] = 0.0
                self.t.push_event("death", f"{m['name']} died")

            m["critical"] = (not m["dead"]) and m["hp"] < 0.25

    def _tick_chat(self, now: float) -> None:
        # Occasionally the chat input opens (focus leak risk); the guard aborts
        # the injection, ESCs, logs an alarm — the §6.2 safety surface.
        if now < self._chat_unsafe_until:
            return
        if random.random() < 0.015:
            self._chat_unsafe_until = now + random.uniform(1.5, 3.5)
            self._aborted += 1
            self._alarms += 1
            self.t.push_event("alarm", "chat input focus detected — injection aborted, ESC sent")

    def _maybe_event(self, now: float, in_combat: bool) -> None:
        if now < self._next_event:
            return
        self._next_event = now + random.uniform(4, 9)
        pool = ["log: target engaged", "log: you have stopped following",
                "follow re-asserted", "debuff -> slow applied", "log: heartbeat ok"]
        self.t.push_event("log", random.choice(pool))

    def _publish(self, now: float) -> None:
        # jitter the agent health a little so the sensor panel breathes.
        self._latency += random.uniform(-12, 12)
        self._latency = max(90.0, min(420.0, self._latency))
        self._hz += random.uniform(-0.6, 0.6)
        self._hz = max(8.0, min(15.0, self._hz))
        self._ocr += random.uniform(-0.02, 0.02)
        self._ocr = max(0.80, min(0.99, self._ocr))

        chat_safe = now >= self._chat_unsafe_until
        members = [dict(m) for m in self._members]
        self.t.snapshot["members"] = members
        self.t.snapshot["group_size"] = sum(1 for m in members if m["present"])
        self.t.update(
            state=self._state,
            running=True,
            own={"power": round(self._power, 3), "casting": self._casting,
                 "mana_gated": self._mana_gated},
            agent={"connected": True, "latency_ms": round(self._latency, 1),
                   "capture_hz": round(self._hz, 1), "ocr_conf": round(self._ocr, 3),
                   "log_fresh_s": round(random.uniform(0.1, 0.8), 2)},
            chat_focus={"safe": chat_safe, "aborted_injections": self._aborted,
                        "alarms": self._alarms},
        )


async def run_sim(telemetry: Telemetry) -> None:
    await Simulator(telemetry).run()
