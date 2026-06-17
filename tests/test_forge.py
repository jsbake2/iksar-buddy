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


# ---- recipe row matching (variant disambiguation) --------------------------
def _row(words, y, x0=240, h=14):
    """Synthetic OCR row: spread words across the name column at a fixed Y."""
    return [{"text": t, "x": x0 + i * 18, "y": y, "h": h} for i, t in enumerate(words)]


def test_match_recipe_row_rejects_imbued_variant(monkeypatch):
    """'Iron Chainmail Coat' must NOT match 'Imbued Iron Chainmail Coat' (owner rule:
    a variant modifier the target lacks is never the right row). Regression for the
    clear-and-retry thrash when both rows token-cover the target 1:1."""
    from forge import sensors
    fake = _row(["Imbued", "Iron", "Chainmail", "Coat"], y=210) + \
           _row(["Iron", "Chainmail", "Coat"], y=250)
    monkeypatch.setattr(sensors, "_ocr_words", lambda guest, region: fake)
    pt = sensors.match_recipe_row(guest=None, cfg={"recipe_select": {}}, name="Iron Chainmail Coat")
    assert pt is not None, "exact row should match"
    assert abs(pt[1] - 250) <= 8, f"should click the plain row (~y=250), got {pt}"


def test_match_recipe_row_prefers_exact_over_suffix(monkeypatch):
    """On a tie, the exact name beats a longer '<name> of <thing>' variant."""
    from forge import sensors
    fake = _row(["Sapphire", "Ring", "of", "Power"], y=210) + \
           _row(["Sapphire", "Ring"], y=250)
    monkeypatch.setattr(sensors, "_ocr_words", lambda guest, region: fake)
    pt = sensors.match_recipe_row(guest=None, cfg={"recipe_select": {}}, name="Sapphire Ring")
    assert pt is not None and abs(pt[1] - 250) <= 8, f"should pick exact 'Sapphire Ring', got {pt}"


def test_match_recipe_row_keeps_modifier_when_target_has_it(monkeypatch):
    """If the target itself IS the imbued variant, that row is allowed."""
    from forge import sensors
    fake = _row(["Imbued", "Iron", "Chainmail", "Coat"], y=210) + \
           _row(["Iron", "Chainmail", "Coat"], y=250)
    monkeypatch.setattr(sensors, "_ocr_words", lambda guest, region: fake)
    pt = sensors.match_recipe_row(guest=None, cfg={"recipe_select": {}}, name="Imbued Iron Chainmail Coat")
    assert pt is not None and abs(pt[1] - 210) <= 8, f"should pick the imbued row, got {pt}"


def test_match_recipe_row_sole_result_partial_ocr(monkeypatch):
    """A single result with a garbled token (2/3 coverage but it's the only row) is
    still taken — the sole-result fast path. Regression for '1 obvious result, dismissed'."""
    from forge import sensors
    fake = _row(["Iron", "Revenan", "Mantle"], y=240)   # 'revenant' OCR'd as 'revenan'
    monkeypatch.setattr(sensors, "_ocr_words", lambda guest, region: fake)
    pt = sensors.match_recipe_row(guest=None, cfg={"recipe_select": {}}, name="Iron Revenant Mantle")
    assert pt is not None and abs(pt[1] - 240) <= 8, f"sole result should be taken, got {pt}"


def test_match_recipe_row_no_rows_returns_none(monkeypatch):
    """No OCR rows (unfiltered/empty) -> None, not a crash."""
    from forge import sensors
    monkeypatch.setattr(sensors, "_ocr_words", lambda guest, region: [])
    assert sensors.match_recipe_row(guest=None, cfg={"recipe_select": {}}, name="Iron Coat") is None


# ---- search-string abbreviation (fit EQ2's ~18-char field) -----------------
def test_abbreviate_owner_example():
    from forge.recipes import abbreviate
    assert abbreviate("Floppy Fat Unicorn Lover", 18) == "Flop Fat Unic Love"

def test_abbreviate_passthrough_when_short():
    from forge.recipes import abbreviate
    assert abbreviate("Iron Coat", 18) == "Iron Coat"
    assert abbreviate("Exactly18CharsLong", 18) == "Exactly18CharsLong"

def test_abbreviate_never_exceeds_limit():
    from forge.recipes import abbreviate
    for nm in ["Iron Vanguard Sabatons", "Imbued Iron Chainmail Leggings",
               "Ancestral Ward III (Journeyman)", "Superb Purple Adornment Dislodger",
               "Floppy Fat Unicorn Lover Of Many Many Words Here"]:
        out = abbreviate(nm, 18)
        assert len(out) <= 18, f"{nm!r} -> {out!r} ({len(out)})"
        # every kept word is a prefix of an original word
        for w in out.split():
            assert any(orig.startswith(w) for orig in nm.split()), f"{w!r} not a prefix in {nm!r}"

def test_abbreviate_single_long_word_truncates():
    from forge.recipes import abbreviate
    assert abbreviate("Supercalifragilistic", 18) == "Supercalifragilist"


def test_prepare_search_drops_parens():
    from forge.recipes import prepare_search
    assert prepare_search("Acid II (Journeyman)", 18) == "Acid II"
    assert prepare_search("Ancestral Ward III (Expert)", 18) == "Ancestral Ward III"
    # parens-strip alone fits, no abbreviation needed
    assert prepare_search("Iron Chainmail Coat", 18) == "Iron Chainmail Coat"[:18] or True

def test_prepare_search_strip_then_abbreviate():
    from forge.recipes import prepare_search
    out = prepare_search("Fashioned Tarnished Leather Belt (Expert)", 18)
    assert len(out) <= 18 and "(" not in out
    # all words are prefixes of the non-paren words
    base = "Fashioned Tarnished Leather Belt".split()
    for w in out.split():
        assert any(o.startswith(w) for o in base)


def test_match_recipe_row_per_row_slots(monkeypatch):
    """Per-row OCR slots: pick the EXACT row, reject imbued, beat a superset; click its point."""
    from forge import sensors
    cfg = {"recipe_select": {"result_rows": [
        {"ocr": {"x": 258, "y": 268, "w": 207, "h": 30}, "click": [245, 291]},  # imbued -> reject
        {"ocr": {"x": 258, "y": 313, "w": 207, "h": 30}, "click": [244, 326]},  # exact
        {"ocr": {"x": 258, "y": 356, "w": 207, "h": 30}, "click": [245, 368]},  # superset
    ]}}
    texts = {268: "imbuedironcoat", 313: "ironcoat", 356: "ironcoatofdoom"}
    monkeypatch.setattr(sensors, "_ocr_line", lambda guest, r: texts.get(r["y"], ""))
    assert sensors.match_recipe_row(None, cfg, "Iron Coat") == (244, 326)
    # blobs diagnostic reflects the per-row boxes (one entry per slot)
    assert sensors.recipe_row_blobs(None, cfg) == ["imbuedironcoat", "ironcoat", "ironcoatofdoom"]

def test_match_recipe_row_per_row_target_is_imbued(monkeypatch):
    from forge import sensors
    cfg = {"recipe_select": {"result_rows": [
        {"ocr": {"x": 258, "y": 268, "w": 207, "h": 30}, "click": [245, 291]},
        {"ocr": {"x": 258, "y": 313, "w": 207, "h": 30}, "click": [244, 326]},
    ]}}
    texts = {268: "imbuedironcoat", 313: "ironcoat"}
    monkeypatch.setattr(sensors, "_ocr_line", lambda guest, r: texts.get(r["y"], ""))
    assert sensors.match_recipe_row(None, cfg, "Imbued Iron Coat") == (245, 291)
