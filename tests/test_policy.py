"""Tests for the tiered, mana-aware Defiler decision loop."""
from brain.config import Config
from brain.policy import Member, WorldState, decide
from brain.state import State


def _cfg(tank_slot=0, **override_keys):
    """A config with the common abilities mapped. critical_heal/emergency_heal are
    blank by default (the owner doesn't have them yet) so the fallback chain is
    exercised; pass e.g. critical_heal='3' to map them."""
    c = Config()
    keys = {"attack": "1", "direct_heal": "4", "ward": "5", "group_ward": "9",
            "group_heal": "8", "cure": "0", "group_cure": "none", "debuff": "3",
            "critical_heal": "", "emergency_heal": ""}
    keys.update(override_keys)
    c.ability_map = {"tank_slot": tank_slot,
                     "abilities": {k: {"key": v} for k, v in keys.items()}}
    c.thresholds = {"hp_standard": 0.85, "hp_critical": 0.5, "hp_emergency": 0.25,
                    "mana_floor": 0.3, "group_heal_count": 2, "group_critical_count": 3,
                    "group_ward_on_ae": True, "auto_rez": True,
                    "rez_priority": ["tank", "healer", "support", "dps"]}
    return c


def test_fail_closed_when_chat_unsafe():
    w = WorldState(members=[Member(0, hp=0.1, ward=False)], chat_safe=False)
    assert decide(w, _cfg(), State.IN_COMBAT) == []


def test_casting_blocks_non_emergency():
    w = WorldState(members=[Member(0, hp=1.0, ward=False)], casting=True)
    assert decide(w, _cfg(), State.IN_COMBAT) == []


def test_emergency_heal_runs_even_while_casting():
    # hp below emergency -> heal fires through a cast (it cancels it). No emergency
    # spell mapped -> falls back to direct_heal.
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], casting=True, group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a[0].role == "direct_heal" and a[0].target_slot == 0


def test_emergency_prefers_mapped_higher_tier():
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], group_ward_up=True)
    a = decide(w, _cfg(emergency_heal="Alt+0", critical_heal="3"), State.IN_COMBAT)
    assert a[0].role == "emergency_heal"


def test_cure_is_tank_first():
    w = WorldState(members=[Member(0, cure=True), Member(1, cure=True)])
    a = decide(w, _cfg(tank_slot=0), State.IN_COMBAT)
    assert a[0].role == "cure" and a[0].target_slot == 0
    # tank elsewhere -> still cures the tank first
    a2 = decide(w, _cfg(tank_slot=1), State.IN_COMBAT)
    assert a2[0].role == "cure" and a2[0].target_slot == 1


def test_rez_sick_member_not_cured():
    w = WorldState(members=[Member(0, cure=True, rez_sick=True)])
    assert decide(w, _cfg(), State.IN_COMBAT) == []


def test_critical_heal_ignores_low_mana():
    # critical hp + LOW mana -> still heals (critical/emergency bypass mana).
    w = WorldState(members=[Member(0, hp=0.4, ward=True)], own_power=0.1, group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a[0].role == "direct_heal" and a[0].target_slot == 0   # fallback from critical_heal


def test_standard_heal_skipped_on_low_mana():
    # standard hurt + LOW mana -> SKIP (conserve mana). Healthy ward -> idle.
    w = WorldState(members=[Member(0, hp=0.7, ward=True)], own_power=0.1, group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT) == []
    # same but mana ok -> standard heal
    w2 = WorldState(members=[Member(0, hp=0.7, ward=True)], own_power=1.0, group_ward_up=True)
    assert decide(w2, _cfg(), State.IN_COMBAT)[0].role == "direct_heal"


def test_ward_heartbeat():
    w = WorldState(members=[Member(0, hp=1.0, ward=False)], group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a[0].role == "ward" and a[0].target_slot == 0


def test_group_ward_on_ae():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], ae_incoming=True, group_ward_up=False)
    assert decide(w, _cfg(), State.IN_COMBAT)[0].role == "group_ward"


def test_group_heal_when_enough_hurt():
    # two members hurt (standard) + mana ok -> group heal
    w = WorldState(members=[Member(0, hp=0.7, ward=True), Member(1, hp=0.7, ward=True)],
                   own_power=1.0, group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT)[0].role == "group_heal"


def test_idle_when_healthy():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT) == []


def test_burst_stacks_heals_and_wards_when_critical():
    # tank critical, distinct heals + emergency ward mapped -> the BURST carries the heal
    # tiers AND the ward(s), not a single heal.
    w = WorldState(members=[Member(0, hp=0.4, ward=True)], own_power=1.0, group_ward_up=True)
    roles = [a.role for a in decide(w, _cfg(critical_heal="3", emergency_ward="Alt+7"), State.IN_COMBAT)]
    assert "critical_heal" in roles and "direct_heal" in roles
    assert "emergency_ward" in roles or "ward" in roles


def _rez_cfg(**kw):
    c = _cfg(**kw)
    c.ability_map["abilities"]["rez"] = {"key": "Ctrl+1"}
    c.ability_map["slot_roles"] = ["healer", "tank", "dps", "dps", "support", "support"]
    c.ability_map["group_target_keys"] = ["F1", "F2", "F3", "F4", "F5", "F6"]
    return c


def test_auto_rez_fires_on_dead_member():
    # a downed DPS + everyone else fine -> rez it, targeting its slot.
    w = WorldState(members=[Member(0, hp=1.0, ward=True), Member(2, dead=True)],
                   group_ward_up=True)
    a = decide(w, _rez_cfg(), State.IN_COMBAT)
    assert a[0].role == "rez" and a[0].target_slot == 2


def test_auto_rez_prefers_tank_by_priority():
    # tank (slot 1) and a dps (slot 2) both down -> rez the TANK first (rez_priority).
    w = WorldState(members=[Member(0, hp=1.0, ward=True), Member(1, dead=True),
                            Member(2, dead=True)], group_ward_up=True)
    a = decide(w, _rez_cfg(), State.IN_COMBAT)
    assert a[0].role == "rez" and a[0].target_slot == 1


def test_living_emergency_blocks_rez():
    # someone's down BUT a living member is in emergency -> heal the living one, hold
    # the rez (a locked cast over a dying teammate wipes the group).
    w = WorldState(members=[Member(0, hp=0.15, ward=True), Member(2, dead=True)],
                   group_ward_up=True)
    a = decide(w, _rez_cfg(), State.IN_COMBAT)
    assert a[0].role != "rez"
    assert not any(x.role == "rez" for x in a)


def test_auto_rez_outranks_routine_heal():
    # a hurt (non-emergency) living member AND a dead member -> rez ranks first.
    w = WorldState(members=[Member(0, hp=0.7, ward=True), Member(2, dead=True)],
                   own_power=1.0, group_ward_up=True)
    a = decide(w, _rez_cfg(), State.IN_COMBAT)
    assert a[0].role == "rez"


def test_auto_rez_fires_out_of_combat():
    # post-wipe, OOC, a member down -> still rez (rez isn't combat-gated).
    w = WorldState(members=[Member(0, hp=1.0, ward=True), Member(1, dead=True)],
                   group_ward_up=True)
    assert decide(w, _rez_cfg(), State.OOC)[0].role == "rez"


def test_auto_rez_disabled_by_flag():
    w = WorldState(members=[Member(0, hp=1.0, ward=True), Member(2, dead=True)],
                   group_ward_up=True)
    c = _rez_cfg()
    c.thresholds["auto_rez"] = False
    assert not any(x.role == "rez" for x in decide(w, c, State.IN_COMBAT))


def test_no_rez_when_nobody_dead():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], group_ward_up=True)
    assert not any(x.role == "rez" for x in decide(w, _rez_cfg(), State.IN_COMBAT))


def test_burst_dedups_shared_key():
    # emergency/critical/direct all share key '4' -> ONE heal entry (no whiffed repeats).
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], own_power=1.0, group_ward_up=True)
    burst = decide(w, _cfg(critical_heal="4", emergency_heal="4"), State.IN_COMBAT)
    heals = [a for a in burst if a.role in ("emergency_heal", "critical_heal", "direct_heal")]
    assert len(heals) == 1
