"""Agent transport client + sense loop.

Connects to the brain, streams sensed state + heartbeats, and applies inbound
COMMANDs through the guarded injector. The agent is DUMB: sense and press, no
decisions (PROJECT.md §2).
"""
from __future__ import annotations

import asyncio
import logging
import time

from shared import protocol as proto
from shared.protocol import Message

from .capture import Capture
from .chat_guard import ChatGuard
from .inject import Injector

log = logging.getLogger("ib.agent.client")


class Agent:
    def __init__(self, host: str, port: int, capture_hz: float = 12.0,
                 no_act: bool = False) -> None:
        self.host, self.port = host, port
        self.capture_hz = capture_hz
        self.no_act = no_act          # sense-only: log COMMANDs, never inject (validation)
        self.cap = Capture()
        self.guard = ChatGuard(calibration={})
        self.inj = Injector(self.guard)
        self.calibration: dict = {}
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0

    def _sampler(self, x0, y0, x1, y1):
        return self.cap.sample_region(x0, y0, x1, y1)

    async def _send(self, type_: str, **data) -> None:
        if self._writer is None:
            return
        self._seq += 1
        await proto.write_message(self._writer, Message(type_, data, seq=self._seq))

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except (ConnectionError, OSError) as e:
                log.info("brain link down (%s); retrying in 2s", e)
            self._writer = None
            await asyncio.sleep(2.0)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer
        log.info("connected to brain %s:%d", self.host, self.port)
        await self._send(proto.HELLO, role="defiler", caps=["pixel", "ocr", "inject"],
                         capture_hz=self.capture_hz)
        await asyncio.gather(self._sense_loop(), self._heartbeat_loop(), self._recv_loop(reader))

    async def _recv_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            msg = await proto.read_message(reader)
            if msg.type == proto.COMMAND:
                self._on_command(msg)
            elif msg.type == proto.CONFIG:
                self.calibration = msg.data.get("calibration", {}) or {}
                self.guard.calibration = self.calibration
                log.info("received config (calibration keys: %s)", list(self.calibration))
            elif msg.type == proto.WELCOME:
                log.info("welcomed by brain (protocol v%s)", msg.data.get("protocol"))

    def _on_command(self, msg: Message) -> None:
        role = msg.data.get("role", "")
        key = msg.data.get("key", "")
        if role.startswith("_"):  # control: _pause/_resume/_estop
            log.info("control: %s", role[1:])
            return
        if self.no_act:
            log.info("cmd %s key=%r -> (sense-only, not pressed)", role, key)
            return
        sent = self.inj.guarded_press(key, self._sampler)
        log.info("cmd %s key=%r -> %s", role, key, "sent" if sent else "BLOCKED(chat-safety)")

    async def _sense_loop(self) -> None:
        period = 1.0 / self.capture_hz
        while True:
            t0 = time.time()
            have = self.cap.grab()
            chat_safe = self.guard.is_safe(self._sampler) if have else False
            members = self.cap.read_hp_bars(self.calibration) if have else []
            await self._send(
                proto.STATE_EVENT,
                members=members,
                own_power=1.0,
                casting=False,
                pending_cures=[],
                ae_incoming=False,
                group_ward_up=True,
                chat_safe=chat_safe,
                aborted_injections=self.guard.aborted_injections,
            )
            await asyncio.sleep(max(0.0, period - (time.time() - t0)))

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._send(proto.HEARTBEAT, capture_hz=self.capture_hz,
                             ocr_conf=None, log_fresh_s=None)
            await asyncio.sleep(1.0)
