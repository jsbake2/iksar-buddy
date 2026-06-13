"""Defiler decision loop (PROJECT.md §5) — tiered, mana-aware healing.

Priority (highest first); returns ONE action per cycle, the most urgent:
  1. EMERGENCY heal   -> dire hp, ANY member; ignores mana, cancels current cast
  2. Cure pending     -> generic cure, TANK FIRST (group cure if many)
  3. Group crit heal  -> >= group_critical_count members critical; ignores mana
  4. CRITICAL heal    -> low hp single target; ignores mana
  5. Tank ward        -> ward down; the Defiler heartbeat (proactive mitigation)
  6. Group ward       -> AE incoming + group ward down
  7. Group heal       -> >= group_heal_count hurt AND mana ok
  8. STANDARD heal    -> hurt single target; SKIPPED when low on mana (conserve)
  9. OOC prepull      -> restore wards / pre-debuff

Mana rule (from the prior tool): critical/emergency heals fire regardless of
power; standard heals are skipped below mana_floor so power is saved for the
hits that matter. Heal TIERS fall back gracefully when the higher-tier spell
isn't mapped yet (emergency -> critical -> standard direct heal).

Pure: (WorldState, Config, State) -> Action|None. The agent applies the action
through the chat-safety guard; the brain never injects directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .state import State


@dataclass
class Member:
    slot: int
    hp: float = 1.0          # 0..1
    ward: bool = True        # ward icon present
    dead: bool = False
    power: float = 1.0       # 0..1
    detriments: list = field(default_factory=list)
    cure: bool = False       # has >=1 CURABLE detriment (generic cure trigger)
    rez_sick: bool = False   # post-revive: detriments are uncurable, don't cure


@dataclass
class WorldState:
    members: list[Member] = field(default_factory=list)   # present members only
    own_power: float = 1.0
    casting: bool = False
    pending_cures: list[str] = field(default_factory=list)  # legacy; per-member m.cure preferred
    ae_incoming: bool = False
    group_ward_up: bool = True
    prepull: bool = False
    chat_safe: bool = True

    def member(self, slot: int) -> Member | None:
        return next((m for m in self.members if m.slot == slot), None)


@dataclass
class Action:
    role: str
    target_slot: int | None
    reason: str


def _abilities(cfg) -> dict:
    return cfg.ability_map.get("abilities", {}) or {}


def _is_mapped(cfg, role: str) -> bool:
    key = (_abilities(cfg).get(role) or {}).get("key", "")
    return bool(key) and key != "none"


def _first_mapped(cfg, *roles: str) -> str | None:
    """First role with a real key — the heal-tier fallback chain."""
    return next((r for r in roles if _is_mapped(cfg, r)), None)


def _lowest(members, tank_slot):
    """Lowest-hp member; ties broken toward the tank."""
    return min(members, key=lambda m: (m.hp, m.slot != tank_slot))


def decide(world: WorldState, cfg, state: State) -> Action | None:
    if not world.chat_safe:
        return None                      # fail-closed: never act unless focus is proven

    th = cfg.thresholds
    tank_slot = int(cfg.ability_map.get("tank_slot", 0))
    # proactive-mitigation roles depend on the active profile: a Defiler refreshes
    # a WARD, a Fury refreshes a HoT. `tank.ward` doubles as "maintenance up?".
    mr = getattr(cfg, "maint_role", "ward")
    gmr = getattr(cfg, "group_maint_role", "group_ward")
    hp_std = float(th.get("hp_standard", 0.85))
    hp_cri = float(th.get("hp_critical", 0.50))
    hp_emer = float(th.get("hp_emergency", 0.25))
    mana_floor = float(th.get("mana_floor", 0.30))
    grp_heal_n = int(th.get("group_heal_count", 2))
    grp_cri_n = int(th.get("group_critical_count", 3))
    mana_ok = world.own_power >= mana_floor

    alive = [m for m in world.members if not m.dead]
    hurt = [m for m in alive if m.hp < hp_std]
    critical = [m for m in alive if m.hp < hp_cri]
    emergency = [m for m in alive if m.hp < hp_emer]
    tank = world.member(tank_slot)
    combat = state in (State.IN_COMBAT, State.WIPE_RECOVERY)

    # 1) EMERGENCY heal — ignores mana, and runs even mid-cast (it cancels it).
    if emergency:
        role = _first_mapped(cfg, "emergency_heal", "critical_heal", "direct_heal")
        if role:
            t = _lowest(emergency, tank_slot)
            return Action(role, t.slot, f"EMERGENCY heal slot{t.slot} {t.hp:.0%}")

    # Everything below respects an in-progress cast (don't interrupt a normal one).
    if world.casting:
        return None

    # 2) Cure pending — generic cure, TANK FIRST (group cure if many afflicted).
    cure_targets = [m for m in alive if m.cure and not m.rez_sick]
    if cure_targets:
        if _is_mapped(cfg, "group_cure") and len(cure_targets) >= grp_heal_n:
            return Action("group_cure", None, f"group cure ({len(cure_targets)})")
        if _is_mapped(cfg, "cure"):
            t = world.member(tank_slot) if (tank and tank in cure_targets) else cure_targets[0]
            return Action("cure", t.slot, f"cure slot{t.slot}")

    if combat:
        # 3) Group critical heal — many in critical; ignore mana.
        if len(critical) >= grp_cri_n and len(alive) > 1 and _is_mapped(cfg, "group_heal"):
            return Action("group_heal", None, f"group heal ({len(critical)} critical)")

        # 4) CRITICAL single heal — ignore mana.
        if critical:
            role = _first_mapped(cfg, "critical_heal", "direct_heal")
            if role:
                t = _lowest(critical, tank_slot)
                return Action(role, t.slot, f"critical heal slot{t.slot} {t.hp:.0%}")

        # 5) Tank maintenance heartbeat (Defiler ward / Fury HoT).
        if tank is not None and not tank.ward and _is_mapped(cfg, mr):
            return Action(mr, tank_slot, f"tank {mr} down")

        # 6) Group maintenance on AE (group ward / group HoT).
        if th.get("group_ward_on_ae", True) and world.ae_incoming \
                and not world.group_ward_up and _is_mapped(cfg, gmr):
            return Action(gmr, None, f"AE incoming, {gmr} down")

        # 7) Group standard heal — enough hurt AND mana ok.
        if mana_ok and len(hurt) >= grp_heal_n and len(alive) > 1 and _is_mapped(cfg, "group_heal"):
            return Action("group_heal", None, f"group heal ({len(hurt)} hurt)")

        # 8) STANDARD single heal — conserve mana: skipped when low.
        if hurt and mana_ok and _is_mapped(cfg, "direct_heal"):
            t = _lowest(hurt, tank_slot)
            return Action("direct_heal", t.slot, f"heal slot{t.slot} {t.hp:.0%}")
        # (low mana -> standard heals deliberately skipped; emergency/critical above already ran)

    # 9) OOC prepull maintenance.
    if state == State.OOC and world.prepull:
        if tank is not None and not tank.ward and _is_mapped(cfg, mr):
            return Action(mr, tank_slot, f"prepull: restore tank {mr}")
        if not world.group_ward_up and _is_mapped(cfg, gmr):
            return Action(gmr, None, f"prepull: restore {gmr}")
        if _is_mapped(cfg, "debuff"):
            return Action("debuff", None, "prepull: pre-debuff incoming")

    return None
