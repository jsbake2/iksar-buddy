#!/usr/bin/env python3
"""EQ2U recipe scraper.

Login once via Playwright (passes Cloudflare), then use the authenticated
request context to pull the master book index and each book's recipe list.
Aggregates to per-class / per-level JSON + a flat CSV the crafter can consume.

Run from repo root:
    .venv/bin/python tools/recipe_scrape/scrape.py            # full run
    .venv/bin/python tools/recipe_scrape/scrape.py --limit 5  # quick test
    .venv/bin/python tools/recipe_scrape/scrape.py --max-level 90

Creds: config/secrets.yaml (gitignored). Output: tools/recipe_scrape/data/.
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys, time, collections
import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "tools" / "recipe_scrape" / "data"
RAW = DATA / "raw_books"          # cache of each book page (resume-friendly)
SECRETS = yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())["eq2wire"]

BASE = "https://u.eq2wire.com"
INDEX_URL = f"{BASE}/soe2/recipebooks/level"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# Categories we keep (owner: all leveling books + tinkering + adornments).
WANT_CATEGORIES = {
    "TS Essentials", "TS Advanced", "TS Apprentice", "TS Journeyman",
    "TS Shadow", "TS Shadowed",
    "Tinkering",
    "Adornments", "Adornments (Materials)", "Adornments (Spirit Stone)",
    "Adornment (Primordial)",
}

# Archetype -> member tradeskill classes (for resolving "essentials" books).
ARCHETYPES = {
    "craftsman": ["Carpenter", "Provisioner", "Woodworker"],
    "outfitter": ["Armorer", "Tailor", "Weaponsmith"],
    "scholar":   ["Alchemist", "Jeweler", "Sage"],
}
CLASSES = ["Alchemist", "Armorer", "Carpenter", "Jeweler", "Provisioner",
           "Sage", "Tailor", "Weaponsmith", "Woodworker"]


def classify(book_name: str, category: str) -> list[str]:
    """Best-effort class tags from a book name. Returns list of class labels
    (archetype books expand to members; tinkering/adornment get their own tag).
    Single mastercrafted recipe books (Ancient Knowledge / Draconic Knowledge /
    Ancient Teachings) have no class in the name -> "Misc"."""
    n = book_name.lower()
    if category.startswith("Tinkering") or "tinker" in n:
        return ["Tinkerer"]
    if category.startswith("Adornment") or "adorner" in n:
        return ["Adorner"]
    for cls in CLASSES:                      # "Advanced Jeweler Volume 16"
        if cls.lower() in n:
            return [cls]
    for arch, members in ARCHETYPES.items(): # "scholar essentials volume 16"
        if arch in n:
            return members
    if "artisan" in n or "crafter" in n:     # generic all-class (L1-9 + expansion)
        return ["Artisan"]
    return ["Misc"]                          # mastercrafted one-offs, class implied by item


class Session:
    """Authenticated EQ2U session with re-login on throttle.

    The server starts returning empty 200s under sustained request rate; the
    fix is to detect short/empty bodies and refresh the login (new ci_session
    + __cf_bm) before continuing.
    """
    def __init__(self, p):
        self.p = p
        self.browser = None
        self.ctx = None
        self.page = None
        self.login()

    def login(self):
        if self.browser:
            try: self.browser.close()
            except Exception: pass
        self.browser = self.p.chromium.launch(headless=True)
        self.ctx = self.browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        self.page = self.ctx.new_page()
        self.page.goto(SECRETS["url_login"], wait_until="domcontentloaded", timeout=45000)
        self.page.fill('input[name="email"]', SECRETS["email"])
        self.page.fill('input[name="password"]', SECRETS["password"])
        self.page.click('input[type="submit"]')
        self.page.wait_for_load_state("networkidle", timeout=30000)
        if "ci_session" not in [c["name"] for c in self.ctx.cookies()]:
            print("!! login failed — no ci_session cookie", file=sys.stderr); sys.exit(1)
        print("logged in (ci_session present, Cloudflare passed)", flush=True)

    def get(self, url, retries=4):
        """Return validated HTML, or None. Empty/short bodies trigger re-login."""
        for i in range(retries):
            try:
                r = self.ctx.request.get(url, timeout=30000)
                html = r.text() if r.ok else ""
                if r.ok and len(html) > 800 and "</html>" in html.lower():
                    return html
                print(f"   bad body ({len(html)}B, status {r.status}) {url} "
                      f"-> re-login (try {i+1}/{retries})", flush=True)
            except Exception as e:
                print(f"   fetch err ({i+1}/{retries}) {url}: {e}", flush=True)
            self.login()
            time.sleep(1.5)
        return None


def parse_index(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for b in soup.select("div.box"):
        a = b.select_one(".name a"); typ = b.select_one(".type"); lvl = b.select_one(".level")
        if not (a and typ):
            continue
        m = re.search(r"/item/index/(\d+)", a.get("href", ""))
        if not m:
            continue
        out.append({
            "id": m.group(1),
            "name": a.get_text(strip=True),
            "category": typ.get_text(strip=True),
            "level": int(lvl.get_text(strip=True)) if lvl and lvl.get_text(strip=True).isdigit() else None,
        })
    return out


def parse_book(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    seen, recipes = set(), []
    for a in soup.select('a[href*="/recipe/detail/"]'):
        m = re.search(r"/recipe/detail/(\d+)", a["href"])
        name = a.get_text(strip=True)
        if not m or not name or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        recipes.append({"recipe_id": m.group(1), "name": name})
    return recipes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap book pages (test)")
    ap.add_argument("--max-level", type=int, default=0, help="skip books above this level")
    ap.add_argument("--delay", type=float, default=0.4, help="politeness delay between books")
    ap.add_argument("--refresh-every", type=int, default=400, help="proactive re-login interval")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True); RAW.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        sess = Session(p)

        print("fetching master index...", flush=True)
        sess.page.goto(INDEX_URL, wait_until="domcontentloaded", timeout=60000)
        sess.page.wait_for_timeout(2500)
        books = parse_index(sess.page.content())
        print(f"  {len(books)} books in index", flush=True)

        sel = [b for b in books if b["category"] in WANT_CATEGORIES
               and "test only" not in b["name"].lower()]   # drop dev/test books
        if args.max_level:
            sel = [b for b in sel if b["level"] is None or b["level"] <= args.max_level]
        if args.limit:
            sel = sel[:args.limit]
        print(f"  {len(sel)} books selected for fetch", flush=True)

        fetched = 0
        all_recipes = []   # flat rows: class, level, category, book, recipe, recipe_id
        for i, bk in enumerate(sel, 1):
            cache = RAW / f"{bk['id']}.html"
            if cache.exists() and cache.stat().st_size > 800:
                html = cache.read_text(encoding="utf-8")
            else:
                if fetched and fetched % args.refresh_every == 0:
                    print(f"  proactive re-login after {fetched} fetches", flush=True)
                    sess.login()
                html = sess.get(f"{BASE}/item/index/{bk['id']}")
                if html is None:
                    print(f"  [{i}/{len(sel)}] SKIP {bk['name']} (fetch failed)", flush=True)
                    continue
                cache.write_text(html, encoding="utf-8")
                fetched += 1
                time.sleep(args.delay)
            recs = parse_book(html)
            classes = classify(bk["name"], bk["category"])
            for cls in classes:
                for r in recs:
                    all_recipes.append({
                        "class": cls, "level": bk["level"], "category": bk["category"],
                        "book": bk["name"], "book_id": bk["id"],
                        "recipe": r["name"], "recipe_id": r["recipe_id"],
                    })
            if i % 25 == 0 or i == len(sel):
                print(f"  [{i}/{len(sel)}] {bk['name']} L{bk['level']} -> {len(recs)} recipes "
                      f"(total rows {len(all_recipes)})", flush=True)

        sess.browser.close()

    write_outputs(all_recipes)


def write_outputs(rows: list[dict]):
    # 1) flat CSV
    import csv
    csv_path = DATA / "recipes_all.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["class", "level", "category", "book",
                                          "recipe", "recipe_id", "book_id"])
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["class"], x["level"] or 0, x["recipe"])):
            w.writerow(r)
    # 2) per-class JSON grouped by level. Skill-based classes (Tinkerer/Adorner)
    #    go to a side dir keyed by book/volume, not character level.
    SIDE = {"Tinkerer", "Adorner", "Misc"}  # skill-based + mastercrafted one-offs
    by_class = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        key = r["level"] if r["class"] not in SIDE else r["book"]
        by_class[r["class"]][key].append(
            {"recipe": r["recipe"], "recipe_id": r["recipe_id"],
             "book": r["book"], "category": r["category"]})
    cls_dir = DATA / "by_class"; cls_dir.mkdir(exist_ok=True)
    side_dir = DATA / "side"; side_dir.mkdir(exist_ok=True)
    for d in (cls_dir, side_dir):                 # clear stale class files from prior runs
        for f in d.glob("*.json"):
            f.unlink()
    for cls, groups in by_class.items():
        out = {str(k): sorted({x["recipe"]: x for x in items}.values(),
                              key=lambda x: x["recipe"])
               for k, items in sorted(groups.items(),
                                      key=lambda kv: (kv[0] is None, str(kv[0])))}
        target = side_dir if cls in SIDE else cls_dir
        (target / f"{cls.lower()}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    # 3) manifest so the browser UI is data-driven (class list + counts).
    manifest = {"main": [], "side": [], "total_rows": len(rows)}
    for cls in sorted(by_class):
        groups = by_class[cls]
        n = sum(len(v) for v in groups.values())
        entry = {"class": cls, "file": f"{cls.lower()}.json",
                 "recipes": n, "groups": len(groups)}
        if cls in SIDE:
            manifest["side"].append(entry)
        else:
            lv = [int(k) for k in groups if isinstance(k, int) or (isinstance(k, str) and str(k).isdigit())]
            entry["min_level"] = min(lv) if lv else None
            entry["max_level"] = max(lv) if lv else None
            manifest["main"].append(entry)
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nwrote {csv_path} ({len(rows)} rows)")
    for cls in sorted(by_class):
        n = sum(len(v) for v in by_class[cls].values())
        loc = "side" if cls in SIDE else "by_class"
        print(f"   {cls:12} {n} recipe-rows / {len(by_class[cls])} groups  -> {loc}/")


if __name__ == "__main__":
    main()
