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

from forge.login import LoginDriver, load_accounts, WORLD

HEALER_DOM = "iksar_buddy"
Log = Callable[[str], None]


def _config_dir() -> Path:
    # character -> account roster lives in the brain config (read at call time so
    # IB_CONFIG_DIR overrides + hot edits apply).
    return Path(os.environ.get(
        "IB_CONFIG_DIR", str(Path(__file__).resolve().parent.parent / "config")))


def _brain_accounts_path() -> Path:
    # brain-owned account credentials (gitignored, NOT under forge's data dir).
    return Path(os.environ.get(
        "IB_DATA_DIR", str(Path.home() / "ib-data"))) / "accounts.yaml"


def _roster() -> dict:
    """character -> {account, adventure, tradeskill} from config/characters.yaml."""
    try:
        data = yaml.safe_load((_config_dir() / "characters.yaml").read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data.get("characters") or {}


def account_of(character: str) -> str:
    """The logical EQ2 account a character belongs to (''=unknown)."""
    return ((_roster().get(character) or {}).get("account")) or ""


def _brain_accounts() -> tuple[dict, str]:
    """Brain-owned EQ2 creds, keyed by account label. ({}, WORLD) if missing."""
    try:
        data = yaml.safe_load(_brain_accounts_path().read_text("utf-8")) or {}
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


class _LogList(list):
    """A list whose .append forwards to a callback — routes the harvest controller's
    internal log into the healer's telemetry log."""
    def __init__(self, log: Log) -> None:
        super().__init__(); self._log = log

    def append(self, m) -> None:  # type: ignore[override]
        self._log(m)


def _agent_login(log: Log):
    """Build the login stack for the healer VM (iksar_buddy) using the SAME code path
    as harvest: the in-guest agent types the login form via keybd_event, because AHK
    Send does NOT land on this VM's fullscreen client (validated — AHK left the
    password blank; the agent typer logs in first try). Returns (harvest_ctl, driver)
    where the driver's form_typer is harvest's agent typer."""
    from harvest.__main__ import Harvest       # lazy: heavy import, avoids a cycle
    h = Harvest()
    h.log = _LogList(log)                       # harvest's typer/deploy logs -> healer log
    drv = LoginDriver(h.g, log, form_typer=h._agent_type_login)
    return h, drv


def healer_login(target_char: str, log: Log = lambda _m: None) -> bool:
    """Launch Bot: power on the healer VM and log directly into `target_char` (the
    active profile's character). Blocking — run in an executor."""
    if not target_char:
        log("launch: no character set on the active profile"); return False
    user, pw, world = _creds(target_char)
    if not (user and pw):
        log(f"launch: no credentials for {target_char} (set ~/ib-data/accounts.yaml)"); return False
    _h, drv = _agent_login(log)
    return drv.boot_and_login(user, pw, target_char, world)


def healer_switch(target_char: str, log: Log = lambda _m: None) -> bool:
    """Same-account character change via in-game '/camp <name>' (typed by the guest
    agent — AHK doesn't land on this VM). Blocking. True once in world. Use
    healer_change() when you don't know if the account matches."""
    if not target_char:
        log("switch: no target character"); return False
    h, drv = _agent_login(log)
    if not h.g.eq2_running():
        log("switch: EQ2 not running (use Launch)"); return False
    log(f"switch: /camp {target_char}")
    h._fire_agent({"chat": f"/camp {target_char}"})
    return drv._await_world(target_char, 60)


def healer_change(target_char: str, current_char: str = "",
                  log: Log = lambda _m: None) -> bool:
    """Select `target_char` — the account-aware switch the dashboard drives. All
    keyboard interaction with the fullscreen client goes through the guest agent.

    Same account as `current_char` (or nothing running) -> /camp switch or a cold
    launch. DIFFERENT account (e.g. an account3 Dirge while an account2 healer is in
    world) -> log OUT (/camp desktop) then log back IN with the target account's
    credentials. Blocking — run in an executor."""
    if not target_char:
        log("change: no target character"); return False
    user, pw, world = _creds(target_char)
    if not (user and pw):
        log(f"change: no credentials for {target_char} (set ~/ib-data/accounts.yaml)"); return False
    h, drv = _agent_login(log)

    if not h.g.eq2_running():                                 # nothing in world -> cold launch
        return drv.boot_and_login(user, pw, target_char, world)

    same_account = account_of(target_char) == account_of(current_char) and account_of(target_char)
    if same_account:
        log(f"change: same account -> /camp {target_char}")
        h._fire_agent({"chat": f"/camp {target_char}"})
        return drv._await_world(target_char, 60)

    # cross-account: EQ2 can't swap accounts in place — agent types /camp desktop to
    # exit the client, then a full relogin with the target account's creds.
    log(f"change: cross-account ({account_of(current_char) or '?'} -> "
        f"{account_of(target_char)}); logging out to relog as {target_char}")
    h._fire_agent({"chat": "/camp desktop"})                 # agent types it (AHK won't land)
    for _ in range(40):                                       # ~/camp countdown + client close
        if not h.g.eq2_running():
            break
        time.sleep(2)
    else:
        log("change: client didn't exit after /camp desktop — aborting (safe)")
        return False
    time.sleep(3)
    return drv.boot_and_login(user, pw, target_char, world)  # VM up, EQ2 down -> full login
