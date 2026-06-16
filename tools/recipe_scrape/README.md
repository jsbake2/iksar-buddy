# Recipe scraper + browser

Pulls every tradeskill recipe (by class + level) from EQ2U/EQ2wire into clean local
data, and a themed browser to explore it and build craft lists for the forge crafter.

## Why this exists
DBG Census has no level/tier/class on recipes; EQ2U does, but behind login + Cloudflare.
So we drive a real browser (Playwright) to log in and pull the master book index + each
book's recipes, then aggregate to per-class / per-level lists.

## Use

```bash
# 1) scrape (one-time-ish; resumable — caches each book page under data/raw_books/)
.venv/bin/python tools/recipe_scrape/scrape.py            # full run (~40 min)
.venv/bin/python tools/recipe_scrape/scrape.py --limit 5  # quick test
#   re-running is instant once cached: it re-aggregates from data/raw_books/

# 2) browse
.venv/bin/python tools/recipe_scrape/serve.py             # opens the browser UI
```

Login creds come from `config/secrets.yaml` (gitignored). Run logs carry the session
cookie and are gitignored; the raw page cache (`data/raw_books/`) is gitignored too.

## Data layout (the deliverable, committed)
- `data/by_class/<class>.json` — 9 craft classes + `artisan` (generic L1–9 / expansion
  all-class), keyed by **character level** → list of `{recipe, recipe_id, book, category}`.
- `data/side/{tinkerer,adorner,misc}.json` — skill-based (Tinkerer/Adorner are 0–350 skill,
  not level) + `misc` (mastercrafted one-offs whose class is implied by the item), keyed by
  **book**.
- `data/recipes_all.csv` — flat master (class, level, category, book, recipe, ids).
- `data/manifest.json` — class list + counts (drives the browser rail).

Categories: `TS Essentials` (archetype-shared), `TS Advanced` (class-specific, the
mastercrafted upgrades), `TS Apprentice/Journeyman/Shadow`, `Tinkering`, `Adornments`.

## Browser UI
Class-first **tier tree** (class → tier 1–9 / 10–19 / … → level → Essentials/Advanced
sections → recipes) and a flat **filterable table** — toggle top-right. Click recipes to
add them to the **craft list** tray, then *Export list (YAML)* drops a `lists.yaml`-shaped
block for the crafter. 7 dashboard themes.

## Known gaps / future
- Tinkerer/Adorner precise 0–350 **skill number** isn't on the recipe page text; would need
  a per-recipe detail pass (~20k fetches). Deferred — book/volume tier proxies the range.
- TS Quest one-off recipes are excluded (not leveling books).
- "Send selection straight to a bot's queue" — currently export-to-YAML; live wiring TBD.
