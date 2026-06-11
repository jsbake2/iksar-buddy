from brain.config import Config
from brain.policy import Member, WorldState, decide
from brain.state import State


def _cfg():
    c = Config()
    c.ability_map = {"tank_slot": 0}
    c.thresholds = {
        "tank_emergency_hp": 0.35,
        "cure_priority": ["noxious", "elemental", "trauma", "arcane", "curse"],
        "group_ward_on_ae": True,
    }
    return c


def test_fail_closed_when_chat_unsafe():
    w = WorldState(members=[Member(0, hp=0.1, ward=False)], chat_safe=False)
    assert decide(w, _cfg(), State.IN_COMBAT) is None


def test_no_action_while_casting():
    w = WorldState(members=[Member(0, ward=False)], casting=True)
    assert decide(w, _cfg(), State.IN_COMBAT) is None


def test_cure_takes_priority():
    w = WorldState(members=[Member(0, ward=False)], pending_cures=["arcane", "noxious"])
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "cure_noxious"  # priority order, not list order


def test_ward_is_the_heartbeat():
    w = WorldState(members=[Member(0, hp=1.0, ward=False)])
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "ward" and a.target_slot == 0


def test_group_ward_on_ae():
    w = WorldState(members=[Member(0, ward=True)], ae_incoming=True, group_ward_up=False)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "group_ward"


def test_emergency_direct_heal():
    w = WorldState(members=[Member(0, hp=0.2, ward=True)], group_ward_up=True)
    a = decide(w, _cfg(), State.IN_COMBAT)
    assert a.role == "direct_heal"


def test_idle_when_healthy():
    w = WorldState(members=[Member(0, hp=1.0, ward=True)], group_ward_up=True)
    assert decide(w, _cfg(), State.IN_COMBAT) is None
