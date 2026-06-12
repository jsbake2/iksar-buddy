"""Tests for the host agent's contextual revive-sickness suppression.

Rez sickness can't be matched by icon color (it varies per death), so the agent
suppresses cure-triggering on a member for REZ_WINDOW seconds after it sees that
member go dead->alive. These drive that state machine with a fake clock.
"""
from __future__ import annotations

import agent.host_agent as ha
from agent.host_agent import HostAgent


def _agent():
    return HostAgent("127.0.0.1", 8765)


def test_rez_suppressed_only_after_revive(monkeypatch):
    a = _agent()
    clock = [1000.0]
    monkeypatch.setattr(ha.time, "time", lambda: clock[0])

    # alive the whole time, never died -> never suppressed
    assert a._rez_suppressed([{"slot": 0, "dead": False}]) == set()
    # member dies (no suppression while dead -- it's just dead)
    a._rez_suppressed([{"slot": 0, "dead": True}])
    # revive transition -> suppressed
    assert a._rez_suppressed([{"slot": 0, "dead": False}]) == {0}
    # still within the window
    clock[0] = 1000.0 + ha.REZ_WINDOW - 1
    assert a._rez_suppressed([{"slot": 0, "dead": False}]) == {0}
    # past the window -> resume normal curing
    clock[0] = 1000.0 + ha.REZ_WINDOW + 1
    assert a._rez_suppressed([{"slot": 0, "dead": False}]) == set()


def test_to_event_suppresses_cure_for_rez_sick(monkeypatch):
    a = _agent()
    clock = [500.0]
    monkeypatch.setattr(ha.time, "time", lambda: clock[0])
    monkeypatch.setattr(a, "_poll_gpu", lambda: {}, raising=False)

    raw = {"members": [
        {"slot": 0, "hp": 100, "power": 100, "dead": True, "detriments": [], "cure": False},
        {"slot": 1, "hp": 100, "power": 100, "dead": False,
         "detriments": [{"cell": 0, "rgb": [200, 60, 60], "ignored": None}], "cure": True},
    ], "own": {"hp": 100, "power": 100}, "chat_safety": {"safe": False}}

    # slot 0 dead this frame, then revives next frame
    a._to_event(raw)
    raw["members"][0]["dead"] = False
    # also give the just-revived slot 0 a (rez-sickness) detriment + cure=True
    raw["members"][0].update(cure=True, detriments=[{"cell": 1, "rgb": [141, 40, 91], "ignored": None}])
    ev = a._to_event(raw)

    by = {m["slot"]: m for m in ev["members"]}
    # slot 0 just revived -> cure SUPPRESSED, flagged rez_sick
    assert by[0]["cure"] is False and by[0]["rez_sick"] is True
    # slot 1 never died -> a real curse still triggers a cure
    assert by[1]["cure"] is True and by[1]["rez_sick"] is False
    assert ev["pending_cures"] == ["generic"]   # only the real curse counts
