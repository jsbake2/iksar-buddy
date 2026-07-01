"""Brain account model: roster lookup, credential resolution, and that a non-healer
(Dirge) profile queues no auto actions. No VMs / no I/O beyond temp files."""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from brain import charswitch as cs
from brain.config import Config
from brain.policy import Member, WorldState, decide
from brain.state import State


def _write(base: Path, roster: str, accounts: str) -> None:
    cfg = base / "config"; cfg.mkdir()
    (cfg / "characters.yaml").write_text("characters:\n" + textwrap.indent(roster, "  "))
    data = base / "ib-data"; data.mkdir()
    (data / "accounts.yaml").write_text(accounts)


def test_account_of_and_creds_resolution(tmp_path, monkeypatch):
    _write(
        tmp_path,
        roster="Joar: { account: account3, adventure: dirge }\n"
               "Jenskin: { account: account2, adventure: defiler }\n",
        accounts="world: Wuoshi\naccounts:\n  account3: { user: matte123, password: \"pw!\" }\n",
    )
    monkeypatch.setenv("IB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("IB_DATA_DIR", str(tmp_path / "ib-data"))
    monkeypatch.setenv("IB_FORGE_DIR", str(tmp_path / "nope"))   # no legacy fallback creds

    assert cs.account_of("Joar") == "account3"
    assert cs.account_of("Unknown") == ""
    user, pw, world = cs.creds_for_character("Joar")
    assert (user, pw, world) == ("matte123", "pw!", "Wuoshi")
    # a char whose account has no creds anywhere resolves to empty (caller logs 'no creds')
    assert cs.creds_for_character("Jenskin")[0] == ""


def test_dirge_profile_queues_nothing_on_a_dying_tank():
    """maintenance_role: none + blank heal roles -> the loop never tries to heal."""
    c = Config().load()                         # loads thresholds + the active profile
    assert "joar" in c.list_profiles()
    # swap in Joar's abilities WITHOUT persisting active_profile to disk (set_profile writes)
    c.ability_map = yaml.safe_load((c.config_dir / "profiles" / "joar.yaml").read_text())
    assert c.maint_role == "none"
    w = WorldState(members=[Member(0, hp=1.0), Member(1, hp=0.10, ward=False)],
                   ae_incoming=True, group_ward_up=False, chat_safe=True)
    assert decide(w, c, State.IN_COMBAT) == []
