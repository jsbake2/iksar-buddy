"""Dirge backup single-target heal heartbeat (brain/server.py _hb_dirge_heal).

A Dirge (support) has no healer decide() roles, so its automation runs through the
heartbeat table. This exercises the st_heal top-off spec in isolation.
"""
from __future__ import annotations

from brain.config import Config
from brain.policy import Member, WorldState
from brain.server import Brain
from brain.telemetry import Telemetry


def _brain(st_heal_key="6", **th):
    c = Config()
    c.ability_map = {"tank_slot": 1, "slot_roles": ["support", "tank", "dps", "dps"],
                     "abilities": {"st_heal": {"key": st_heal_key}}}
    c.thresholds = {"dirge_heal_hp": 0.70, "dirge_heal_recast_s": 2.5, **th}
    return Brain(c, Telemetry())


def _world(*members):
    return WorldState(members=list(members))


def test_dirge_heal_tops_off_lowest():
    b = _brain()
    w = _world(Member(1, hp=0.9), Member(2, hp=0.5))   # slot 2 lowest, below 0.70
    cmd = b._hb_dirge_heal(w, 100.0)
    assert cmd is not None
    role, key, slot, reason, stamp = cmd
    assert role == "st_heal" and key == "6" and slot == 2


def test_dirge_heal_tank_first_on_tie():
    b = _brain()
    w = _world(Member(1, hp=0.5), Member(2, hp=0.5))   # tie -> tank (slot 1)
    _, _, slot, _, _ = b._hb_dirge_heal(w, 100.0)
    assert slot == 1


def test_dirge_heal_idle_when_healthy():
    b = _brain()
    assert b._hb_dirge_heal(_world(Member(1, hp=0.95), Member(2, hp=0.8)), 100.0) is None


def test_dirge_heal_skips_dead():
    b = _brain()
    # only member below floor is dead -> no heal (rez handles the dead, not st_heal)
    assert b._hb_dirge_heal(_world(Member(1, hp=1.0), Member(2, hp=0.1, dead=True)), 100.0) is None


def test_dirge_heal_disabled_when_floor_zero():
    b = _brain(dirge_heal_hp=0.0)
    assert b._hb_dirge_heal(_world(Member(1, hp=0.2)), 100.0) is None


def test_dirge_heal_noop_when_unmapped():
    # healer profiles don't map st_heal -> key_for('') -> spec no-ops (their heals run
    # through decide(), not here).
    b = _brain(st_heal_key="")
    assert b._hb_dirge_heal(_world(Member(1, hp=0.2)), 100.0) is None


def test_dirge_heal_per_target_cooldown():
    b = _brain()
    w = _world(Member(1, hp=0.9), Member(2, hp=0.5))
    _, _, slot, _, stamp = b._hb_dirge_heal(w, 100.0)
    stamp()                                            # record the recast time
    assert b._hb_dirge_heal(w, 101.0) is None          # within 2.5s -> blocked
    assert b._hb_dirge_heal(w, 103.0) is not None      # past cooldown -> fires again
