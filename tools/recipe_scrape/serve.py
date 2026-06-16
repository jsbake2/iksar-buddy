#!/usr/bin/env python3
"""Serve the recipe browser locally.

    python tools/recipe_scrape/serve.py            # serves + opens the browser
    python tools/recipe_scrape/serve.py --port 9000 --no-open

Serves this directory (so the page at /browser/ can fetch /data/*.json over HTTP —
file:// won't work because of fetch/CORS). Read-only static server.
"""
from __future__ import annotations
import argparse, functools, http.server, pathlib, socketserver, webbrowser

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not (ROOT / "data" / "manifest.json").exists():
        print("No data/manifest.json — run:  .venv/bin/python tools/recipe_scrape/scrape.py")
        return

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    url = f"http://{args.host}:{args.port}/browser/index.html"
    with Server((args.host, args.port), handler) as httpd:
        print(f"Recipe browser:  {url}\n(Ctrl-C to stop)")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
