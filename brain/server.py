"""Transport server: accepts the agent, runs the sense->decide->act loop.

The agent streams STATE_EVENT / HEARTBEAT; the brain updates telemetry, runs the
Defiler policy, and replies with COMMAND messages. One agent at a time.
"""
from __future__ import annotations

import asyncio
import logging
import time

from shared import protocol as proto
from shared.protocol import Message

from .config import Config
from .policy import Action, Member, WorldState, decide
from .state import Override, State, StateMachine
from .telemetry import Telemetry

log = logging.getLogger("ib.brain.server")


class Brain:
    def __init__(self, cfg: Config, telemetry: Telemetry) -> None:
        self.cfg = cfg
        self.telemetry = telemetry
        self.sm = StateMachine()
        self._agent: asyncio.StreamWriter | None = None
        self._seq = 0

    # -- outbound ----------------------------------------------------------
    async def send(self, type_: str, **data) -> None:
        if self._agent is None:
            return
        self._seq += 1
        try:
            await proto.write_message(self._agent, Message(type_, data, seq=self._seq))
        except (ConnectionError, RuntimeError):
            pass

    async def push_command(self, action: Action) -> None:
        key = self.cfg.key_for(action.role)
        await self.send(proto.COMMAND, role=action.role, key=key,
                        target_slot=action.target_slot, reason=action.reason)
        self.telemetry.push_event("cast", f"{action.role} -> {action.reason}")

    # -- manual controls (from dashboard) ----------------------------------
    async def apply_override(self, ov: Override | None) -> None:
        if ov is None:
            self.sm.clear_override()
        else:
            self.sm.set_override(ov)
        self.telemetry.update(state=self.sm.state.value,
                              override=self.sm.override.value if self.sm.override else None)

    # -- connection handler ------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if self._agent is not None:
            log.warning("second agent from %s rejected", peer)
            writer.close()
            return
        self._agent = writer
        log.info("agent connected: %s", peer)
        self.telemetry.update(agent={**self.telemetry.snapshot["agent"], "connected": True})
        await self.send(proto.WELCOME, protocol=proto.PROTOCOL_VERSION)
        await self.send(proto.CONFIG, ability_map=self.cfg.ability_map,
                        calibration=self.cfg.calibration)
        try:
            while True:
                msg = await proto.read_message(reader)
                await self._dispatch(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except proto.ProtocolError as e:
            log.warning("protocol error: %s", e)
        finally:
            log.info("agent disconnected: %s", peer)
            self._agent = None
            self.telemetry.update(agent={**self.telemetry.snapshot["agent"], "connected": False})
            writer.close()

    async def _dispatch(self, msg: Message) -> None:
        if msg.type == proto.HEARTBEAT:
            latency_ms = round((time.time() - msg.ts) * 1000, 1)
            self.telemetry.update(agent={
                "connected": True, "latency_ms": latency_ms,
                "capture_hz": msg.data.get("capture_hz"),
                "ocr_conf": msg.data.get("ocr_conf"),
                "log_fresh_s": msg.data.get("log_fresh_s"),
            })
        elif msg.type == proto.STATE_EVENT:
            await self._on_state_event(msg)
        elif msg.type == proto.LOG:
            self.telemetry.push_event("log", str(msg.data.get("text", "")))
        elif msg.type == proto.HELLO:
            log.info("agent hello: %s", msg.data)

    async def _on_state_event(self, msg: Message) -> None:
        d = msg.data
        world = WorldState(
            members=[Member(**m) for m in d.get("members", [])],
            own_power=d.get("own_power", 1.0),
            casting=d.get("casting", False),
            pending_cures=d.get("pending_cures", []),
            ae_incoming=d.get("ae_incoming", False),
            group_ward_up=d.get("group_ward_up", True),
            prepull=d.get("prepull", False),
            chat_safe=d.get("chat_safe", True),
        )
        # coarse combat signal feeds the state machine (override may suppress).
        if "in_combat" in d:
            self.sm.on_combat_signal(bool(d["in_combat"]))

        # Map each member's lit detriment cells to display type-labels. The 5
        # cells are ASSUMED to correspond positionally to the 5 cure categories;
        # curing is generic regardless, so this is display-only (owner can fix
        # the order). `cure` is the real, type-agnostic trigger.
        from .telemetry import CURE_TYPES, SLOT_ROLES
        names = d.get("names", {})
        present_slots = {m.slot for m in world.members}
        member_rows = []
        for slot in range(6):
            m = world.member(slot)
            present = slot in present_slots
            rez_sick = bool(m is not None and getattr(m, "rez_sick", False))
            dets = []
            # rez-sick members DO have lit cells, but they're uncurable revive
            # sickness -- don't show them as cure-type detriments (that read as
            # "cursed"); the rez badge conveys the state instead.
            if m is not None and not rez_sick:
                for cell in (m.detriments or []):
                    idx = cell.get("cell") if isinstance(cell, dict) else cell
                    if isinstance(idx, int) and 0 <= idx < len(CURE_TYPES) \
                            and (not isinstance(cell, dict) or not cell.get("ignored")):
                        dets.append(CURE_TYPES[idx])
            member_rows.append({
                "slot": slot,
                "name": names.get(str(slot), names.get(slot, "")),
                "role": SLOT_ROLES[slot] if slot < len(SLOT_ROLES) else "",
                "present": present,
                "hp": (m.hp if m is not None else 1.0),
                "ward": (m.ward if m is not None else True),
                "dead": (m.dead if m is not None else False),
                "power": (m.power if m is not None else 1.0),
                "critical": bool(m is not None and m.hp < float(self.cfg.thresholds.get("tank_emergency_hp", 0.35))),
                "detriments": dets,
                "rez_sick": rez_sick,
            })

        cf = d.get("chat_focus") or {}
        self.telemetry.update(
            state=self.sm.state.value,
            override=self.sm.override.value if self.sm.override else None,
            own={"power": world.own_power, "hp": d.get("own_hp", 1.0),
                 "casting": world.casting},
            chat_focus={"safe": world.chat_safe,
                        "game_present": cf.get("game_present"),
                        "chat_active": cf.get("chat_active"),
                        "aborted_injections": d.get("aborted_injections", 0)},
            host=d.get("host", {}),
        )
        self.telemetry.set_members(member_rows)

        action = decide(world, self.cfg, self.sm.state)
        if action is not None:
            await self.push_command(action)


async def serve(brain: Brain, host: str, port: int) -> asyncio.AbstractServer:
    server = await asyncio.start_server(brain.handle, host, port)
    log.info("transport listening on %s:%d", host, port)
    return server
