"""ib brain entrypoint.

Opsec (§7.5): the process title is set to 'ib' — nothing reading eq2/bot.
Runs the transport server (agent link), the FastAPI dashboard, and a config
hot-reload watcher concurrently on one event loop.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from .config import Config
from .server import Brain, serve
from .telemetry import Telemetry
from .web.app import create_app

try:
    import setproctitle
except ImportError:  # pragma: no cover
    setproctitle = None


def _set_title() -> None:
    if setproctitle is not None:
        setproctitle.setproctitle("ib")


async def _config_watcher(cfg: Config, telemetry: Telemetry, interval: float = 2.0) -> None:
    while True:
        await asyncio.sleep(interval)
        if cfg.reload_if_changed():
            telemetry.push_event("config", "reloaded")


async def _run(args: argparse.Namespace) -> None:
    cfg = Config().load()
    telemetry = Telemetry()
    brain = Brain(cfg, telemetry)

    transport = await serve(brain, args.host, args.port)
    app = create_app(brain, telemetry)
    uvi = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.web_port,
                                        log_level="warning", access_log=False))

    async with transport:
        await asyncio.gather(
            transport.serve_forever(),
            uvi.serve(),
            _config_watcher(cfg, telemetry),
        )


def main() -> None:
    p = argparse.ArgumentParser(prog="ib", description="ib brain")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765, help="agent transport port")
    p.add_argument("--web-port", type=int, default=8080, help="dashboard port")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _set_title()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
