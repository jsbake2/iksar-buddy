#!/usr/bin/env python3
"""EQ2U recon: log in, dump HTML of the recipe-book index + sample pages.

One-shot inspection step so we can see the real DOM before writing the parser.
Creds come from config/secrets.yaml (gitignored). Run from repo root:
    .venv/bin/python tools/recipe_scrape/recon.py
"""
from __future__ import annotations
import pathlib, json
import yaml
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "tools" / "recipe_scrape" / "recon_out"
OUT.mkdir(parents=True, exist_ok=True)

SECRETS = yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())["eq2wire"]

# Pages to capture for structure analysis.
TARGETS = {
    "login":            SECRETS["url_login"],
    "books_by_level":   "https://u.eq2wire.com/soe2/recipebooks/level",
    "book_item_index":  "https://u.eq2wire.com/item/index/99988405",
    "book_item_named":  "https://u.eq2wire.com/i/scholar+essentials+volume+16",
    "recipe_search":    "https://u.eq2wire.com/recipe/recipe_search",
}


def dump(page, name, url):
    print(f"  -> {name}: {url}", flush=True)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)  # let Cloudflare/JS settle
        html = page.content()
        (OUT / f"{name}.html").write_text(html, encoding="utf-8")
        (OUT / f"{name}.url.txt").write_text(page.url, encoding="utf-8")
        print(f"     saved {len(html):,} bytes, final url={page.url}", flush=True)
    except Exception as e:
        print(f"     ERROR: {e}", flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()

        # 1) login page — capture form structure first.
        dump(page, "login", TARGETS["login"])

        # 2) attempt login. Field selectors are guesses; recon prints what's there.
        print("  attempting login...", flush=True)
        try:
            # common patterns for the EQ2wire/CI auth form
            for sel in ['input[name="email"]', 'input[type="email"]', '#email',
                        'input[name="identity"]', 'input[name="username"]']:
                if page.locator(sel).count():
                    page.fill(sel, SECRETS["email"]); print(f"     email -> {sel}", flush=True); break
            for sel in ['input[name="password"]', 'input[type="password"]', '#password']:
                if page.locator(sel).count():
                    page.fill(sel, SECRETS["password"]); print(f"     pw -> {sel}", flush=True); break
            for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Log")']:
                if page.locator(sel).count():
                    page.click(sel); print(f"     submit -> {sel}", flush=True); break
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            print(f"     post-login url={page.url}", flush=True)
            (OUT / "post_login.html").write_text(page.content(), encoding="utf-8")
            cookies = ctx.cookies()
            (OUT / "cookies.json").write_text(json.dumps(
                [{"name": c["name"], "domain": c["domain"]} for c in cookies], indent=2))
            print(f"     cookies: {[c['name'] for c in cookies]}", flush=True)
        except Exception as e:
            print(f"     LOGIN ERROR: {e}", flush=True)

        # 3) capture the data pages while authenticated.
        for name in ("books_by_level", "book_item_index", "book_item_named", "recipe_search"):
            dump(page, name, TARGETS[name])

        browser.close()
    print(f"\nDone. Inspect {OUT}")


if __name__ == "__main__":
    main()
