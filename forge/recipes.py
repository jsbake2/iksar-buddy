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


_RECIPE_STATIONS: dict[str, str] | None = None


def recipe_station(name: str) -> str:
    """Crafting station/table for a canonical recipe ('Forge', 'Sewing Table', …), '' if
    unknown. Built from the scraped DB (by_class + side JSON), cached."""
    global _RECIPE_STATIONS
    if _RECIPE_STATIONS is None:
        st: dict[str, str] = {}
        for sub in ("by_class", "side"):
            d = _DATA_DIR / sub
            if d.exists():
                for f in d.glob("*.json"):
                    try:
                        for items in json.loads(f.read_text(encoding="utf-8")).values():
                            for r in items:
                                rn, sta = r.get("recipe"), r.get("station")
                                if rn and sta and sta != "Unknown":
                                    # Key case-insensitively: bag/container recipes are stored
                                    # lowercase ("pristine rawhide leather backpack") while most
                                    # rows are Title-Case — exact-match would miss them.
                                    st.setdefault(rn.lower(), sta)
                    except (OSError, ValueError):
                        pass
        _RECIPE_STATIONS = st
    return _RECIPE_STATIONS.get(name.lower(), "")


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


# Journal lines that are NOT recipes — the timed/fast-writ quest TIMER especially. They
# must be dropped before parsing AND before wrap-merge, or "Time remaining 0:19" becomes a
# bogus objective or gets glued onto the real recipe above it. (Recipes never say "remaining".)
_QUEST_NOISE_RE = re.compile(r"remaining|time\s*left", re.I)


def _is_quest_noise(line: str) -> bool:
    return bool(_QUEST_NOISE_RE.search(line or ""))


def _join_wrapped(text: str) -> str:
    """Merge wrapped continuation lines back onto their objective. A line that doesn't start
    a new objective (no 'need to create' / leading '-') is glued to the previous one — fixes
    a recipe whose '(Journeyman)' tail wrapped to the next line. Quest-timer noise lines are
    dropped (not merged) so they can't corrupt the recipe above them."""
    out: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        ls = raw.strip()
        if not ls or _is_quest_noise(ls):
            continue
        starts = bool(re.search(r"(?:eed|need)\s+to", ls, re.I)) or ls.startswith("-")
        if not starts and out:
            out[-1] += " " + ls
        else:
            out.append(ls)
    return "\n".join(out)


def _fix_brackets(s: str) -> str:
    """OCR misreads parentheses as { } or [ ] — a recipe ONLY ever uses ( ). Normalize."""
    return s.replace("{", "(").replace("}", ")").replace("[", "(").replace("]", ")")


_ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '()")


def odd_chars(s: str) -> str:
    """Special chars a recipe NEVER contains (allowed: letters, digits, space, apostrophe,
    parens). Sorted + deduped, '' if clean. Used to flag OCR noise for the owner to fix."""
    return "".join(sorted({c for c in (s or "") if c not in _ALLOWED_CHARS}))


def _clean_writ_name(raw: str) -> str:
    s = re.sub(r"^\s*an?\s+", "", (raw or "").strip(), flags=re.I)   # drop a/an
    return _fix_roman(_fix_brackets(s)).strip(" .")


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


# Writ FLAVOR-TEXT prefixes: EQ2 writs prepend these (jeweler runes -> "Rune of …",
# alchemy -> "Essence of …") but the recipe NAME has no such prefix. We strip them to find
# the real recipe — UNLESS the full flavored name itself matches the DB (then keep it).
# Extensible: the owner adds more via craft.yaml `writ_flavor_prefixes` as they surface.
_FLAVOR_PREFIXES = ["Rune of", "Essence of"]


def _base_variants(base: str, prefixes: list[str]) -> list[str]:
    """The base name, plus flavor-prefix-stripped variants to try (full name first)."""
    out = [base]
    bl = base.lower()
    for pre in prefixes:
        p = pre.lower() + " "
        if bl.startswith(p):
            out.append(base[len(pre):].strip())
    return out


# Items whose RECIPE (and the row in the craft-window result list) is named "Pristine
# <item>", while the writ objective drops the word ("I need to create a Boiled Leather
# Backpack"). Without the prefix the OCR row-match fails. We prepend "Pristine " to the
# resolved name for any objective containing one of these keywords. NOTE (owner): bags
# carry the Pristine prefix only up to ~level 80; above that the recipe drops it. The
# leveling crafters are nowhere near 80, so default-on for backpacks/sacks/quivers; the
# same Pristine-recipe-vs-bare-objective mismatch hits all carry containers. Extend/adjust
# via craft.yaml `pristine_prefix_items`.
_PRISTINE_ITEMS = ["backpack", "sack", "quiver"]


def _pristine_fix(name: str, keywords: list[str]) -> str:
    nl = name.lower()
    if nl.startswith("pristine"):
        return name
    if any(k in nl for k in keywords):
        return "Pristine " + name
    return name


_ARTICLE_RE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)


def _pristine_variants(name: str) -> list[str]:
    """Names to RETRY when a writ objective isn't in the DB: many house/furniture/container recipes
    are named 'a pristine X' while the writ drops the word — and often the ARTICLE too (live OCR
    reads 'Large Burlap Rug', the recipe is 'a pristine large burlap rug'). So insert 'pristine'
    after whatever article is present, and when none is, try each article 'a/an/the pristine X'
    (plus bare). The DB index lookup is case-insensitive, so case doesn't matter. Empty if already
    pristine. (owner: try this whenever the recipe isn't found.)"""
    if "pristine" in name.lower():
        return []
    m = _ARTICLE_RE.match(name)
    rest = name[m.end():] if m else name
    out = []
    if m:
        out.append(f"{m.group(0)}pristine {rest}")    # keep the OCR's own article first
    for art in ("a", "an", "the", ""):                 # DB recipes are usually 'a pristine X'
        out.append((f"{art} pristine {rest}" if art else f"pristine {rest}"))
    seen, uniq = set(), []
    for v in out:
        k = v.lower()
        if k not in seen:
            seen.add(k); uniq.append(v)
    return uniq


def _match_recipe(b: str, t: str, q: str, idx: dict, base_list: list,
                  prefixes: list, base_cutoff: float):
    """Resolve (base, tier, quality) to a canonical DB recipe name, or None. Tries the base and
    flavor-prefix-stripped variants; exact index hit first, else a close fuzzy base match; then
    requires the same tier (and quality when both known)."""
    for bv in _base_variants(b, prefixes):
        cands = [bv.lower()] if bv.lower() in idx else \
                [m.lower() for m in difflib.get_close_matches(bv, base_list, n=1, cutoff=base_cutoff)]
        for cb in cands:
            entries = idx.get(cb, [])
            pick = next((en for et, eq, en in entries if et == t and (not q or not eq or eq == q)), None) \
                or next((en for et, eq, en in entries if et == t), None)
            if pick:
                return pick
    return None


def resolve_writ(items: dict[str, int], base_cutoff: float = 0.86,
                 flavor_prefixes: list[str] | None = None,
                 pristine_items: list[str] | None = None) -> list[tuple[str, str, bool, int, str]]:
    """[(raw, resolved, verified, count, warn)] for each OCR'd writ objective. `warn` is any
    unexpected special char left in the OCR name (recipes only use ' and ()), '' if clean.

    Decompose into (base, tier, quality) and match the BASE name STRICTLY — then require the
    same tier (and quality when known). resolved = canonical DB recipe on a confident match
    (verified=True), else the cleaned OCR name (verified=False). We never substitute a wrong
    recipe — a different base ('Rune of Puncture' vs 'Lung Puncture') or tier is left
    unverified for the owner to check, not crafted blind."""
    prefixes = _FLAVOR_PREFIXES if flavor_prefixes is None else flavor_prefixes
    prist = _PRISTINE_ITEMS if pristine_items is None else pristine_items
    names = recipe_names()
    out = []
    idx, base_list = _recipe_index() if names else ({}, [])
    for raw, count in items.items():
        # Add the Pristine prefix BEFORE decompose+DB-match so verification/station resolve
        # on the real recipe name ("Pristine Boiled Leather Backpack" IS the DB/book entry).
        # Doing it after the match left it unverified with an unknown station.
        clean = _pristine_fix(_clean_writ_name(raw), prist)
        b, t, q = _decompose(clean)
        canon, verified = clean, False
        if names:
            # Match the base (+ flavor-prefix-stripped variants), same tier+quality.
            pick = _match_recipe(b, t, q, idx, base_list, prefixes, base_cutoff)
            if not pick:
                # NOT FOUND -> retry with 'pristine' inserted: many house/furniture/container
                # recipes are named 'a pristine X' but the writ objective drops it (owner).
                for pv in _pristine_variants(clean):
                    pb, pt, pq = _decompose(pv)
                    pick = _match_recipe(pb, pt, pq, idx, base_list, prefixes, base_cutoff)
                    if pick:
                        break
            if pick:
                canon, verified = pick, True
        # Flag leftover special chars (after {}->() normalization) so the owner can fix
        # OCR noise. A verified DB match is clean by definition; only warn on the OCR name.
        warn = "" if verified else odd_chars(clean)
        out.append((raw, canon, verified, count, warn))
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

# Provisioner food/drink writs name the OBJECTIVE with a serving word ("plate of roasted
# mushrooms", "serving of …") that isn't part of the RECIPE name — strip the leading serving
# word so the craft-list/search lands on the real recipe. Owner-curated list; add more here as
# they turn up.
_PROVISIONER_PREFIX_RE = re.compile(
    # 'bott[il1]e'/'g[li1|]ass' tolerate the OCR l->i/1 misread ("bottle"->"bottie", "glass"->"giass")
    # — clean spellings already matched; this catches the leaked misreads the owner saw.
    r"^\s*(?:plate|serving|cup|shot|stein|flask|g[li1|]ass|bowl|pot|bott[il1]e)\s+of\s+", re.IGNORECASE)
# Some provisioner writs also TAIL the objective with the category word ("Mountain Man drink" ->
# recipe "Mountain Man"). Strip a trailing 'drink'/'food'. Owner-curated; add more if they appear.
_PROVISIONER_SUFFIX_RE = re.compile(r"\s+(?:drink|food)\s*$", re.IGNORECASE)


def _clean_provisioner(n: str) -> str:
    return _PROVISIONER_SUFFIX_RE.sub("", _PROVISIONER_PREFIX_RE.sub("", n)).strip()


# Per-trade tweaks (scribe/sage recipes search by "<name> (App...)").
_TRADE_SETTINGS = {
    # sage/scribe: type the BASE name only and let the recipe picker match the tier (owner). The
    # old " (App" suffix injected a stray '(' that corrupted the search; dropped.
    "sage":   {"extra_clean": lambda n: _SCRIBE_PREFIX_RE.sub("", n).strip(), "search_suffix": "", "search_keep_tier": False},
    "scribe": {"extra_clean": lambda n: _SCRIBE_PREFIX_RE.sub("", n).strip(), "search_suffix": "", "search_keep_tier": False},
    "provisioner": {"extra_clean": _clean_provisioner, "search_suffix": ""},
}
_DEFAULT_TRADE = {"extra_clean": None, "search_suffix": "", "search_keep_tier": False}


def trade_settings(trade_class: str) -> dict:
    return _TRADE_SETTINGS.get((trade_class or "").lower(), _DEFAULT_TRADE)


def search_name(name: str, trade_class: str) -> str:
    """The string to type into the recipe search box (adds trade suffix)."""
    return name + trade_settings(trade_class).get("search_suffix", "")


_PARENS_RE = re.compile(r"\s*\([^)]*\)")


_QUALITY_RE = re.compile(r"\((Apprentice|Journeyman|Adept|Expert|Master|Grandmaster)\)", re.I)


def _scrub_parens(s: str) -> str:
    """HARD GLOBAL RULE (owner): a '(' or ')' must NEVER be typed into EQ2's search box — it breaks
    the per-word match AND wastes the tiny 18-char field. Drop matched parentheticals, then remove
    any stray paren chars (e.g. an unclosed '(App'), and collapse whitespace."""
    s = _PARENS_RE.sub(" ", s)
    s = re.sub(r"[()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def prepare_search(text: str, limit: int = 18, keep_tier: bool = False) -> str:
    """Turn a recipe name (or tuned search) into the string to TYPE into EQ2's search box.

    Two HARD owner rules: (1) NEVER type a '(' or ')'. (2) Don't spend the ~18-char field on the
    quality tier — the OCR recipe-picker disambiguates the tier off the full `name`, so the typed
    search is the BASE name only and gets the whole budget. So by default we strip every
    parenthetical (incl. the tier) and any stray paren char, and type the abbreviated base.

    keep_tier=True is an opt-in escape hatch (none use it now) for a class where the base alone
    can't land the recipe: it appends the tier as a BARE word (still never a paren).
    """
    t = (text or "").strip()
    base = _scrub_parens(t) or t
    if not keep_tier:
        return abbreviate(base, limit)
    m = _QUALITY_RE.search(t)
    qual = m.group(1) if m else ""
    if not qual:
        return abbreviate(base, limit)
    room = limit - len(qual) - 1                      # reserve a space + the (whole) tier word
    if room < 2:                                      # tier alone ~fills the field -> abbreviate all
        return abbreviate(f"{base} {qual}", limit)
    return f"{abbreviate(base, room)} {qual}".strip()


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
        if not line or _is_quest_noise(line):       # skip the timed-writ quest timer
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
