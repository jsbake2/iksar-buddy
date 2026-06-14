"""Healer login + character switch — now on the SAME direct-login path as forge
(forge.login.LoginDriver). Replaces the old LaunchPad -> char-select -> OCR-pick
flow (changing list order + duplicate names + flaky click offset made it pick the
wrong toon).

  Launch  : boot the healer VM -> LaunchPad -> EverQuest2.exe -> log straight into
            the active profile's character.  -> healer_login()
  Switch  : same-account character change via in-game "/camp <name>".  -> healer_switch()

Credentials live in the gitignored accounts.yaml (owner data dir), keyed by VM dom
(`iksar_buddy`). World + creds come from there; the character comes from the brain's
active profile (brain.cfg.select_character). Only same-account toons switch via /camp;
a cross-account move still needs a full Launch.
"""
from __future__ import annotations

from typing import Callable

from forge.guest import Guest
from forge.login import LoginDriver, load_accounts

HEALER_DOM = "iksar_buddy"
Log = Callable[[str], None]


def _creds() -> tuple[str, str, str]:
    accts, world = load_accounts()
    a = accts.get(HEALER_DOM) or {}
    return (a.get("user") or ""), (a.get("password") or ""), world


def healer_login(target_char: str, log: Log = lambda _m: None) -> bool:
    """Launch Bot: power on the healer VM and log directly into `target_char`
    (the active profile's character). If EQ2 is already in world as another
    same-account toon, /camp-switches instead. Blocking — run in an executor."""
    if not target_char:
        log("launch: no character set on the active profile"); return False
    user, pw, world = _creds()
    if not (user and pw):
        log(f"launch: no credentials for {HEALER_DOM} (set accounts.yaml)"); return False
    drv = LoginDriver(Guest(HEALER_DOM), log)
    return drv.boot_and_login(user, pw, target_char, world)


def healer_switch(target_char: str, log: Log = lambda _m: None) -> bool:
    """Camp + switch to `target_char` via in-game '/camp <name>' (same account, no
    char-select). Blocking — run in an executor. Returns True once in world."""
    if not target_char:
        log("switch: no target character"); return False
    g = Guest(HEALER_DOM)
    if not g.eq2_running():
        log("switch: EQ2 not running (use Launch)"); return False
    return LoginDriver(g, log).camp_to(target_char)
