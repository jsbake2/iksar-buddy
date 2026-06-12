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
                    "group_ward_on_ae": True}
    return c


def test_fail_closed_when_chat_unsafe():
    w = WorldState(members=[Member(0, hp=0.1, ward=False)], chat_safe=False)
    assert decide(w, _cfg(), State.IN_COMBAT) is None


def test_casting_blocks_non_emergency():
    w = WorldState(members=[Member(0, hp=1.0, ward=False)], casting=True)
    assert decide(w, _cfg(), State.IN_COMBAT) is None


def test_emergency_heal_runs_even_while_casting():
    # hp below emergency -> heal fires through a cast (it cancels it). No emergency
    # spell mapped -> falls back to direct_heal.
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], casting=True, group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "direct_heal" and a.target_slot == 0


def test_emergency_prefers_mapped_higher_tier():
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], group_ward_up=True)
    a = decide(w, _cfg(emergency_heal="Alt+0", critical_heal="3"), State.IN_COMBAT)
    assert a.role == "emergency_heal"


def test_cure_is_tank_first():
    w = WorldState(members=[Member(0, cure=True), Member(1, cure=True)])
    a = decide(w, _cfg(tank_slot=0), State.IN_COMBAT)
    assert a.role == "cure" and a.target_slot == 0
    # tank elsewhere -> still cures the tank first
    a2 = decide(w, _cfg(tank_slot=1), State.IN_COMBAT)
    assert a2.role == "cure" and a2.target_slot == 1


def test_rez_sick_member_not_cured():
    w = WorldState(members=[Member(0, cure=True, rez_sick=True)])
    assert decide(w, _cfg(), State.IN_COMBAT) is None


def test_critical_heal_ignores_low_mana():
    # critical hp + LOW mana -> still heals (critical/emergency bypass mana).
    w = WorldState(members=[Member(0, hp=0.4, ward=True)], own_power=0.1, group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "direct_heal" and a.target_slot == 0   # fallback from critical_heal


def test_standard_heal_skipped_on_low_mana():
    # standard hurt + LOW mana -> SKIP (conserve mana). Healthy ward -> idle.
    w = WorldState(members=[Member(0, hp=0.7, ward=True)], own_power=0.1, group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT) is None
    # same but mana ok -> standard heal
    w2 = WorldState(members=[Member(0, hp=0.7, ward=True)], own_power=1.0, group_ward_up=True)
    assert decide(w2, _cfg(), State.IN_COMBAT).role == "direct_heal"


def test_ward_heartbeat():
    w = WorldState(members=[Member(0, hp=1.0, ward=False)], group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "ward" and a.target_slot == 0


def test_group_ward_on_ae():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], ae_incoming=True, group_ward_up=False)
    assert decide(w, _cfg(), State.IN_COMBAT).role == "group_ward"


def test_group_heal_when_enough_hurt():
    # two members hurt (standard) + mana ok -> group heal
    w = WorldState(members=[Member(0, hp=0.7, ward=True), Member(1, hp=0.7, ward=True)],
                   own_power=1.0, group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT).role == "group_heal"


def test_idle_when_healthy():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT) is None
