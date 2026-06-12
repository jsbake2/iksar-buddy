"""Defiler decision loop (PROJECT.md §5).

Priority order — items 1-3 are state-MAINTENANCE checks (the reason Defiler
automates well), not reactions:
  1. Cure pending          -> cure by detrimental type
  2. Tank ward absent/down  -> recast    (THE HEARTBEAT)
  3. Group ward down + AE   -> group ward
  4. Tank below emergency through wards (rare) -> direct heal
  5. OOC + prepull flag     -> re-debuff / restore wards / regen

The loop is pure: (WorldState, Config, State) -> Action|None. The agent applies
the action through the chat-safety guard; the brain never injects directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import State


@dataclass
class Member:
    slot: int
    hp: float = 1.0          # 0..1 from pixel scanline
    ward: bool = True        # ward icon present
    dead: bool = False
    power: float = 1.0       # 0..1 (display + future power-aware decisions)
    detriments: list = field(default_factory=list)  # cell indices currently lit
    cure: bool = False       # has >=1 CURABLE detriment (generic cure trigger)
    rez_sick: bool = False   # within post-revive window; detriments are uncurable


@dataclass
class WorldState:
    members: list[Member] = field(default_factory=list)
    own_power: float = 1.0
    casting: bool = False            # mid-cast: don't double-cast
    pending_cures: list[str] = field(default_factory=list)  # detrimental types
    ae_incoming: bool = False
    group_ward_up: bool = True
    prepull: bool = False
    chat_safe: bool = True           # focus provably on game world

    def member(self, slot: int) -> Member | None:
        for m in self.members:
            if m.slot == slot:
                return m
        return None


@dataclass
class Action:
    role: str                # ability role -> resolved to a key by the agent
    target_slot: int | None  # group slot to target first (None = current target)
    reason: str


def decide(world: WorldState, cfg, state: State) -> Action | None:
    """Return the single highest-priority action, or None to idle."""
    # Hard gates first.
    if not world.chat_safe:
        return None          # fail-closed: never act if focus is unproven
    if world.casting:
        return None          # already casting; don't interrupt/queue-spam

    tank_slot = int(cfg.ability_map.get("tank_slot", 0))
    th = cfg.thresholds
    cure_order = th.get("cure_priority", [])

    # 1) Cure pending (resolve by configured priority).
    if world.pending_cures:
        for ctype in cure_order:
            if ctype in world.pending_cures:
                return Action(f"cure_{ctype}", None, f"cure {ctype}")
        # unknown type still pending -> take the first
        return Action(f"cure_{world.pending_cures[0]}", None, "cure (unordered)")

    if state in (State.IN_COMBAT, State.WIPE_RECOVERY):
        tank = world.member(tank_slot)

        # 2) Tank ward absent/depleted -> recast. THE HEARTBEAT.
        if tank is not None and not tank.ward:
            return Action("ward", tank_slot, "tank ward down")

        # 3) Group ward down + AE incoming -> group ward.
        if th.get("group_ward_on_ae", True) and world.ae_incoming and not world.group_ward_up:
            return Action("group_ward", None, "AE incoming, group ward down")

        # 4) Tank below emergency through wards (rare path) -> direct heal.
        emer = float(th.get("tank_emergency_hp", 0.35))
        if tank is not None and tank.hp < emer:
            return Action("direct_heal", tank_slot, f"tank {tank.hp:.0%} < emergency")

    # 5) OOC prepull maintenance.
    if state == State.OOC and world.prepull:
        tank = world.member(tank_slot)
        if tank is not None and not tank.ward:
            return Action("ward", tank_slot, "prepull: restore tank ward")
        if not world.group_ward_up:
            return Action("group_ward", None, "prepull: restore group ward")
        return Action("debuff", None, "prepull: pre-debuff incoming")

    return None
