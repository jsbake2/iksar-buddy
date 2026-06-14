"""Tests for Forge's pure logic: recipe parsing (ported from the dino) + the
account interlock. No VMs / no I/O."""
from __future__ import annotations

import tempfile
from pathlib import Path

from forge.recipes import (clean_item_name, parse_crafted_log, parse_ocr_items,
                           parse_recipe_list, parse_scribed_recipes, search_name)
from shared.account_lock import AccountLock


# ---- recipe parsing --------------------------------------------------------
def test_ocr_count_parsing():
    text = "I need to create 2 Pristine Forged Iron Vanguard Greaves (1/3)\n"
    items = parse_ocr_items(text, "armorer")
    assert items == {"Iron Vanguard Greaves": 2}   # total-done = 3-1 = 2


def test_ocr_no_count_defaults_one():
    # no (N/M) count -> defaults to 1; "I need to scribe <name>" prefix stripped.
    items = parse_ocr_items("- I need to scribe Minor Healing", "sage")
    assert items.get("Minor Healing") == 1


def test_scribe_prefix_strip_and_suffix():
    name = clean_item_name("Apprentice IV: Minor Healing", "sage")
    assert name == "Minor Healing"
    assert search_name("Minor Healing", "sage") == "Minor Healing (App"


def test_recipe_list_plain_and_log():
    text = ('Pristine Elm Chair\n'
            '(1)[Mon] Recipe: "Pristine Teak Table" put in recipe book.\n')
    items = parse_recipe_list(text)
    assert "Pristine Elm Chair" in items
    assert "Pristine Teak Table" in items


def test_scribed_recipes_from_log():
    # only the "put in recipe book" lines; deduped + order-preserving; other chat
    # log noise ignored. (Regex still UNVERIFIED vs real EQ2Emu output.)
    text = (
        "(1718000000)[Fri Jun 13] You say to the group, hi\n"
        '(1718000001)[Fri Jun 13] Recipe: "Pristine Teak Table" put in recipe book.\n'
        '(1718000002)[Fri Jun 13] Recipe: "Pristine Teak Chair" put in recipe book.\n'
        '(1718000003)[Fri Jun 13] Recipe: "Pristine Teak Table" put in recipe book.\n'
        "(1718000004)[Fri Jun 13] You gain experience!\n")
    out = parse_scribed_recipes(text)
    assert list(out) == ["Pristine Teak Table", "Pristine Teak Chair"]
    assert all(v == 1 for v in out.values())


def test_scribed_recipes_empty_when_no_book_lines():
    assert parse_scribed_recipes("just chatter\nYou created a thing.\n") == {}


def test_crafted_log_parse():
    out = parse_crafted_log("You created a Pristine Feyiron Kris.\nrandom line\n")
    assert out == ["Pristine Feyiron Kris"]


# ---- account interlock -----------------------------------------------------
def test_account_lock_basic():
    with tempfile.TemporaryDirectory() as d:
        lk = AccountLock(d)
        ok, who = lk.acquire("account2", "healer:Jenskin")
        assert ok and who is None
        # a different holder is blocked while account2 is held
        ok2, who2 = lk.acquire("account2", "forge:Croolst")
        assert not ok2 and who2 == "healer:Jenskin"
        assert lk.holder("account2") == "healer:Jenskin"
        # release frees it
        lk.release("account2", "healer:Jenskin")
        assert lk.holder("account2") is None
        ok3, _ = lk.acquire("account2", "forge:Croolst")
        assert ok3


def test_account_lock_same_holder_idempotent():
    with tempfile.TemporaryDirectory() as d:
        lk = AccountLock(d)
        assert lk.acquire("account1", "forge:Paraphon")[0]
        assert lk.acquire("account1", "forge:Paraphon")[0]   # refresh, still ok


def test_account_lock_unmapped_is_free():
    with tempfile.TemporaryDirectory() as d:
        lk = AccountLock(d)
        # empty account name = no lock needed (always succeeds)
        assert lk.acquire("", "anyone")[0]
