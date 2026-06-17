"""Recipe extraction — OCR quest-journal parsing + recipe-list/log parsing.

Ported from the dino (craft.py parse_ocr_items / parse_recipe_list_file). Pure
text logic, no I/O, no deps — unit-testable. The OCR *capture* (screenshot region
-> tesseract text) lives in sensors.py; this turns that text into {recipe: count}.
"""
from __future__ import annotations

import difflib
import json
import pathlib
import re

# Scraped recipe DB (by_class + side JSON), used to verify OCR'd writ names against real
# recipes. Located relative to the repo/app root; absent until the scrape is deployed.
_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "tools" / "recipe_scrape" / "data"
_RECIPE_NAMES: set[str] | None = None


def recipe_names() -> set[str]:
    """All canonical recipe names from the scraped DB (cached). Empty if not deployed."""
    global _RECIPE_NAMES
    if _RECIPE_NAMES is None:
        names: set[str] = set()
        for sub in ("by_class", "side"):
            d = _DATA_DIR / sub
            if d.exists():
                for f in d.glob("*.json"):
                    try:
                        for items in json.loads(f.read_text(encoding="utf-8")).values():
                            for r in items:
                                if r.get("recipe"):
                                    names.add(r["recipe"])
                    except (OSError, ValueError):
                        pass
        _RECIPE_NAMES = names
    return _RECIPE_NAMES


# Roman-numeral runs OCR badly (the font's capital I reads as l / | / 1): "III" -> "|ll",
# "II" -> "Il". Normalize a standalone run of those glyphs back to the roman of that length.
_ROMAN_FIX = re.compile(r"(?<=\s)([IilL|1]{2,4})(?=\s|\(|\)|\.|$)")
_ROMAN = {2: "II", 3: "III", 4: "IV"}
_TIER_RE = re.compile(r"(?:^|\s)(IX|IV|VI{0,3}|V|I{1,3}|X)(?=\s|\(|$)")


def _fix_roman(s: str) -> str:
    return _ROMAN_FIX.sub(lambda m: _ROMAN.get(len(m.group(1)), m.group(1)), s)


def _tier(name: str) -> str:
    """The roman-numeral tier in a recipe name ('III'), or '' if none."""
    m = _TIER_RE.search(name)
    return m.group(1) if m else ""


def _join_wrapped(text: str) -> str:
    """Merge wrapped continuation lines back onto their objective. A line that doesn't start
    a new objective (no 'need to create' / leading '-') is glued to the previous one — fixes
    a recipe whose '(Journeyman)' tail wrapped to the next line."""
    out: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        ls = raw.strip()
        if not ls:
            continue
        starts = bool(re.search(r"(?:eed|need)\s+to", ls, re.I)) or ls.startswith("-")
        if not starts and out:
            out[-1] += " " + ls
        else:
            out.append(ls)
    return "\n".join(out)


def _clean_writ_name(raw: str) -> str:
    s = re.sub(r"^\s*an?\s+", "", (raw or "").strip(), flags=re.I)   # drop a/an
    return _fix_roman(s).strip(" .")


_QUALITIES = ["Apprentice", "Journeyman", "Adept", "Expert", "Master", "Grandmaster"]
_RECIPE_INDEX: tuple[dict, list] | None = None


def _decompose(name: str) -> tuple[str, str, str]:
    """(base, tier, quality) — base name without the roman tier or the '(Quality)' tag."""
    n = _fix_roman(name)
    quality = ""
    qm = re.search(r"\(([^)]*)\)", n)
    if qm:
        q = difflib.get_close_matches(qm.group(1).strip(), _QUALITIES, n=1, cutoff=0.6)
        quality = q[0] if q else qm.group(1).strip()
    n = _PARENS_RE.sub("", n).strip()
    tier = _tier(n)
    base = re.sub(r"(?:^|\s)" + re.escape(tier) + r"$", "", n).strip() if tier else n
    return base, tier, quality


def _recipe_index() -> tuple[dict, list]:
    """{base_lower: [(tier, quality, canonical)]}, [unique base names] — cached."""
    global _RECIPE_INDEX
    if _RECIPE_INDEX is None:
        idx: dict[str, list] = {}
        bases: dict[str, str] = {}
        for n in recipe_names():
            b, t, q = _decompose(n)
            idx.setdefault(b.lower(), []).append((t, q, n))
            bases.setdefault(b.lower(), b)
        _RECIPE_INDEX = (idx, list(bases.values()))
    return _RECIPE_INDEX


def resolve_writ(items: dict[str, int], base_cutoff: float = 0.86) -> list[tuple[str, str, bool, int]]:
    """[(raw, resolved, verified, count)] for each OCR'd writ objective.

    Decompose into (base, tier, quality) and match the BASE name STRICTLY — then require the
    same tier (and quality when known). resolved = canonical DB recipe on a confident match
    (verified=True), else the cleaned OCR name (verified=False). We never substitute a wrong
    recipe — a different base ('Rune of Puncture' vs 'Lung Puncture') or tier is left
    unverified for the owner to check, not crafted blind."""
    names = recipe_names()
    out = []
    idx, base_list = _recipe_index() if names else ({}, [])
    for raw, count in items.items():
        clean = _clean_writ_name(raw)
        b, t, q = _decompose(clean)
        canon, verified = clean, False
        if names:
            bases = [b.lower()] if b.lower() in idx else \
                    [m.lower() for m in difflib.get_close_matches(b, base_list, n=1, cutoff=base_cutoff)]
            for cb in bases:
                entries = idx.get(cb, [])
                pick = next((en for et, eq, en in entries if et == t and (not q or not eq or eq == q)), None) \
                    or next((en for et, eq, en in entries if et == t), None)
                if pick:
                    canon, verified = pick, True
                    break
        out.append((raw, canon, verified, count))
    return out

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


_PARENS_RE = re.compile(r"\s*\([^)]*\)")


def prepare_search(text: str, limit: int = 18) -> str:
    """Turn a recipe name (or tuned search) into the string to TYPE into EQ2's search box.

    1) Drop parentheticals — the tier tag like "(Journeyman)"/"(Expert)" isn't needed to
       search (owner crafts this way); the OCR row-match still disambiguates tier by full
       name. This alone fits most names.
    2) If still over the field limit, abbreviate each word evenly (see abbreviate()).
    """
    cleaned = _PARENS_RE.sub("", text or "").strip() or (text or "").strip()
    return abbreviate(cleaned, limit)


def abbreviate(text: str, limit: int = 18) -> str:
    """Shrink a search string to fit EQ2's ~18-char search field WITHOUT just chopping
    the tail (which drops whole trailing words and overruns the field, scrambling input).

    Keep every word, abbreviating each to its longest prefix that fits, distributing the
    budget as evenly as possible (a short word like "Fat" stays whole; the freed chars go
    to longer words). Reserves one char per inter-word space.

        "Floppy Fat Unicorn Lover" (24) -> "Flop Fat Unic Love" (18)

    EQ2's recipe search matches per-word prefixes, so the abbreviation still resolves the
    recipe. Returns text unchanged when it already fits.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    words = text.split()
    n = len(words)
    if n <= 1:
        return text[:limit]
    budget = limit - (n - 1)            # chars left for letters after reserving spaces
    if budget < n:                      # not even 1 char/word -> keep as many whole words as fit
        out, used = [], 0
        for w in words:
            if used + len(w) + (1 if out else 0) > limit:
                break
            used += len(w) + (1 if out else 0); out.append(w)
        return " ".join(out) or text[:limit]
    lengths = [len(w) for w in words]
    alloc = [0] * n
    b, progressing = budget, True
    while b > 0 and progressing:        # round-robin: 1 char at a time to any not-yet-full word
        progressing = False
        for i in range(n):
            if b == 0:
                break
            if alloc[i] < lengths[i]:
                alloc[i] += 1; b -= 1; progressing = True
    return " ".join(w[:alloc[i]] for i, w in enumerate(words) if alloc[i] > 0)


def clean_item_name(raw: str, trade_class: str) -> str:
    name = _PREFIX_STRIP_RE.sub("", raw).strip()
    extra = trade_settings(trade_class).get("extra_clean")
    if extra:
        name = extra(name)
    return name.strip(". \t")


def parse_ocr_items(text: str, trade_class: str = "") -> dict[str, int]:
    """OCR journal text -> {recipe_name: count_still_needed}. Flexible: anchors on
    the (N/M) count, else falls back to lines that look like quest objectives. Wrapped
    objective lines are merged first (a long recipe whose tail spilled to the next line)."""
    items: dict[str, int] = {}
    for line in re.split(r"[\n\r]+", _join_wrapped(text)):
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
