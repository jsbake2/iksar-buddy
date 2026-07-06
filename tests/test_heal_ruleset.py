"""Regression tests for guest_agent/heal_ruleset.py — the generated heal.json.

The ruleset is what the in-guest reflex actually executes; a wrong pixel loc or
macro here means the healer presses garbage (that bug shipped once — see the
module docstring). Pin down: geometry -> pixel locs, thresholds -> sample fracs,
ability_map -> action macros, and the not-learned fallback chain.
"""
from guest_agent.heal_ruleset import build_heal_ruleset

CAL = {
    "group_bars": {"x0": 33, "x1": 139, "hp_base_y": 120, "pitch": 75, "slots": 6},
    "power_bar": {"x0": 19, "x1": 128, "y": 46},
}

ABILITIES = {
    "tank_slot": 1,
    "group_target_keys": ["F1", "F2", "F3", "F4", "F5", "F6"],
    "abilities": {
        "direct_heal": {"key": "4"},
        "critical_heal": {"key": "3"},
        "group_heal": {"key": "8"},
        "cure": {"key": "0"},
    },
}

THRESH = {"hp_standard": 0.90, "hp_critical": 0.75, "mana_floor": 0.30}


def test_pixel_locs_from_calibration_and_thresholds():
    rs = build_heal_ruleset(CAL, ABILITIES, THRESH)
    # std sample x = x0 + hp_standard * (x1 - x0) = 33 + 0.90*106 = 128 (rounded)
    assert rs["pixels"]["standard"]["0"]["loc"] == [128, 120]
    # cri x = 33 + 0.75*106 = 112.5 -> 112 (banker's rounding is fine, pin actual)
    assert rs["pixels"]["critical"]["0"]["loc"] == [round(33 + 0.75 * 106), 120]
    # row 3 is pitch*3 further down
    assert rs["pixels"]["standard"]["3"]["loc"][1] == 120 + 75 * 3
    # mana pixel on the power bar at mana_floor
    assert rs["pixels"]["self_mana"]["loc"] == [round(19 + 0.30 * 109), 46]
    # presence check at the bar's left edge, expects black when slot empty
    assert rs["pixels"]["group_check"]["5"] == {"loc": [33, 120 + 75 * 5], "clr": [0, 0, 0]}


def test_actions_target_then_cast():
    rs = build_heal_ruleset(CAL, ABILITIES, THRESH)
    assert rs["actions"]["heal_0_std"]["action"] == "F1,4"
    assert rs["actions"]["heal_3_std"]["action"] == "F4,4"
    assert rs["actions"]["heal_2_cri"]["action"] == "F3,3"
    assert rs["actions"]["heal_group_std"]["action"] == "8"
    # cure emitted per-slot with the one generic cure key
    assert rs["actions"]["cure_nox_1"]["action"] == "F2,0"


def test_unlearned_critical_falls_back_to_standard_heal():
    for crit in ({"key": "3", "learned": False},
                 {"key": "3", "desc": "Not learned yet"},
                 {"key": "none"},
                 {"key": ""},
                 None):
        am = {**ABILITIES, "abilities": {**ABILITIES["abilities"], "critical_heal": crit}}
        rs = build_heal_ruleset(CAL, am, THRESH)
        assert rs["actions"]["heal_0_cri"]["action"] == "F1,4", crit


def test_no_cure_key_emits_no_cure_actions():
    am = {**ABILITIES, "abilities": {k: v for k, v in ABILITIES["abilities"].items()
                                     if k != "cure"}}
    rs = build_heal_ruleset(CAL, am, THRESH)
    assert not any(a.startswith("cure_") for a in rs["actions"])


def test_thresholds_optional_falls_back_to_module_defaults():
    """No thresholds passed -> historical fallbacks (mana 0.24, NOT the brain's
    0.30 — that mismatch is exactly why the sync passes thresholds.yaml)."""
    rs = build_heal_ruleset(CAL, ABILITIES, None)
    assert rs["pixels"]["self_mana"]["loc"][0] == round(19 + 0.24 * 109)


def test_calibration_overrides_colors_and_chat_region():
    cal = {**CAL,
           "bar_colors": {"hp_full_rgb": [1, 2, 3], "power_rgb": [4, 5, 6]},
           "sensor": {"chat_input": [10, 20, 30, 40], "chat_bright_thresh": 99}}
    rs = build_heal_ruleset(cal, ABILITIES, THRESH)
    assert rs["pixels"]["standard"]["0"]["clr"] == [1, 2, 3]
    assert rs["pixels"]["self_mana"]["clr"] == [4, 5, 6]
    assert rs["chat_input"] == {"region": {"x": 10, "y": 20, "w": 30, "h": 40},
                                "bright_threshold": 99}


def test_empty_configs_still_produce_a_complete_ruleset():
    """Defaults everywhere: must not KeyError; reflex can at least run."""
    rs = build_heal_ruleset({}, {}, {})
    assert set(rs["pixels"]) == {"self_mana", "group_check", "standard", "critical"}
    assert rs["actions"]["heal_0_std"]["action"] == "F1,4"
    assert rs["chat_input"]["region"]["w"] > 0
