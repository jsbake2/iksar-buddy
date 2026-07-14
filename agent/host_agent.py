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
import os
import re
from pathlib import Path

_DAEMON_ENABLED = True    # in-guest key daemon fast path (~<0.1s vs ~0.5s one-shot)

# The daemon isn't in the VM base image, so a revert/reboot wipes it. The agent
# self-deploys it (script + task) when the heartbeat stops advancing. The ONE
# canonical script is infra/vm/ahk/key_daemon.ahk (the agent/ copy was a dup —
# REFACTOR P0.6); runs from C:\ib\keyd.ahk via the 'ibkeyd' task in the
# interactive session (same principal as the one-shot 'ibkey' task).
# NOTE on the path: deploy under C:\ib\ahk\ (where AutoHotkey64.exe lives — writes fine)
# and use the name ibkd.ahk, NOT keyd.ahk. Both C:\ib\keyd.ahk (07-12) and C:\ib\ahk\keyd.ahk
# (07-13) became un-writable — CreateFile fails even as SYSTEM with the file absent and no AHK
# running — while a FRESH filename in the same dir writes instantly. Cause unconfirmed (a
# poisoned path entry / filter keyed on that exact name), so we sidestep it with ibkd.ahk.
# The daemon's keycmd/heartbeat paths are absolute inside the script, so its location is free.
_DAEMON_GUEST_SCRIPT = r"C:\ib\ahk\ibkd.ahk"
_DAEMON_GUEST_XML = r"C:\ib\ahk\ibkeyd.xml"
_IBKEYD_XML = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <RegistrationInfo><URI>\\ibkeyd</URI></RegistrationInfo>\n'
    '  <Principals><Principal id="Author">'
    '<UserId>S-1-5-21-1061650457-3563521756-761907317-1000</UserId>'
    '<LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel>'
    '</Principal></Principals>\n'
    '  <Settings><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
    '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
    '<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
    '<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>'
    '<UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine></Settings>\n'
    # LogonTrigger: Windows auto-starts the daemon at the user's logon (which the VM does
    # at every boot via auto-login), so a reboot self-recovers with NO host redeploy — the
    # task persists in Task Scheduler and the script persists on C:\. The host only /Run's
    # it for an immediate start and health-checks it thereafter. (2026-07-13 reboot-proofing)
    '  <Triggers><LogonTrigger><Enabled>true</Enabled>'
    '<UserId>S-1-5-21-1061650457-3563521756-761907317-1000</UserId></LogonTrigger></Triggers>\n'
    '  <Actions Context="Author"><Exec>'
    '<Command>C:\\ib\\ahk\\AutoHotkey64.exe</Command>'
    '<Arguments>C:\\ib\\ahk\\ibkd.ahk</Arguments></Exec></Actions>\n'
    '</Task>')
import time

from shared import protocol as proto
from shared.guest import Guest
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
# The combat log we tail is the BOT'S OWN client log — i.e. the character selected
# in the dashboard (slot 0 = names[0], pushed via CONFIG). Templated on the character
# so a profile/character switch retargets the log without a restart.
EQ2_LOG_TMPL = (r"C:\Users\Public\Daybreak Game Company\Installed Games"
                r"\EverQuest II\logs\Wuoshi\eq2log_{char}.txt")
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


def _overlay_tunables() -> None:
    """Everything above is a FALLBACK default — thresholds.yaml (detection knobs)
    and calibration.yaml (healer_dom, eq2_log_template) override at startup
    (REFACTOR P1.2/P1.5). Names come via CONFIG from the brain, not from here."""
    global DOM, EQ2_LOG_TMPL, REZ_WINDOW, COMBAT_HP_DROP, COMBAT_DECAY_S
    global CHAT_HYSTERESIS_S, COMBAT_LOG_POLL_S
    from shared import tunables
    th, cal = tunables.thresholds(), tunables.calibration()
    DOM = cal.get("healer_dom") or DOM
    EQ2_LOG_TMPL = cal.get("eq2_log_template") or EQ2_LOG_TMPL
    REZ_WINDOW = float(th.get("rez_window_s", REZ_WINDOW))
    COMBAT_HP_DROP = float(th.get("combat_hp_drop", COMBAT_HP_DROP))
    COMBAT_DECAY_S = float(th.get("combat_decay_s", COMBAT_DECAY_S))
    CHAT_HYSTERESIS_S = float(th.get("chat_hysteresis_s", CHAT_HYSTERESIS_S))
    COMBAT_LOG_POLL_S = float(th.get("combat_log_poll_s", COMBAT_LOG_POLL_S))


_overlay_tunables()


def _poll_gpu(g: Guest) -> dict:
    """Run nvidia-smi IN the guest (the 4070 is passed through, so the host can't
    see it) via the qemu guest agent. Returns {} on any failure. Blocking; call
    from an executor and throttle (it's a ~1s guest round-trip)."""
    try:
        out = g.exec_out(NVSMI,
                         ["--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                          "--format=csv,noheader,nounits"], wait=3.5)
        util, mem, temp = (p.strip() for p in out.split(",")[:3])
        return {"gpu_util": int(util), "gpu_mem_mb": int(mem), "gpu_temp": int(temp)}
    except Exception:
        pass
    return {}


class HostAgent:
    def __init__(self, host: str, port: int, hz: float = 2.0) -> None:
        self.host, self.port = host, port
        self.period = 1.0 / hz
        self.sensor = HostSensor()
        self.g = Guest(DOM)         # the shared virsh/guest-agent I/O core
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
        # pre-pull trigger: when the TANK tells/group-messages the trigger string, fire pre_pull.
        self._prepull_tank = ""     # tank's name (lower), from CONFIG names[tank_slot]
        self._prepull_str = "incomming a"  # trigger substring (lower), from CONFIG prepull_trigger
        self._prepull_epoch = None  # highest prepull-line epoch already acted on
        self._prepull_pending = False  # set on a NEW trigger line, consumed into the next event
        self._armed = False         # injection master switch (off until owner arms)
        self._chat_safe = False     # latest chat-safety verdict (the inject gate)
        self._aborted = 0           # injections aborted because chat was unsafe
        self._group_target_keys = []  # slot -> F-key (from CONFIG)
        self._injecting = False     # serialize injects (don't overlap key sequences)
        # names + combat regex follow the active healer PROFILE (pushed via CONFIG):
        # Jenskin/Robskin (Defiler) vs Croolst/Paraphon (Fury). Default until CONFIG.
        self._names = dict(NAMES)
        self._name_re = NAME_RE
        self._inject_tok = int(time.time())   # seed from clock -> unique across restarts,
                                              # so the daemon never mistakes a new press for
                                              # a stale token it already ran
        self._daemon_ok = False     # is the in-guest key daemon alive? (health-checked)
        self._hb_prev = None        # last heartbeat mtime — alive = it ADVANCED (clock-jump safe)
        self._hb_misses = 0         # consecutive genuine stale reads (blank reads don't count)

    async def _ensure_inject_task_loop(self) -> None:
        """Health-check the persistent in-guest key DAEMON (fast path, ~<0.1s) and keep
        the one-shot 'ibkey' task enabled (fallback), both OFF the inject hot path. The
        daemon touches a heartbeat file ~1/s; if it's stale it's dead/missing so we
        (re)start ibkeyd and route presses through the one-shot fallback until it's
        healthy again. Best-effort; never raises."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                # ALIVE = the heartbeat mtime ADVANCED since last check. Comparing to the
                # previous reading (not to wall-clock 'now') is immune to the guest clock
                # jumping — which it does, and which the old age-based check mistook for a
                # healthy daemon while it was actually dead (fed a corpse, nothing injected).
                ticks = (await loop.run_in_executor(None, self._guest_read,
                    "(Get-Item C:\\ib\\keydaemon.hb -EA SilentlyContinue).LastWriteTime.Ticks") or "").strip()
                # A blank read is the guest-agent hiccuping under load (guest-exec returns
                # '' / '\n'), NOT proof the daemon died. Treating it as death made the health
                # check FLAP — one bad read dropped us to the slow one-shot fallback for that
                # cycle, and rapid presses got mutex-dropped (2026-07-12). Ignore blanks:
                # keep the current verdict and don't compare/advance _hb_prev.
                if ticks:                       # skip blank reads (guest-agent noise)
                    alive = self._hb_prev is not None and ticks != self._hb_prev
                    self._hb_prev = ticks
                    if alive:
                        if not self._daemon_ok:
                            log.info("key daemon healthy (hb advancing) — fast path on")
                        self._daemon_ok = True
                        self._hb_misses = 0
                    else:
                        # Require TWO consecutive genuine (non-blank) stale reads before
                        # declaring the daemon dead — one stale sample is within noise.
                        self._hb_misses += 1
                        if self._hb_misses >= 2:
                            if self._daemon_ok:
                                log.warning("key daemon not advancing — redeploy + one-shot fallback")
                            self._daemon_ok = False
                            # (re)deploy the task+script (handles a VM revert that wiped it),
                            # then nudge it in case it exists but exited.
                            await loop.run_in_executor(None, self._deploy_daemon)
                            await loop.run_in_executor(None, self._guest_read,
                                "schtasks.exe /Run /TN ibkeyd 2>&1 | Out-Null; 'ok'")
                await loop.run_in_executor(None, self._guest_read,
                    "Enable-ScheduledTask -TaskName ibkey -EA SilentlyContinue | Out-Null; 'ok'")
            except Exception as e:
                log.debug("ensure-inject-task error: %s", e)
            await asyncio.sleep(8)

    async def run(self) -> None:
        asyncio.create_task(self._combat_log_loop())   # runs independent of brain link
        asyncio.create_task(self._ensure_inject_task_loop())  # keep ibkey enabled (off hot path)
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
                gpu = await loop.run_in_executor(None, _poll_gpu, self.g)
                if gpu:
                    self._gpu = gpu
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

    def _log_path(self) -> str:
        """EQ2 chat-log path for the ACTIVE character (dashboard dropdown = names[0]).
        Falls back to the last-known/default if names aren't set yet."""
        char = (self._names.get(0) or "").strip() or "Jenskin"
        return EQ2_LOG_TMPL.format(char=char)

    async def _combat_log_loop(self) -> None:
        """Trip combat on RECENT group-named damage lines in Jenskin's EQ2 log.
        Each line is "(epoch)[date] text"; we read the guest's current epoch in the
        SAME call (the guest clock differs from the host's) and compare, so a hit
        counts only if it happened within COMBAT_DECAY_S. The group-name filter
        ignores the rest of the zone's combat spam. Runs even while disarmed."""
        loop = asyncio.get_running_loop()
        while True:
            # Rebuild each poll so the path follows the CURRENT character (names[0],
            # set from CONFIG on every profile switch) — no restart needed. Double any
            # ' so the config-owned path can't break out of the PS single-quotes.
            # FAST PATH (P2.3): ib_agent tails the log in-guest and mirrors the last
            # 250 lines to C:\ib\combat_tail.txt — read that tiny file when it's fresh
            # (< 5s old) instead of Get-Content -Tail 250 on the full log. Stale/absent
            # mirror (agent down) -> the old tail poll, so nothing regresses.
            log_path = self._log_path().replace("'", "''")
            ps = (
                "Write-Output ('NOW=' + [int][double]::Parse((Get-Date -UFormat %s))); "
                "$m = Get-Item 'C:\\ib\\combat_tail.txt' -EA SilentlyContinue; "
                "if ($m -and ((Get-Date) - $m.LastWriteTime).TotalSeconds -lt 5) "
                "{ Get-Content -LiteralPath $m.FullName } "
                f"elseif (Test-Path -LiteralPath '{log_path}') "
                f"{{ Get-Content -LiteralPath '{log_path}' -Tail 250 }}")
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
        # PRE-PULL trigger: a tell-to-you / group line FROM the tank containing the trigger
        # string -> fire pre_pull. Epoch-deduped (each line once) + baselined on first scan
        # so pre-existing log content never fires. Recent only.
        if self._prepull_tank and self._prepull_str:
            newest_pp = None
            for ln in lines[1:]:
                m = re.match(r"\((\d+)\)", ln)
                if not m:
                    continue
                low = ln.lower()
                if (self._prepull_tank in low and self._prepull_str in low
                        and ("tells you" in low or "the group" in low)):
                    ep = int(m.group(1))
                    if newest_pp is None or ep > newest_pp:
                        newest_pp = ep
            if newest_pp is not None:
                # Fire on any NEW (higher-epoch) trigger line that's RECENT — no first-scan
                # baseline (that ate the very first call). Recency (<=30s) keeps a stale line
                # in the startup tail from firing; epoch-dedup stops re-firing on re-reads.
                pp_new = newest_pp > (self._prepull_epoch or 0)
                self._prepull_epoch = max(self._prepull_epoch or 0, newest_pp)
                if pp_new and now_guest - newest_pp <= 30:
                    self._prepull_pending = True
                    log.info("PRE-PULL trigger: %s said %r", self._prepull_tank, self._prepull_str)
        # newest GROUP combat line (names a member + a combat action); ignore the
        # rest of the zone's combat. Recency is measured in the guest's own clock.
        newest = None
        for ln in lines[1:]:
            m = re.match(r"\((\d+)\)", ln)
            if m and COMBAT_RE.search(ln) and self._name_re.search(ln):
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
        """Run a PowerShell command in the guest and return its stdout (shared
        Guest.read_ps). Synchronous; call via run_in_executor."""
        return self.g.read_ps(ps)

    async def _recv_loop(self, reader: asyncio.StreamReader) -> None:
        loop = asyncio.get_running_loop()
        while True:
            msg = await proto.read_message(reader)
            if msg.type == proto.CONFIG:
                am = msg.data.get("ability_map") or {}
                self._group_target_keys = am.get("group_target_keys") or []
                names = msg.data.get("names") or am.get("names") or {}
                if names:
                    self._names = {int(k): v for k, v in names.items() if v}
                    # combat counts only if a GROUP MEMBER OTHER THAN the bot (slot 0)
                    # is named — rebuild the regex for the active profile's names.
                    others = [re.escape(v) for s, v in self._names.items() if s != 0 and v]
                    if others:
                        self._name_re = re.compile(r"\b(" + "|".join(others) + r")\b")
                # pre-pull trigger config: the TANK's name + the trigger string (the tank
                # whispers / group-messages "Incomming a <mob>" before a pull).
                tank_slot = int(am.get("tank_slot", 1))
                self._prepull_tank = (self._names.get(tank_slot) or "").lower()
                self._prepull_str = str(am.get("prepull_trigger") or "incomming a").lower()
                log.info("config: %d target keys, names=%s, prepull tank=%r str=%r",
                         len(self._group_target_keys), self._names,
                         self._prepull_tank, self._prepull_str)
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
        if role == "_reset_combat":
            # Force OOC NOW and discard all combat lines seen so far. _last_combat_epoch
            # already tracks the newest line, so auto-detection stays quiet until a
            # genuinely NEW combat line appears -- i.e. it unsticks but keeps auto.
            self._combat_until = 0.0
            log.info("COMBAT RESET (forced OOC; auto resumes on next new combat)"); return

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

    def _deploy_daemon(self) -> bool:
        """(Re)deploy the in-guest key daemon: push the script + 'ibkeyd' task and start
        it. Self-heals after a VM revert/reboot wipes it. Idempotent — safe to call while
        the daemon is alive (IgnoreNew makes the /Run a no-op). Best-effort; never raises."""
        script_path = (Path(__file__).resolve().parent.parent
                       / "infra" / "vm" / "ahk" / "key_daemon.ahk")
        try:
            script = script_path.read_bytes()
        except OSError as e:
            log.warning("daemon self-deploy: script missing on host: %s", e)
            return False
        try:
            # Only (re)write the SCRIPT if it's actually MISSING. push_bytes does a
            # Remove-then-write; under guest-agent load the write can fail AFTER the remove,
            # which would delete a perfectly good script (that's how the 2026-07-12 outage
            # kept re-killing itself). A present script persists on C:\ across reboots, so
            # leave it alone. CREATE (when missing) needs push_bytes ([IO.File]::WriteAllBytes)
            # — the guest FILE API (guest-file-open) can only overwrite, not create new files.
            # Script size: -1 = missing. A blank/failed read (guest-agent hiccup) parses to -2
            # -> assume PRESENT and never rewrite (its Remove-then-write would delete a good
            # script if the rewrite then failed — the self-inflicted daemon-kill loop, 2026-07-13).
            sz = (self._guest_read(
                f"if(Test-Path -LiteralPath '{_DAEMON_GUEST_SCRIPT}')"
                f"{{(Get-Item -LiteralPath '{_DAEMON_GUEST_SCRIPT}').Length}}else{{-1}}") or "").strip()
            try:
                n = int(sz)
            except ValueError:
                n = -2
            # Rewrite only when missing (-1) or truncated/corrupt (0..499 bytes). AHK holds its
            # OWN script file open while running, so overwriting a live script fails with a lock
            # — kill AHK first to release it, then push. (Full script is ~4.6 KB.)
            if n == -1 or (0 <= n < 500):
                self._guest_read("taskkill /IM AutoHotkey64.exe /F 2>&1 | Out-Null; 'ok'")
                if not self.g.push_bytes(script, _DAEMON_GUEST_SCRIPT):
                    log.warning("daemon self-deploy: script write failed")
                    return False
            # (Re)register the AUTO-START task. The XML carries a LogonTrigger, so once this
            # lands the task persists in Task Scheduler and Windows restarts the daemon on
            # every boot with NO host redeploy. Overwrite the tiny XML so trigger updates
            # propagate; /Create /F + /Run (IgnoreNew = harmless no-op if already alive).
            self.g.push_bytes(_IBKEYD_XML.encode("utf-16"), _DAEMON_GUEST_XML)
            self._guest_read(
                f"schtasks.exe /Create /TN ibkeyd /XML '{_DAEMON_GUEST_XML}' /F | Out-Null; "
                "schtasks.exe /Run /TN ibkeyd | Out-Null; 'ok'")
            log.info("ensured in-guest key daemon (script len=%s, auto-start task set)", sz)
            return True
        except Exception as e:
            log.warning("daemon self-deploy failed: %s", e)
            return False

    def _inject_daemon(self, seq: str) -> bool:
        """FAST path: hand the sequence to the always-running in-guest daemon by writing
        '<token>|<seq>' to keycmd.txt via the guest-agent file API (no process spawn, no
        Task Scheduler). The daemon polls ~15ms and injects. ~4x faster than the one-shot
        task. False on any write failure so the caller falls back."""
        try:
            self._inject_tok += 1
            self.g.file_write("C:\\ib\\keycmd.txt", f"{self._inject_tok}|{seq}".encode())
            return True
        except Exception as e:
            log.warning("daemon inject write failed (falling back): %s", e)
            return False

    def _inject_fast(self, seq: str) -> bool:
        """Write keys.txt via the guest-agent FILE API (no PowerShell cold-start) and
        trigger the interactive-session AHK via native schtasks (no PowerShell). Cuts
        ~2 PowerShell spawns (~0.5-1s) off the press-to-action path. False on any
        failure so the caller can fall back to the proven PowerShell path."""
        try:
            self.g.file_write("C:\\ib\\keys.txt", seq.encode())
            self.g.agent_cmd({"execute": "guest-exec", "arguments": {
                "path": "schtasks.exe", "arg": ["/Run", "/TN", "ibkey"]}})
            return True
        except Exception as e:
            log.debug("fast inject failed, falling back to PS: %s", e)
            return False

    def _inject(self, seq: str, role: str) -> None:
        """Write the key sequence to the guest and fire the Event-mode AHK task.
        Re-checks nothing here (the chat gate already passed in _on_command); keep
        the window between gate and press tiny by injecting immediately. The task's
        enabled-state is kept healthy off this path by _ensure_inject_task_loop."""
        # DAEMON path is LIVE (runSeq bug fixed in bddf41f, self-heal added in 3797982):
        # keycmd.txt + persistent in-guest AHK watcher, ~<0.1s. The one-shot ibkey
        # (~0.5s) and PS (~2s) paths below are FALLBACKS only. (REFACTOR P2.4)
        if _DAEMON_ENABLED and self._daemon_ok and self._inject_daemon(seq):
            log.info("INJECTED %s -> [%s]", role, seq)
            return
        # one-shot task via guest file-write + native schtasks (proven, ~0.5s)
        if self._inject_fast(seq):
            log.info("INJECTED %s -> [%s]", role, seq)
            return
        # fallback 2: the original PowerShell path (slowest but most proven).
        # seq is bot-generated key specs (quotes stripped by press_keys' contract),
        # but strip ' here too so nothing can break out of the PS single-quotes.
        safe = seq.replace("'", "")
        ps = (f"Set-Content C:\\ib\\keys.txt '{safe}' -NoNewline; "
              f"Start-ScheduledTask -TaskName ibkey")
        try:
            self.g.exec_start("powershell.exe", ["-NoProfile", "-Command", ps])
            log.info("INJECTED(ps) %s -> [%s]", role, seq)
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

    def _consume_prepull(self) -> bool:
        """One-shot: True once per detected trigger, then reset."""
        if self._prepull_pending:
            self._prepull_pending = False
            return True
        return False

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
            "names": {str(k): v for k, v in self._names.items()},
            "own_power": (own.get("power") or 0) / 100.0,
            "own_hp": (own.get("hp") or 0) / 100.0,
            "casting": False,                  # cast-bar sensing not built yet
            "in_combat": in_combat,            # inferred from HP drops (no game flag)
            "prepull_trigger": self._consume_prepull(),  # tank called incoming -> fire pre_pull
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
