"""Host-side agent: streams HostSensor -> brain, logs COMMANDs (PROJECT.md §10).

Runs on the CachyOS host. Connects to the brain's transport server as THE agent,
pushes STATE_EVENT every cycle (~2Hz host capture) and a periodic HEARTBEAT, and
receives COMMAND/CONFIG.

ACT IS DISABLED here on purpose: COMMANDs are logged, not injected. Injection
needs (a) the Defiler keybind map (owner-blocked until level 10) and (b) the
real chat-safety guard proving focus is on the game world. Until both exist this
agent is sense-and-display only, which keeps the inviolable chat-safety invariant
trivially satisfied (nothing is ever typed). When wiring act: gate every inject
on a proven-safe chat focus and fail closed.

Run on host:  python3 -m agent.host_agent --brain 127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from shared import protocol as proto
from shared.protocol import Message

from .host_sensor import HostSensor

log = logging.getLogger("ib.agent.host")

# slot -> character name (until OCR of the frame labels lands)
NAMES = {0: "Jenskin", 1: "Robskin"}


class HostAgent:
    def __init__(self, host: str, port: int, hz: float = 2.0) -> None:
        self.host, self.port = host, port
        self.period = 1.0 / hz
        self.sensor = HostSensor()
        self._cycles = 0
        self._t0 = time.time()

    async def run(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                log.info("connected to brain %s:%d", self.host, self.port)
                await proto.write_message(writer, Message(proto.HELLO, {
                    "agent": "host_sensor", "capabilities": ["bars", "detriments"],
                    "inject": False}))
                await asyncio.gather(self._sense_loop(writer), self._recv_loop(reader))
            except (ConnectionError, OSError) as e:
                log.warning("brain link down (%s); retrying in 2s", e)
                await asyncio.sleep(2)

    async def _sense_loop(self, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        last_hb = 0.0
        while True:
            t = time.time()
            world = await loop.run_in_executor(None, self.sensor.read_world)
            if world is not None:
                await proto.write_message(writer, Message(proto.STATE_EVENT,
                                                          self._to_event(world)))
            self._cycles += 1
            if t - last_hb >= 2.0:
                hz = self._cycles / max(1e-6, time.time() - self._t0)
                await proto.write_message(writer, Message(proto.HEARTBEAT, {
                    "capture_hz": round(hz, 2), "ocr_conf": None, "log_fresh_s": None}))
                last_hb = t
            await asyncio.sleep(max(0, self.period - (time.time() - t)))

    async def _recv_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            msg = await proto.read_message(reader)
            if msg.type == proto.COMMAND:
                # ACT DISABLED: log only. See module docstring before enabling.
                log.info("COMMAND (not injected): role=%s key=%s target=%s reason=%s",
                         msg.data.get("role"), msg.data.get("key"),
                         msg.data.get("target_slot"), msg.data.get("reason"))
            elif msg.type in (proto.WELCOME, proto.CONFIG, proto.PING):
                log.debug("brain msg %s", msg.type)

    def _to_event(self, world: dict) -> dict:
        own = world.get("own") or {}
        members = []
        cure_needed = False
        for m in world.get("members", []):
            cure_needed = cure_needed or m.get("cure", False)
            members.append({
                "slot": m["slot"],
                "hp": (m["hp"] or 0) / 100.0,
                "power": (m["power"] or 0) / 100.0,
                "ward": True,                  # ward sensing not built yet
                "dead": m.get("dead", False),
                "detriments": m.get("detriments", []),
                "cure": m.get("cure", False),
            })
        return {
            "members": members,
            "names": {str(k): v for k, v in NAMES.items()},
            "own_power": (own.get("power") or 0) / 100.0,
            "own_hp": (own.get("hp") or 0) / 100.0,
            "casting": False,                  # cast-bar sensing not built yet
            "pending_cures": ["generic"] if cure_needed else [],
            "chat_safe": True,                 # act disabled; real guard before inject
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="127.0.0.1:8765")
    ap.add_argument("--hz", type=float, default=2.0)
    a = ap.parse_args()
    host, port = a.brain.split(":")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(HostAgent(host, int(port), a.hz).run())


if __name__ == "__main__":
    main()
