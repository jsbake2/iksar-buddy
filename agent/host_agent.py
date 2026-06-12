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
import base64
import json
import logging
import os
import re
import subprocess
import time

from shared import protocol as proto
from shared.protocol import Message

from .host_sensor import HostSensor

log = logging.getLogger("ib.agent.host")

# slot -> character name (until OCR of the frame labels lands)
NAMES = {0: "Jenskin", 1: "Robskin"}

DOM = "iksar_buddy"
NVSMI = r"C:\Windows\System32\nvidia-smi.exe"

# Seconds after a revive during which a member's detriments are treated as
# (uncurable) revive sickness and do NOT trigger a cure. Observed ~80s at low
# level; generous default covers it. Tunable per the owner's SME knowledge.
REZ_WINDOW = 240.0
# Combat detection: no in-game combat flag is sensed, so infer it from HP. Any
# group member (or the healer) losing more than COMBAT_HP_DROP of health between
# cycles = took damage = in combat; it stays "in combat" until COMBAT_DECAY_S of
# no further hits. Healing raises HP (positive delta) and is ignored.
COMBAT_HP_DROP = 0.02
COMBAT_DECAY_S = 5.0
# Primary combat signal: the EQ2 chat log (Jenskin's client). Robskin always
# attacks in combat, so his damage lines are a clean trigger; mob->group damage
# and misses count too. HP-delta above is the fallback for when someone pulls
# without Robskin / the log read lags. Path is per server+character.
EQ2_LOG = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
           r"\EverQuest II\logs\Wuoshi\eq2log_Jenskin.txt")
COMBAT_LOG_POLL_S = 1.0
# Lines that only appear during combat (heals/regen/buffs deliberately excluded).
# Real format is "<X> hits <Y> for 35 piercing damage" (NOT "points of ...").
COMBAT_RE = re.compile(
    r"for \d+ \w+ damage|points of \w+ damage|scores a hit on|"
    r"tries to .*? but (?:misses|fails)|\bparries\b|\bripostes\b|"
    r"multi[- ]?attack|flurr", re.I)
# CRUCIAL: a combat line only counts as OUR combat if it names a group member
# OTHER THAN the bot itself (Jenskin = slot 0). Two reasons: (1) Jenskin's log
# captures the whole zone's combat, so we must filter to the group; (2) the bot's
# OWN assist makes Jenskin attack, which would generate "Jenskin hits..." lines
# that self-sustain combat forever (the "still spamming after combat" bug). The
# tank (Robskin) is the true combat signal -- he fights iff there's real combat.
NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for s, n in NAMES.items() if s != 0) + r")\b")

# Chat-safety blink hysteresis: any sign of an active chat input (text OR the
# cursor's lit phase) latches "chat busy" for this long, so a cursor blinking
# ~1Hz keeps an open-but-empty chat input flagged unsafe the whole time. A clear
# (black) line for longer than this reads safe.
CHAT_HYSTERESIS_S = 3.0


def _poll_gpu() -> dict:
    """Run nvidia-smi IN the guest (the 4070 is passed through, so the host can't
    see it) via the qemu guest agent. Returns {} on any failure. Blocking; call
    from an executor and throttle (it's a ~1s guest round-trip)."""
    def virsh(cmd):
        r = subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system",
                            "qemu-agent-command", DOM, json.dumps(cmd)],
                           capture_output=True, text=True, timeout=8)
        return json.loads(r.stdout)["return"]
    try:
        pid = virsh({"execute": "guest-exec", "arguments": {
            "path": NVSMI, "capture-output": True,
            "arg": ["--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                    "--format=csv,noheader,nounits"]}})["pid"]
        for _ in range(10):
            st = virsh({"execute": "guest-exec-status", "arguments": {"pid": pid}})
            if st.get("exited"):
                out = base64.b64decode(st.get("out-data", "")).decode(errors="replace")
                util, mem, temp = (p.strip() for p in out.split(",")[:3])
                return {"gpu_util": int(util), "gpu_mem_mb": int(mem), "gpu_temp": int(temp)}
            time.sleep(0.3)
    except Exception:
        pass
    return {}


class HostAgent:
    def __init__(self, host: str, port: int, hz: float = 2.0) -> None:
        self.host, self.port = host, port
        self.period = 1.0 / hz
        self.sensor = HostSensor()
        self._cycles = 0
        self._t0 = time.time()
        self._gpu = {}
        self._gpu_ts = 0.0
        self._dead_prev = {}        # slot -> was-dead last cycle
        self._revived_at = {}       # slot -> time of last dead->alive transition
        self._chat_busy_until = 0.0  # chat-safety blink hysteresis deadline
        self._prev_hp = {}          # slot -> last hp (0..1); "own" key for the healer
        self._combat_until = 0.0    # in-combat decays to OOC this many seconds after last hit
        self._last_combat_epoch = None  # highest combat-line epoch already acted on (no re-use)
        self._armed = False         # injection master switch (off until owner arms)
        self._chat_safe = False     # latest chat-safety verdict (the inject gate)
        self._aborted = 0           # injections aborted because chat was unsafe
        self._group_target_keys = []  # slot -> F-key (from CONFIG)
        self._injecting = False     # serialize injects (don't overlap key sequences)

    async def run(self) -> None:
        asyncio.create_task(self._combat_log_loop())   # runs independent of brain link
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                log.info("connected to brain %s:%d", self.host, self.port)
                await proto.write_message(writer, Message(proto.HELLO, {
                    "agent": "host_sensor", "capabilities": ["bars", "detriments", "inject"],
                    "inject": True}))
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
            # refresh GPU stats every ~12s (guest round-trip; don't do per cycle)
            if t - self._gpu_ts >= 12.0:
                self._gpu_ts = t
                g = await loop.run_in_executor(None, _poll_gpu)
                if g:
                    self._gpu = g
            if world is not None:
                await proto.write_message(writer, Message(proto.STATE_EVENT,
                                                          self._to_event(world)))
            self._cycles += 1
            if t - last_hb >= 2.0:
                hz = self._cycles / max(1e-6, time.time() - self._t0)
                await proto.write_message(writer, Message(proto.HEARTBEAT, {
                    "capture_hz": round(hz, 2), "ocr_conf": None, "log_fresh_s": None,
                    "armed": self._armed}))
                last_hb = t
            await asyncio.sleep(max(0, self.period - (time.time() - t)))

    async def _combat_log_loop(self) -> None:
        """Trip combat on RECENT group-named damage lines in Jenskin's EQ2 log.
        Each line is "(epoch)[date] text"; we read the guest's current epoch in the
        SAME call (the guest clock differs from the host's) and compare, so a hit
        counts only if it happened within COMBAT_DECAY_S. The group-name filter
        ignores the rest of the zone's combat spam. Runs even while disarmed."""
        loop = asyncio.get_running_loop()
        ps = (
            "Write-Output ('NOW=' + [int][double]::Parse((Get-Date -UFormat %s))); "
            f"if (Test-Path -LiteralPath '{EQ2_LOG}') "
            f"{{ Get-Content -LiteralPath '{EQ2_LOG}' -Tail 250 }}")
        while True:
            try:
                out = await loop.run_in_executor(None, self._guest_read, ps)
                if out:
                    self._scan_combat_lines(out)
            except Exception as e:  # never let this loop die
                log.debug("combat-log poll error: %s", e)
            await asyncio.sleep(COMBAT_LOG_POLL_S)

    def _scan_combat_lines(self, out: str) -> None:
        lines = out.splitlines()
        if not lines or not lines[0].startswith("NOW="):
            return
        try:
            now_guest = int(lines[0][4:])
        except ValueError:
            return
        # newest GROUP combat line (names a member + a combat action); ignore the
        # rest of the zone's combat. Recency is measured in the guest's own clock.
        newest = None
        for ln in lines[1:]:
            m = re.match(r"\((\d+)\)", ln)
            if m and COMBAT_RE.search(ln) and NAME_RE.search(ln):
                ep = int(m.group(1))
                if newest is None or ep > newest:
                    newest = ep
        if newest is None:
            return
        # Use each combat line ONCE: only trip on a line newer than the highest we
        # have already acted on. Re-reading the same 250-line tail can't re-enter
        # combat off stale lines (the "re-enters combat for no reason" bug). First
        # poll just baselines so pre-existing log content never trips.
        first = self._last_combat_epoch is None
        is_new = (not first) and newest > self._last_combat_epoch
        self._last_combat_epoch = max(self._last_combat_epoch or 0, newest)
        if is_new and now_guest - newest <= COMBAT_DECAY_S:
            self._combat_until = time.time() + max(0.0, COMBAT_DECAY_S - (now_guest - newest))

    def _guest_read(self, ps: str) -> str | None:
        """Run a PowerShell command in the guest and return its stdout (guest-exec
        with output capture). Synchronous; call via run_in_executor."""
        base = ["sudo", "-n", "virsh", "-c", "qemu:///system", "qemu-agent-command", DOM]
        try:
            r = subprocess.run(base + [json.dumps({"execute": "guest-exec", "arguments": {
                "path": "powershell.exe",
                "arg": ["-NoProfile", "-NonInteractive", "-Command", ps],
                "capture-output": True}})], capture_output=True, text=True, timeout=8)
            pid = json.loads(r.stdout)["return"]["pid"]
        except Exception:
            return None
        for _ in range(30):
            try:
                s = subprocess.run(base + [json.dumps({"execute": "guest-exec-status",
                                   "arguments": {"pid": pid}})],
                                   capture_output=True, text=True, timeout=8)
                st = json.loads(s.stdout)["return"]
            except Exception:
                return None
            if st.get("exited"):
                return base64.b64decode(st.get("out-data", "")).decode(errors="replace")
            time.sleep(0.15)
        return None

    async def _recv_loop(self, reader: asyncio.StreamReader) -> None:
        loop = asyncio.get_running_loop()
        while True:
            msg = await proto.read_message(reader)
            if msg.type == proto.CONFIG:
                am = msg.data.get("ability_map") or {}
                self._group_target_keys = am.get("group_target_keys") or []
                log.info("config: %d target keys", len(self._group_target_keys))
            elif msg.type == proto.COMMAND:
                await self._on_command(msg.data, loop)
            elif msg.type in (proto.WELCOME, proto.PING):
                log.debug("brain msg %s", msg.type)

    async def _on_command(self, data: dict, loop) -> None:
        role = data.get("role", "")
        # control verbs (pause/resume/estop) toggle the injection master switch.
        if role == "_resume":
            self._armed = True; log.info("ARMED"); return
        if role in ("_pause", "_estop"):
            self._armed = False; log.info("DISARMED (%s)", role); return

        key = (data.get("key") or "").strip()
        target = data.get("target_slot")
        manual = bool(data.get("manual"))
        # MANUAL button presses are explicit owner intent -> no arm needed. Only
        # the AUTO loop's commands require the bot to be armed.
        if not manual and not self._armed:
            log.info("COMMAND (disarmed auto, not injected): %s", role); return
        # THE INVIOLABLE GATE (manual + auto): never press a key unless chat is
        # provably safe -- a stray key in chat is the dead giveaway.
        if not self._chat_safe:
            self._aborted += 1
            log.warning("COMMAND ABORTED (chat unsafe): %s", role); return
        if not key or key == "none":
            log.info("COMMAND %s has no key", role); return
        if self._injecting:
            log.info("COMMAND %s dropped (inject busy)", role); return

        # build the key sequence: target F-key (if a slot) then the ability key.
        seq = []
        if isinstance(target, int) and 0 <= target < len(self._group_target_keys):
            tk = (self._group_target_keys[target] or "").strip()
            if tk:
                seq.append(tk)
        seq.append(key)
        self._injecting = True
        try:
            await loop.run_in_executor(None, self._inject, ",".join(seq), role)
        finally:
            self._injecting = False

    def _inject(self, seq: str, role: str) -> None:
        """Write the key sequence to the guest and fire the Event-mode AHK task.
        Re-checks nothing here (the chat gate already passed in _on_command); keep
        the window between gate and press tiny by injecting immediately."""
        ps = (f"Set-Content C:\\ib\\keys.txt '{seq}' -NoNewline; "
              f"Start-ScheduledTask -TaskName ibkey")
        try:
            subprocess.run(["sudo", "-n", "virsh", "-c", "qemu:///system",
                            "qemu-agent-command", DOM,
                            json.dumps({"execute": "guest-exec", "arguments": {
                                "path": "powershell.exe",
                                "arg": ["-NoProfile", "-Command", ps]}})],
                           capture_output=True, timeout=8)
            log.info("INJECTED %s -> [%s]", role, seq)
        except Exception as e:  # pragma: no cover
            log.warning("inject failed: %s", e)

    def _rez_suppressed(self, raw_members) -> set:
        """Track death->revive transitions; return slots currently within the
        post-revive window (their detriments = revive sickness, don't cure)."""
        now = time.time()
        suppressed = set()
        for m in raw_members:
            slot = m["slot"]
            dead = bool(m.get("dead", False))
            if self._dead_prev.get(slot) and not dead:     # just revived
                self._revived_at[slot] = now
            self._dead_prev[slot] = dead
            if now - self._revived_at.get(slot, -1e9) < REZ_WINDOW:
                suppressed.add(slot)
        return suppressed

    def _to_event(self, world: dict) -> dict:
        own = world.get("own") or {}
        raw = world.get("members", [])
        suppressed = self._rez_suppressed(raw)
        members = []
        cure_needed = False
        for m in raw:
            slot = m["slot"]
            # rez-sickness suppression: a just-revived member's detriments are
            # uncurable revive sickness, so never trigger a cure on them.
            cure = m.get("cure", False) and slot not in suppressed
            cure_needed = cure_needed or cure
            # rez_sick is for DISPLAY: only true while the member is in the window
            # AND actually shows a detriment icon. So the badge clears the moment
            # the sickness icon wears off, not when the 4-min window finally ends.
            rez_sick = slot in suppressed and bool(m.get("detriments"))
            members.append({
                "slot": slot,
                "hp": (m["hp"] or 0) / 100.0,
                "power": (m["power"] or 0) / 100.0,
                "ward": True,                  # ward sensing not built yet
                "dead": m.get("dead", False),
                "detriments": m.get("detriments", []),
                "cure": cure,
                "rez_sick": rez_sick,
            })
        # Chat-safety with blink hysteresis. raw chat_active True (text/cursor) OR
        # None (read failure) latches "busy" for CHAT_HYSTERESIS_S. safe only when
        # the game HUD is showing AND the chat line has been clear past the latch.
        safety = world.get("chat_safety") or {}
        game_present = bool(safety.get("game_present"))
        raw_active = safety.get("chat_active")          # True / False / None
        now = time.time()

        # Infer combat from HP drops (no in-game combat flag is sensed). Compare
        # each present member + the healer to last cycle; a drop past the threshold
        # = took damage = combat, latched for COMBAT_DECAY_S.
        cur_hp = {m["slot"]: (m["hp"] or 0) / 100.0 for m in raw}
        cur_hp["own"] = (own.get("hp") or 0) / 100.0
        for k, hp in cur_hp.items():
            prev = self._prev_hp.get(k)
            if prev is not None and prev - hp >= COMBAT_HP_DROP and hp > 0.01:
                self._combat_until = now + COMBAT_DECAY_S
        self._prev_hp = cur_hp
        in_combat = now < self._combat_until
        if raw_active is True or raw_active is None:
            self._chat_busy_until = now + CHAT_HYSTERESIS_S
        chat_busy = now < self._chat_busy_until
        chat_safe = game_present and not chat_busy
        self._chat_safe = chat_safe            # the inject gate reads this
        return {
            "members": members,
            "names": {str(k): v for k, v in NAMES.items()},
            "own_power": (own.get("power") or 0) / 100.0,
            "own_hp": (own.get("hp") or 0) / 100.0,
            "casting": False,                  # cast-bar sensing not built yet
            "in_combat": in_combat,            # inferred from HP drops (no game flag)
            "pending_cures": ["generic"] if cure_needed else [],
            "chat_safe": chat_safe,
            "chat_focus": {"game_present": game_present, "chat_active": chat_busy},
            "aborted_injections": self._aborted,
            "host": {"load": round(os.getloadavg()[0], 2), **self._gpu},
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
