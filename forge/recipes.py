"""Recipe extraction — OCR quest-journal parsing + recipe-list/log parsing.

Ported from the dino (craft.py parse_ocr_items / parse_recipe_list_file). Pure
text logic, no I/O, no deps — unit-testable. The OCR *capture* (screenshot region
-> tesseract text) lives in sensors.py; this turns that text into {recipe: count}.
"""
from __future__ import annotations

import re

# Anchor on a trailing "(done/total)" count; capture everything before it.
_COUNT_RE = re.compile(r"^(.*?)\s*\((\d+)/(\d+)\)\s*$")

# Strip OCR-garbled quest prefixes: "I need to create/scribe/make [N] [a] Pristine
# <material>" — after Pristine there's a material modifier word that's NOT part of
# the recipe search name.
_PREFIX_STRIP_RE = re.compile(
    r"^[-=~*\s]*"
    r"(?:.*?(?:eed|need)\s+to\s+\w+\s+)?"
    r"(?:\d+\s+)?"
    r"(?:a\s+)?"
    r"(?:Pr\w*(?:ine|lne)\s+\w+\s+)?",
    re.IGNORECASE,
)
_SCRIBE_PREFIX_RE = re.compile(r"^(?:Apprentice\s*IV[:\s]*)?", re.IGNORECASE)

# Per-trade tweaks (scribe/sage recipes search by "<name> (App...)").
_TRADE_SETTINGS = {
    "sage":   {"extra_clean": lambda n: _SCRIBE_PREFIX_RE.sub("", n).strip(), "search_suffix": " (App"},
    "scribe": {"extra_clean": lambda n: _SCRIBE_PREFIX_RE.sub("", n).strip(), "search_suffix": " (App"},
}
_DEFAULT_TRADE = {"extra_clean": None, "search_suffix": ""}


def trade_settings(trade_class: str) -> dict:
    return _TRADE_SETTINGS.get((trade_class or "").lower(), _DEFAULT_TRADE)


def search_name(name: str, trade_class: str) -> str:
    """The string to type into the recipe search box (adds trade suffix)."""
    return name + trade_settings(trade_class).get("search_suffix", "")


def clean_item_name(raw: str, trade_class: str) -> str:
    name = _PREFIX_STRIP_RE.sub("", raw).strip()
    extra = trade_settings(trade_class).get("extra_clean")
    if extra:
        name = extra(name)
    return name.strip(". \t")


def parse_ocr_items(text: str, trade_class: str = "") -> dict[str, int]:
    """OCR journal text -> {recipe_name: count_still_needed}. Flexible: anchors on
    the (N/M) count, else falls back to lines that look like quest objectives."""
    items: dict[str, int] = {}
    for line in re.split(r"[\n\r]+", text):
        line = line.strip()
        if not line:
            continue
        m = _COUNT_RE.match(line)
        if m:
            raw, done, total = m.group(1), int(m.group(2)), int(m.group(3))
            count = max(total - done, 1)
            name = clean_item_name(raw, trade_class)
            if name and name not in items:
                items[name] = count
            continue
        if re.search(r"(?:eed|need)", line, re.IGNORECASE):
            name = clean_item_name(line, trade_class)
            if name and name not in items:
                items[name] = 1
    return items


def parse_recipe_list(text: str) -> dict[str, int]:
    """A pasted/loaded recipe list, or EQ2-log 'Recipe: "X" put in recipe book.'
    lines -> {recipe: 1}. One recipe per line otherwise."""
    items: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\(\d+\)\[.*?\] Recipe: ", line):
            mm = re.search(r'Recipe: "(.*?)" put in recipe book\.', line)
            if mm:
                items[mm.group(1)] = 1
        else:
            items[line] = 1
    return items


# Recipes learned when a book is transcribed. The dino assumed the RETAIL format
# `Recipe: "<name>" put in recipe book.`; this may differ on EQ2Emu — VALIDATE
# against a real log line, then adjust this one regex. Unlike parse_recipe_list,
# this ONLY pulls the book lines (the EQ2 chat log is full of other text).
_SCRIBED_RE = re.compile(r'Recipe:\s*"(.*?)"\s+put in recipe book', re.I)


def parse_scribed_recipes(text: str) -> dict[str, int]:
    """From an EQ2 chat log, pull recipes added by transcribing a book -> {name: 1}.
    Order-preserving, deduped."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = _SCRIBED_RE.search(line)
        if m:
            name = m.group(1).strip()
            if name:
                out[name] = 1
    return out


def parse_crafted_log(text: str) -> list[str]:
    """EQ2 log lines confirming a craft completed -> list of created item names.
    Used for authoritative completion + dedup (FORGE.md §4.6). Matches the common
    'You created <item>.' / 'You made <item>.' shapes (refine once we see real lines)."""
    out: list[str] = []
    for line in text.splitlines():
        mm = re.search(r"You (?:created|made|crafted|finished creating)\s+(?:a |an )?(.+?)\.", line, re.I)
        if mm:
            out.append(mm.group(1).strip())
    return out
