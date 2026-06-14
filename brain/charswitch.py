"""Camp-and-switch-character for the healer.

The owner's chars now /camp to CHARACTER SELECT (not a full logout), so a healer
profile change can swap the in-game character too: press the camp key -> wait out
the countdown -> OCR-and-click the target profile's character -> Play -> in-world.

This reuses the forge host-side substrate — `forge.guest.Guest` drives the same
`iksar_buddy` VM (it has the same ibkey/ibgclick guest tasks the agent uses) and
`forge.sensors.find_character` does the char-select OCR. Same client + 1920x1080,
so the forge's VALIDATED char_select calibration applies; we read it from the
forge config and fall back to the known-good defaults.

Only same-ACCOUNT characters appear at char-select. Jenskin<->Croolst share an
account, so they swap cleanly; a profile on a different account won't be found
(camp lands on the wrong account's list) -> we report it and leave the profile
unchanged. A cross-account move still needs a full Stop Bot + Launch.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import yaml

from forge import sensors
from forge.guest import Guest

HEALER_DOM = "iksar_buddy"

# Known-good fallback (matches launcher.ahk's hardcoded picks + the forge's live-
# validated char_select). Used if the forge craft.yaml can't be read.
_DEFAULT_CHAR_SELECT = {
    "list_region": {"x": 80, "y": 380, "w": 420, "h": 560},
    "row_click_x": 100,
    "play_click": [1715, 890],
    "server": "Wuoshi",
}


def _char_select_cfg() -> dict:
    """The forge's char_select calibration (single source of truth) or the default."""
    forge_dir = os.environ.get("IB_FORGE_DIR", str(Path.home() / "ib-data" / "forge"))
    try:
        prof = yaml.safe_load((Path(forge_dir) / "craft.yaml").read_text(encoding="utf-8")) or {}
        cs = prof.get("char_select")
        if isinstance(cs, dict) and cs.get("list_region"):
            return cs
    except (OSError, yaml.YAMLError):
        pass
    return _DEFAULT_CHAR_SELECT


def _select_at_charselect(g: Guest, target_char: str,
                          log: Callable[[str], None]) -> bool:
    """At char-select already: validated OCR pick of `target_char` (clicks the row,
    reads the detail-panel name to confirm, then Play). Shared with forge so login
    behaves identically in both tools. Never Plays an unconfirmed selection."""
    return sensors.select_character(g, {"char_select": _char_select_cfg()},
                                    target_char, log=log, play=True)


def select_only(target_char: str,
                log: Callable[[str], None] = lambda _m: None) -> bool:
    """Pick `target_char` when ALREADY at char-select (used by Launch, which now
    stops the in-game launcher at char-select and lets the host pick by profile).
    Blocking — run it in an executor."""
    if not target_char:
        log("char-select: no target character set"); return False
    g = Guest(HEALER_DOM)
    if not g.eq2_running():
        log("char-select: EQ2 not running"); return False
    return _select_at_charselect(g, target_char, log)


def camp_and_select(target_char: str, camp_key: str, camp_wait: float = 20.0,
                    log: Callable[[str], None] = lambda _m: None) -> bool:
    """Camp the current character and select `target_char` at char-select.

    Blocking (sleeps through the camp countdown + char-select render) — run it in
    an executor. Returns True only if the target row was found and clicked.
    """
    if not target_char:
        log("camp+switch: no target character"); return False
    if not camp_key or camp_key.lower() == "none":
        log("camp+switch: no camp key bound"); return False

    g = Guest(HEALER_DOM)
    if not g.eq2_running():
        log("camp+switch: EQ2 not running"); return False

    log(f"camping (key {camp_key}) -> char-select")
    g.press_keys(camp_key)
    time.sleep(camp_wait)                      # camp countdown -> char-select
    return _select_at_charselect(g, target_char, log)
