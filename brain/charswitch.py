"""Healer (brain) login + character switch.

Uses forge.login.LoginDriver as the shared low-level login DRIVER (LaunchPad ->
EQ2 -> game form -> in world), but the account model lives HERE in the brain:

  * roster        : config/characters.yaml (character -> account) -> account_of()
  * credentials   : ~/ib-data/accounts.yaml (brain-owned, gitignored), keyed by the
                    logical account label; falls back to the legacy forge accounts.yaml
                    so the existing account1/account2 healer logins keep working.
  * select a char : brain/web swap ->
      - same account as the one in world  -> /camp <char>            (healer_switch)
      - different account (e.g. account3 Dirge) -> log OUT, log back IN with that
        account's credentials                                         (healer_change)

Only same-account toons switch via /camp; a cross-account move fully relogs.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import yaml

from forge.guest import Guest
from forge.login import LoginDriver, load_accounts, _camp_ahk, WORLD

HEALER_DOM = "iksar_buddy"
Log = Callable[[str], None]

# character -> account roster lives in the brain config (shared source of truth).
_CONFIG_DIR = Path(os.environ.get(
    "IB_CONFIG_DIR", str(Path(__file__).resolve().parent.parent / "config")))
# brain-owned account credentials (gitignored, NOT under forge's data dir).
_BRAIN_ACCOUNTS = Path(os.environ.get(
    "IB_DATA_DIR", str(Path.home() / "ib-data"))) / "accounts.yaml"


def _roster() -> dict:
    """character -> {account, adventure, tradeskill} from config/characters.yaml."""
    try:
        data = yaml.safe_load((_CONFIG_DIR / "characters.yaml").read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data.get("characters") or {}


def account_of(character: str) -> str:
    """The logical EQ2 account a character belongs to (''=unknown)."""
    return ((_roster().get(character) or {}).get("account")) or ""


def _brain_accounts() -> tuple[dict, str]:
    """Brain-owned EQ2 creds, keyed by account label. ({}, WORLD) if missing."""
    try:
        data = yaml.safe_load(_BRAIN_ACCOUNTS.read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}, WORLD
    return (data.get("accounts") or {}), (data.get("world") or WORLD)


def creds_for_character(character: str) -> tuple[str, str, str]:
    """(user, password, world) for `character`: resolve its account, look it up in the
    brain accounts file first, then fall back to the legacy forge accounts.yaml (by
    account label OR the healer VM dom). ('', '', world) if unknown."""
    account = account_of(character)
    baccts, world = _brain_accounts()
    a = baccts.get(account) or {}
    if a.get("user"):
        return a["user"], a.get("password") or "", world
    faccts, fworld = load_accounts()                      # legacy forge creds
    a = faccts.get(account) or faccts.get(HEALER_DOM) or {}
    return (a.get("user") or ""), (a.get("password") or ""), (world or fworld)


def _creds(target_char: str = "") -> tuple[str, str, str]:
    return creds_for_character(target_char)


def healer_login(target_char: str, log: Log = lambda _m: None) -> bool:
    """Launch Bot: power on the healer VM and log directly into `target_char` (the
    active profile's character). If EQ2 is already in world as another SAME-account
    toon, /camp-switches instead. Blocking — run in an executor."""
    if not target_char:
        log("launch: no character set on the active profile"); return False
    user, pw, world = _creds(target_char)
    if not (user and pw):
        log(f"launch: no credentials for {target_char} (set ~/ib-data/accounts.yaml)"); return False
    drv = LoginDriver(Guest(HEALER_DOM), log)
    return drv.boot_and_login(user, pw, target_char, world)


def healer_switch(target_char: str, log: Log = lambda _m: None) -> bool:
    """Same-account character change via in-game '/camp <name>'. Blocking. True once
    in world. Use healer_change() when you don't know if the account matches."""
    if not target_char:
        log("switch: no target character"); return False
    g = Guest(HEALER_DOM)
    if not g.eq2_running():
        log("switch: EQ2 not running (use Launch)"); return False
    return LoginDriver(g, log).camp_to(target_char)


def healer_change(target_char: str, current_char: str = "",
                  log: Log = lambda _m: None) -> bool:
    """Select `target_char` — the account-aware switch the dashboard drives.

    Same account as `current_char` (or nothing running) -> /camp switch or a normal
    launch. DIFFERENT account (e.g. an account3 Dirge while an account2 healer is in
    world) -> log OUT (/camp desktop) then log back IN with the target account's
    credentials. Blocking — run in an executor."""
    if not target_char:
        log("change: no target character"); return False
    user, pw, world = _creds(target_char)
    if not (user and pw):
        log(f"change: no credentials for {target_char} (set ~/ib-data/accounts.yaml)"); return False
    g = Guest(HEALER_DOM)
    drv = LoginDriver(g, log)

    if not g.eq2_running():                                   # nothing in world -> cold launch
        return drv.boot_and_login(user, pw, target_char, world)

    same_account = account_of(target_char) == account_of(current_char) and account_of(target_char)
    if same_account:
        log(f"change: same account -> /camp {target_char}")
        return drv.camp_to(target_char)

    # cross-account: EQ2 can't swap accounts in place — exit to desktop, then relog.
    log(f"change: cross-account ({account_of(current_char) or '?'} -> "
        f"{account_of(target_char)}); logging out to relog as {target_char}")
    g.run_ahk(_camp_ahk("desktop"))                          # /camp desktop (client exits)
    for _ in range(40):                                       # ~/camp countdown + client close
        if not g.eq2_running():
            break
        time.sleep(2)
    else:
        log("change: client didn't exit after /camp desktop — aborting (safe)")
        return False
    time.sleep(3)
    return drv.boot_and_login(user, pw, target_char, world)  # VM up, EQ2 down -> full login
