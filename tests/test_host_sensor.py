"""Unit tests for the host-side sensor detection math (agent/host_sensor.py).

All of it was validated LIVE against the game, but had no regression guard. These
build synthetic pixel dicts ({(x,y): (r,g,b)}) and exercise the pure detection
logic — no screenshots, no magick — so a refactor that breaks the blue-anchor /
fill / detriment / chat-safety rules fails here instead of in-game.
"""
from __future__ import annotations

import agent.host_sensor as hs
from agent.host_sensor import HostSensor


# ---- predicates -----------------------------------------------------------
def test_color_predicates():
    assert hs.is_blue((20, 30, 200)) and not hs.is_blue((200, 30, 20))
    assert hs.is_bright((40, 40, 40)) and not hs.is_bright((10, 10, 10))   # >90 sum
    assert hs.is_icon((50, 50, 50)) and not hs.is_icon((30, 30, 30))       # >120 sum


def test_ignore_signature_mechanism(monkeypatch):
    # Rez sickness is NOT color-matched (its icon color varies per death -> handled
    # contextually in host_agent). IGNORE_SIGNATURES is empty by default, but the
    # mechanism still works for any genuinely stable-color uncurable.
    assert hs.is_ignored((103, 26, 61)) is None            # nothing matched now
    monkeypatch.setitem(hs.IGNORE_SIGNATURES, "demo", (100, 100, 100))
    assert hs.is_ignored((100, 100, 100)) == "demo"
    assert hs.is_ignored((130, 130, 130)) is None          # outside tol


# ---- helpers to synthesize a group frame ----------------------------------
def _bar_row(pix, y, x0, x1, frac, color):
    """Fill `frac` of the track [x0,x1) at row y with `color`; rest stays dark."""
    span = int((x1 - x0) * frac)
    for i, x in enumerate(range(x0, x1)):
        pix[(x, y)] = color if i < span else (5, 5, 5)


def _member(pix, slot, hp_frac, pw_frac):
    tx0, tx1 = hs.GRP_TRACK
    pwr_y = hs.PWR_BASE + hs.PITCH * slot
    _bar_row(pix, pwr_y, tx0, tx1, pw_frac, (30, 40, 220))            # blue power
    _bar_row(pix, pwr_y - hs.HP_PWR_GAP, tx0, tx1, hp_frac, (40, 200, 60))  # green HP


# ---- bar reading ----------------------------------------------------------
def test_fill_pct_half_bar():
    s = HostSensor()
    pix = {}
    y = 200
    _bar_row(pix, y, *hs.GRP_TRACK, 0.5, (40, 200, 60))
    assert 45 <= s._fill(pix, hs.GRP_TRACK, y) <= 55


def test_power_row_anchors_on_blue():
    s = HostSensor()
    pix = {}
    _member(pix, 0, 1.0, 1.0)
    # power row found near its hint, regardless of the green HP row above
    assert s._power_row(pix, hs.GRP_TRACK, hs.PWR_BASE) == hs.PWR_BASE


def test_read_members_two_present():
    s = HostSensor()
    pix = {}
    _member(pix, 0, 1.0, 1.0)      # full
    _member(pix, 1, 0.30, 0.80)    # hurt
    out = s.read_members(pix)
    by_slot = {m["slot"]: m for m in out}
    assert set(by_slot) == {0, 1}                       # only present slots
    assert by_slot[0]["hp"] == 100 and by_slot[0]["power"] == 100
    assert 25 <= by_slot[1]["hp"] <= 35
    assert 75 <= by_slot[1]["power"] <= 85
    assert not by_slot[0]["dead"] and not by_slot[1]["dead"]


def test_low_red_hp_bar_still_reads_via_blue_anchor():
    # EQ2 recolors HP green->red as it drops; the bar must still read by anchoring
    # on the (hue-stable) blue power row and measuring brightness, not green.
    s = HostSensor()
    pix = {}
    tx0, tx1 = hs.GRP_TRACK
    pwr_y = hs.PWR_BASE
    _bar_row(pix, pwr_y, tx0, tx1, 1.0, (30, 40, 220))          # full blue power
    _bar_row(pix, pwr_y - hs.HP_PWR_GAP, tx0, tx1, 0.4, (200, 40, 40))  # RED hp 40%
    m = s.read_members(pix)[0]
    assert 35 <= m["hp"] <= 45                                 # not 0, not missed


def test_dead_member_hp_zero():
    s = HostSensor()
    pix = {}
    _member(pix, 0, 0.0, 0.5)
    assert s.read_members(pix)[0]["dead"] is True


# ---- detriments -----------------------------------------------------------
def _fill_cell(pix, row_y, cell_idx, color):
    xc = hs.CELL_XC[cell_idx]
    for x in range(xc - hs.INSET, xc + hs.INSET + 1):
        for y in range(row_y - hs.INSET, row_y + hs.INSET + 1):
            pix[(x, y)] = color


def test_detriment_curable_at_sensor_level():
    # The sensor reports ANY lit cell as curable (ignored=None). Rez sickness is
    # NOT excluded here by color -- it's suppressed contextually in the agent.
    s = HostSensor()
    row_y = hs.PWR_BASE + hs.ROW_DY
    pix = {}
    _fill_cell(pix, row_y, 0, (200, 60, 60))         # a real bright curse
    cells, cure = s._detriments(pix, row_y)
    assert cure is True and cells[0]["ignored"] is None

    pix2 = {}
    _fill_cell(pix2, row_y, 1, (141, 40, 91))        # rez-sickness-colored cell
    cells2, cure2 = s._detriments(pix2, row_y)
    assert cure2 is True and cells2[0]["ignored"] is None  # agent suppresses, not sensor


def test_empty_detriment_row_no_cure():
    s = HostSensor()
    cells, cure = s._detriments({}, hs.PWR_BASE + hs.ROW_DY)
    assert cells == [] and cure is False


# ---- chat-safety guard ----------------------------------------------------
def test_chat_safety_fails_closed_until_calibrated(monkeypatch):
    s = HostSensor()
    # even with the game present and chat reading inactive, the guard must stay
    # unsafe while the detector is uncalibrated (the inviolable fail-closed rule).
    monkeypatch.setattr(s, "_chat_active", lambda *a, **k: False)
    assert hs.CHAT_GUARD_CALIBRATED is False
    safety = s.chat_safety(pix={}, power=100)
    assert safety["game_present"] is True
    assert safety["safe"] is False


def test_chat_safety_unsafe_when_no_game(monkeypatch):
    s = HostSensor()
    monkeypatch.setattr(s, "_chat_active", lambda *a, **k: None)
    safety = s.chat_safety(pix={}, power=None)    # power None => not in-world
    assert safety["game_present"] is False and safety["safe"] is False
