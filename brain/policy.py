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


def _key(cfg, role: str) -> str:
    return (_abilities(cfg).get(role) or {}).get("key", "")


def _lowest(members, tank_slot):
    """Lowest-hp member; ties broken toward the tank."""
    return min(members, key=lambda m: (m.hp, m.slot != tank_slot))


def decide(world: WorldState, cfg, state: State) -> list[Action]:
    """Priority-ordered BURST for this tick. The brain fires the FIRST entry that's off
    cooldown/GCD, so across successive GCDs it pours the WHOLE stack — when someone is low
    it casts emergency->critical->regular heals AND the ward(s) (de-duped by key, so a tier
    that shares a key isn't queued twice) until they recover, instead of one heal per tick
    with idle gaps. [] = nothing this tick (brain falls to its ward/assist heartbeats)."""
    if not world.chat_safe:
        return []                        # fail-closed: never act unless focus is proven

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

    out: list[Action] = []
    seen: set = set()

    def add(role, slot, why):
        """Append a mapped ability, de-duped by (resolved key, slot) so tiers that share a
        key (e.g. emergency/critical/direct all -> '4' until the bigger spells are learned)
        aren't queued multiple times and whiffed."""
        if not _is_mapped(cfg, role):
            return
        k = (_key(cfg, role), slot)
        if k in seen:
            return
        seen.add(k)
        out.append(Action(role, slot, why))

    # 1) EMERGENCY — lowest member below the emergency line. Pour the FULL burst: every heal
    #    tier + the ward(s). Ignores mana, runs even mid-cast (it cancels a normal one).
    if emergency:
        t = _lowest(emergency, tank_slot)
        add("emergency_heal", t.slot, f"EMERGENCY heal s{t.slot} {t.hp:.0%}")
        add("critical_heal",  t.slot, f"emergency: critical heal s{t.slot}")
        add("direct_heal",    t.slot, f"emergency: heal s{t.slot}")
        if len(alive) > 1:
            add("group_heal", None, "emergency group heal")
        add("emergency_ward", tank_slot, "EMERGENCY ward")
        add(mr,               tank_slot, f"emergency {mr}")

    # Cures + lower tiers respect an in-progress (non-emergency) cast.
    if not world.casting:
        # 2) CURES — generic, tank first (group cure if many afflicted).
        cure_targets = [m for m in alive if m.cure and not m.rez_sick]
        if cure_targets:
            if len(cure_targets) >= grp_heal_n:
                add("group_cure", None, f"group cure ({len(cure_targets)})")
            ct = world.member(tank_slot) if (tank and tank in cure_targets) else cure_targets[0]
            add("cure", ct.slot, f"cure s{ct.slot}")

        if combat:
            # 3) CRITICAL members — critical+regular heals + the ward stack. Ignore mana.
            if critical:
                t = _lowest(critical, tank_slot)
                if len(critical) >= grp_cri_n and len(alive) > 1:
                    add("group_heal", None, f"group heal ({len(critical)} critical)")
                add("critical_heal", t.slot, f"critical heal s{t.slot} {t.hp:.0%}")
                add("direct_heal",   t.slot, f"critical: heal s{t.slot}")
                add("emergency_ward", tank_slot, "critical: emergency ward")
                add(mr,               tank_slot, f"critical {mr}")

            # 4) Keep the tank ward up (proactive mitigation).
            if tank is not None and not tank.ward:
                add(mr, tank_slot, f"tank {mr} down")

            # 5) Group ward on AE.
            if th.get("group_ward_on_ae", True) and world.ae_incoming and not world.group_ward_up:
                add(gmr, None, f"AE incoming, {gmr} down")

            # 6) STANDARD heals — mana-gated (conserve power for the hits that matter).
            if hurt and mana_ok:
                if len(hurt) >= grp_heal_n and len(alive) > 1:
                    add("group_heal", None, f"group heal ({len(hurt)} hurt)")
                t = _lowest(hurt, tank_slot)
                add("direct_heal", t.slot, f"heal s{t.slot} {t.hp:.0%}")

    # 7) OOC prepull maintenance.
    if state == State.OOC and world.prepull:
        if tank is not None and not tank.ward:
            add(mr, tank_slot, f"prepull: restore tank {mr}")
        if not world.group_ward_up:
            add(gmr, None, f"prepull: restore {gmr}")
        add("debuff", None, "prepull: pre-debuff incoming")

    return out
