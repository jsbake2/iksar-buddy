#!/usr/bin/env python3
"""Pull the crafting station (bench) for every recipe from DBG Census and cache it.

EQ2U recipe_id == Census recipe id, and Census's open `recipe` collection carries the
`bench` field (which EQ2U's pages don't expose per-recipe). So we pull id->bench once
(~14 light pages, no auth) and write data/census_bench.json {id: station}. scrape.py
joins it onto each recipe. Re-run only when the recipe set changes.

    .venv/bin/python tools/recipe_scrape/fetch_bench.py
"""
from __future__ import annotations
import json, pathlib, time, urllib.request

DATA = pathlib.Path(__file__).resolve().parent / "data"
URL = "https://census.daybreakgames.com/s:example/get/eq2/recipe/?c:show=id,bench&c:limit={lim}&c:start={start}"
PAGE = 5000

# raw bench code -> friendly station. Themed/expansion variants fold into their base
# by keyword (everfrost_workbench -> Work Bench, blood_iron_forge -> Forge, …).
def station(bench: str) -> str:
    b = (bench or "").lower()
    if not b:
        return "Unknown"
    rules = [
        ("forge", "Forge"),
        ("work_bench", "Work Bench"), ("workbench", "Work Bench"),
        ("work_desk", "Work Desk"), ("engrav", "Work Desk"),
        ("chemistry", "Chemistry Table"), ("alch", "Chemistry Table"), ("cauldron", "Chemistry Table"),
        ("woodworking", "Woodworking Table"),
        ("sewing", "Sewing Table"), ("loom", "Sewing Table"), ("mannequin", "Sewing Table"),
        ("stove", "Stove & Keg"), ("keg", "Stove & Keg"), ("brew", "Stove & Keg"), ("pot", "Stove & Keg"),
    ]
    for kw, name in rules:
        if kw in b:
            return name
    return "Other"


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    start = 0
    while True:
        url = URL.format(lim=PAGE, start=start)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=40) as r:
                    data = json.loads(r.read())
                break
            except Exception as e:
                print(f"  page {start} retry {attempt+1}: {e}", flush=True)
                time.sleep(3 * (attempt + 1))
        else:
            print(f"!! gave up on page {start}"); break
        rows = data.get("recipe_list") or []
        for r in rows:
            rid = r.get("id")
            if rid is not None:
                out[str(rid)] = station(r.get("bench"))
        print(f"  start={start} got {len(rows)} (total {len(out)})", flush=True)
        if len(rows) < PAGE:
            break
        start += PAGE
        time.sleep(0.4)
    (DATA / "census_bench.json").write_text(json.dumps(out), encoding="utf-8")
    from collections import Counter
    c = Counter(out.values())
    print(f"\nwrote data/census_bench.json ({len(out)} recipes)")
    for s, n in c.most_common():
        print(f"   {n:6}  {s}")


if __name__ == "__main__":
    main()
