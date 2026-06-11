"""ib agent entrypoint (runs on the guest).

Opsec (§7.5): process title 'ib'. NOTE — on Windows setproctitle does NOT change
what Task Manager shows for python.exe; package as ib.exe (PyInstaller) or run a
renamed interpreter for real concealment. Window titles are set explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from .client import Agent

try:
    import setproctitle
except ImportError:  # pragma: no cover
    setproctitle = None


def main() -> None:
    p = argparse.ArgumentParser(prog="ib-agent", description="ib agent (guest)")
    p.add_argument("--brain", default="192.168.122.1", help="brain host (NAT gateway by default)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--hz", type=float, default=12.0, help="capture/sense rate")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if setproctitle is not None:
        setproctitle.setproctitle("ib")

    agent = Agent(args.brain, args.port, capture_hz=args.hz)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
