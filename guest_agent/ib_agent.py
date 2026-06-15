"""ib_agent — the in-guest reflex agent (iksar_buddy).

Runs INSIDE a crafter/healer VM, in the interactive session (scheduled task
`ibagent`, LogonType InteractiveToken — session 0 can't BitBlt or inject input).
It does the timing-critical sense->react loop LOCALLY (mss grab ~5ms + cv2/pixel
match + instant keypress), so reactions land in tens of ms instead of the host's
~170ms virsh-screenshot round-trip.

Comms is OUTBOUND-ONLY HTTP to the host dashboard (no inbound port on the guest —
firewall/opsec clean, no VM XML change):
  - poll  GET  {host}/api/agent/{bot}/command    -> what to do (idle | react | stop)
  - push  POST {host}/api/agent/{bot}/telemetry  -> live state + craft-done handoff

The hot loop never touches the network. This skeleton proves the channel + the
engine scaffold; the Forge counter ruleset plugs into react_* (see craft_reflex).

Config: C:\\ib\\agent\\agent.json  {host, bot, poll_hz}. No eq2/bot strings anywhere
observable (process is pythonw.exe; task is `ibagent`).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "agent.json"
LOG_PATH = HERE / "agent.log"

VERSION = "0.1.0"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def load_cfg() -> dict:
    cfg = {"host": "http://10.0.0.16:18081", "bot": "A", "poll_hz": 4.0}
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        log(f"cfg load failed ({e}); using defaults {cfg}")
    return cfg


class Agent:
    def __init__(self, cfg: dict) -> None:
        self.host = cfg["host"].rstrip("/")
        self.bot = cfg["bot"]
        self.period = max(0.1, 1.0 / float(cfg.get("poll_hz", 4.0)))
        self.cfg = cfg
        self.s = requests.Session()
        self.cmd_url = f"{self.host}/api/agent/{self.bot}/command"
        self.tele_url = f"{self.host}/api/agent/{self.bot}/telemetry"
        self.last_epoch = -1
        self.state = "idle"
        self.reactions = 0

    # -- comms -------------------------------------------------------------
    def poll_command(self) -> dict | None:
        try:
            r = self.s.get(self.cmd_url, timeout=4)
            if r.ok:
                return r.json()
        except requests.RequestException:
            return None
        return None

    def push(self, **extra) -> None:
        body = {"state": self.state, "ver": VERSION, "reactions": self.reactions, **extra}
        try:
            self.s.post(self.tele_url, json=body, timeout=4)
        except requests.RequestException:
            pass

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:
        log(f"ib_agent {VERSION} starting: bot={self.bot} host={self.host} "
            f"period={self.period:.2f}s")
        backoff = self.period
        while True:
            cmd = self.poll_command()
            if cmd is None:
                # host unreachable -> back off (cap 5s), keep trying
                backoff = min(5.0, backoff * 1.5)
                time.sleep(backoff)
                continue
            backoff = self.period
            epoch = int(cmd.get("epoch", 0))
            action = cmd.get("action", "idle")
            if epoch != self.last_epoch:
                self.last_epoch = epoch
                log(f"command epoch={epoch} action={action} {cmd}")
                self.on_command(action, cmd)
            self.push()
            time.sleep(self.period)

    def on_command(self, action: str, cmd: dict) -> None:
        """Dispatch a NEW command. Phase 1: just track state. The Forge counter
        ruleset (react until done) plugs in here in Phase 4."""
        if action in ("idle", "stop"):
            self.state = "idle"
        elif action == "react":
            self.state = "react"     # placeholder; craft_reflex.run() lands here next
        else:
            self.state = action


def main() -> int:
    cfg = load_cfg()
    while True:
        try:
            Agent(cfg).run()
        except Exception:
            log("FATAL loop crash:\n" + traceback.format_exc())
            time.sleep(2.0)          # never exit — the scheduled task stays resident
    return 0


if __name__ == "__main__":
    sys.exit(main())
