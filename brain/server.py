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

# Cast/cooldown throttle. Without cast-bar sensing the loop re-decides every
# sensor cycle (~0.5s) and would re-issue the SAME action while it's still casting
# or before the screenshot reflects the result -- the "cured once then cast it 4
# more times" bug. GLOBAL_GCD caps how often ANY command goes out; the per-action
# cooldown blocks repeating the SAME (action,target) until it has had time to land
# and show. A DIFFERENT action (e.g. a heal after a cure) is NOT blocked, so
# healing is never starved. Values are land+sensor-lag estimates; tune per spell.
GLOBAL_GCD_S = 0.9
ACTION_COOLDOWN_S = {
    # cure tightened 2.5->1.6 to match the faster sense rate (~1.4Hz): a multi-detriment
    # member gets cleaned in ~1.6s/cure instead of ~3s, killing the "long wait" between cures.
    "cure": 1.6, "group_cure": 1.6,
    "ward": 5.0, "group_ward": 5.0,
    "group_heal": 2.0, "direct_heal": 1.6, "critical_heal": 1.4,
    "emergency_heal": 1.0, "emergency_ward": 1.2,
}
DEFAULT_COOLDOWN_S = 1.5
# Re-press the attack key every few seconds while in combat so the bot keeps
# assisting onto whatever the tank retargets. Only fires in the GAPS (when no
# heal/cure is needed this cycle) so it never steals a cast from healing. It only
# runs in IN_COMBAT, so it stops the moment combat-detection clears -- which is
# why group-name-filtered combat detection (crisp end-of-combat) matters here.
ASSIST_INTERVAL_S = 3.0


class Brain:
    def __init__(self, cfg: Config, telemetry: Telemetry) -> None:
        self.cfg = cfg
        self.telemetry = telemetry
        self.sm = StateMachine()
        self._agent: asyncio.StreamWriter | None = None
        self._seq = 0
        self._next_action_at = 0.0   # global min-gap deadline between commands
        self._cooldowns: dict = {}   # (role, target_slot) -> earliest repeat time
        self._last_assist = 0.0      # last time we pressed attack (combat re-assist)
        self._last_ward = 0.0        # last time we recast the tank ward (heartbeat)
        self._last_debuff = 0.0      # 2-man dps cycle: last debuff
        self._dps_spell_at = 0.0     # 2-man dps cycle: when to fire the scheduled spell_attack
        self._last_prepull = 0.0     # debounce the tank's incoming-call -> pre_pull

    # -- outbound ----------------------------------------------------------
    async def send(self, type_: str, **data) -> None:
        if self._agent is None:
            return
        self._seq += 1
        try:
            await proto.write_message(self._agent, Message(type_, data, seq=self._seq))
        except (ConnectionError, RuntimeError):
            pass

    async def push_config(self) -> None:
        """Send the active profile's keymap + names + calibration to the agent.
        Called on connect AND after a profile switch so the agent's targeting keys,
        and the character names it shows/uses for combat detection, follow the profile."""
        await self.send(proto.CONFIG, ability_map=self.cfg.ability_map,
                        calibration=self.cfg.calibration,
                        names=self.cfg.names)

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
        await self.push_config()
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
            if "armed" in msg.data:           # injection master switch state
                self.telemetry.update(running=bool(msg.data["armed"]))
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
            entered = self.sm.on_combat_signal(bool(d["in_combat"]))
            # On the OOC->IN_COMBAT edge, assist once: press the attack key (mode
            # auto -> needs the bot armed; the agent also gates on chat-safe).
            if entered and self.sm.state == State.IN_COMBAT:
                akey = self.cfg.key_for("attack")
                if akey and akey != "none":
                    tank_slot = int(self.cfg.ability_map.get("tank_slot", 0))
                    await self.send("command", role="attack", key=akey,
                                    target_slot=tank_slot, manual=False,
                                    reason="combat start -> assist tank")
                    self._last_assist = time.time()
                    log.info("combat start: assist tank (attack '%s')", akey)

        # PRE-PULL on the tank's incoming-call: the agent detected the trigger string in a
        # tell/group line from the tank -> cast the pre_pull macro (target the tank so its
        # debuff implied-targets the incoming mob). manual=False -> only when armed; the
        # agent also gates on chat-safe. Debounced so a repeated call can't spam it.
        if d.get("prepull_trigger"):
            now = time.time()
            ppk = self.cfg.key_for("pre_pull")
            if ppk and ppk != "none" and now - self._last_prepull >= 3.0:
                tank_slot = int(self.cfg.ability_map.get("tank_slot", 0))
                await self.send("command", role="pre_pull", key=ppk, target_slot=tank_slot,
                                manual=False, reason="tank called incoming -> pre-pull")
                self._last_prepull = now
                self.telemetry.push_event("cast", "pre-pull (tank called incoming)")
                log.info("pre-pull fired (tank incoming-call)")

        # Map each member's lit detriment cells to display type-labels. The 5
        # cells are ASSUMED to correspond positionally to the 5 cure categories;
        # curing is generic regardless, so this is display-only (owner can fix
        # the order). `cure` is the real, type-agnostic trigger.
        from .telemetry import CURE_TYPES, SLOT_ROLES
        names = d.get("names", {})
        slot_roles = self.cfg.ability_map.get("slot_roles") or SLOT_ROLES
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
                "role": slot_roles[slot] if slot < len(slot_roles) else "",
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

        # decide() returns a PRIORITY BURST. Fire the FIRST entry that's off cooldown +
        # past the GCD; over successive GCDs this pours the whole stack (all heal tiers +
        # wards) until the target recovers. If nothing fires (empty burst or all on
        # cooldown), fall through to the ward/assist heartbeats.
        actions = decide(world, self.cfg, self.sm.state)
        now = time.time()
        fired = False
        if now >= self._next_action_at:
            for action in actions:
                key = (action.role, action.target_slot)
                if now >= self._cooldowns.get(key, 0.0):
                    await self.push_command(action)
                    self._next_action_at = now + GLOBAL_GCD_S
                    self._cooldowns[key] = now + ACTION_COOLDOWN_S.get(
                        action.role, DEFAULT_COOLDOWN_S)
                    fired = True
                    break
        # Ward heartbeat: with no ward-bar sensing, recast the tank ward on a timer
        # while IN_COMBAT (a Defiler's core mitigation). Only in the gaps (nothing in the
        # burst fired) and only in combat -- if end-of-combat detection were loose this
        # would burn mana, which is why combat-end is kept tight. Interval is owner-
        # tunable (ward_heartbeat_s); 0/absent disables it.
        if not fired:
            hb = float(self.cfg.threshold("ward_heartbeat_s", 0) or 0)
            mr = self.cfg.maint_role                 # 'ward' (Defiler) or 'hot' (Fury)
            wkey = self.cfg.key_for(mr)
            if (hb > 0 and wkey and wkey != "none"
                    and self.sm.state == State.IN_COMBAT and self.sm.override is None
                    and now >= self._next_action_at and now - self._last_ward >= hb):
                tank_slot = int(self.cfg.ability_map.get("tank_slot", 0))
                await self.send("command", role=mr, key=wkey, target_slot=tank_slot,
                                manual=False, reason=f"{mr} heartbeat")
                self._last_ward = now
                self._next_action_at = now + GLOBAL_GCD_S
            # Pet/assist heartbeat: re-send the pet + assist (the attack key) on a timer
            # while IN_COMBAT so the pet stays on the tank's target (owner: pet sent on
            # combat START and PERIODICALLY). Targets the tank then presses attack, exactly
            # like the combat-start assist. Tunable assist_heartbeat_s (0/absent disables).
            ah = float(self.cfg.threshold("assist_heartbeat_s", ASSIST_INTERVAL_S) or 0)
            akey = self.cfg.key_for("attack")
            if (ah > 0 and akey and akey != "none"
                    and self.sm.state == State.IN_COMBAT and self.sm.override is None
                    and now >= self._next_action_at and now - self._last_assist >= ah):
                tank_slot = int(self.cfg.ability_map.get("tank_slot", 0))
                await self.send("command", role="attack", key=akey, target_slot=tank_slot,
                                manual=False, reason="pet/assist heartbeat")
                self._last_assist = now
                self._next_action_at = now + GLOBAL_GCD_S
            # COMBAT DEBUFF: in ANY group, debuff the tank's target every debuff_cycle_s.
            # Debuffs are high-value; the old spell-attack was puny and drained power, so it's
            # gone. SKIPPED under debuff_power_floor power so offense never starves the heals.
            # Targets the tank (-> EQ2 implied-target the mob). Lowest priority in the gap
            # (heals/wards/pet already had their shot) and only IN_COMBAT.
            elif (self.sm.state == State.IN_COMBAT and self.sm.override is None
                    and now >= self._next_action_at):
                cyc = float(self.cfg.threshold("debuff_cycle_s", 10.0) or 0)
                pfloor = float(self.cfg.threshold("debuff_power_floor", 0.50))
                dbk = self.cfg.key_for("debuff")
                if (cyc > 0 and dbk and dbk != "none" and world.own_power >= pfloor
                        and now - self._last_debuff >= cyc):
                    tank_slot = int(self.cfg.ability_map.get("tank_slot", 0))
                    await self.send("command", role="debuff", key=dbk, target_slot=tank_slot,
                                    manual=False, reason="combat debuff")
                    self._last_debuff = now
                    self._next_action_at = now + GLOBAL_GCD_S


async def serve(brain: Brain, host: str, port: int) -> asyncio.AbstractServer:
    server = await asyncio.start_server(brain.handle, host, port)
    log.info("transport listening on %s:%d", host, port)
    return server
