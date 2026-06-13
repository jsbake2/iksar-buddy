"""ibf — Forge crafting dashboard entrypoint (FORGE.md).

Runs the FastAPI dashboard + websocket telemetry + the mock craft sim on one
asyncio loop. Opsec (§7.5): process title 'ibf', no eq2/bot in any visible name.

    python -m forge --web-port 18081

Dashboard: http://<host>:18081 . Backend is mocked until the real CraftWorkers
land (FORGE.md §11); the web contract stays the same.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn
import yaml

from .sim import ForgeSim
from .telemetry import ForgeTelemetry
from .web.app import create_app

try:
    import setproctitle
except ImportError:  # pragma: no cover
    setproctitle = None

CONFIG = Path(__file__).resolve().parent.parent / "config" / "forge" / "stations.yaml"


def _load_stations() -> dict:
    if CONFIG.exists():
        with CONFIG.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


async def _run(args: argparse.Namespace) -> None:
    stations = _load_stations()
    tele = ForgeTelemetry(trade_classes=stations.get("trade_classes", []))
    for bot in stations.get("bots", []):
        tele.add_bot(bot)
    sim = ForgeSim(tele)
    app = create_app(tele, sim)
    uvi = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.web_port,
                                        log_level="warning", access_log=False))
    await asyncio.gather(uvi.serve(), sim.run())


def main() -> None:
    p = argparse.ArgumentParser(prog="ibf", description="ib forge (crafting) dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--web-port", type=int, default=18081, help="dashboard port")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if setproctitle is not None:
        setproctitle.setproctitle("ibf")
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
