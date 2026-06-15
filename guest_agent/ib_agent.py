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
import threading
import time
import traceback
from pathlib import Path

import requests

try:
    from craft_reflex import CraftReflex
except Exception:                      # noqa: BLE001 — skeleton still runs without it
    CraftReflex = None
try:
    from heal_reflex import HealReflex
except Exception:                      # noqa: BLE001
    HealReflex = None

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


def _load_local(name: str) -> dict:
    """Load a ruleset shipped to the guest (e.g. heal.json) when the command doesn't
    carry one — lets the healer run before the host-brain integration exists."""
    try:
        return json.loads((HERE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"local ruleset {name} load failed: {e}")
        return {}


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
        # reflex (craft react loop) runs in its own thread; the poll loop watches it
        self.reflex = None
        self.reflex_thread = None
        self.react_epoch = 0
        self.done_epoch = None          # the react_epoch whose craft finished
        self._stop_flag = False

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
        rx = self.reflex
        body = {"state": self.state, "ver": VERSION,
                "reactions": getattr(rx, "reactions", self.reactions) if rx else self.reactions,
                "epoch": self.react_epoch,
                "done": (self.done_epoch == self.react_epoch and self.react_epoch != 0),
                **extra}
        if rx is not None and hasattr(rx, "heals"):        # healer telemetry
            body["heals"] = getattr(rx, "heals", 0)
            body["cures"] = getattr(rx, "cures", 0)
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
        """Dispatch a NEW command (called once per epoch). 'react' = host handed off a
        running craft; 'heal' = run the healer monitor loop; both spin a reflex thread.
        'idle'/'stop' = halt it."""
        if action == "react":
            if not self._start_reflex(CraftReflex, cmd, "react", "craft_reflex"):
                return
            self.react_epoch = int(cmd.get("epoch", 0))
            self.done_epoch = None
        elif action == "heal":
            rs = cmd if cmd.get("pixels") else _load_local("heal.json")
            if not self._start_reflex(HealReflex, rs, "heal", "heal_reflex"):
                return
        else:                            # idle / stop / anything else -> halt the reflex
            self._stop_reflex()
            self.state = "idle"

    def _start_reflex(self, cls, ruleset, state, name) -> bool:
        if cls is None or not ruleset:
            log(f"{state} requested but {name} unavailable / no ruleset")
            self.state = "error"
            return False
        self._stop_reflex()
        self._stop_flag = False
        self.reflex = cls(ruleset, log, should_stop=lambda: self._stop_flag)
        self.reflex_thread = threading.Thread(target=self._run_reflex, daemon=True)
        self.reflex_thread.start()
        self.state = state
        return True

    def _run_reflex(self) -> None:
        try:
            ok = self.reflex.run()
        except Exception:                # noqa: BLE001
            log("reflex crashed:\n" + traceback.format_exc())
            ok = False
        if ok:
            self.done_epoch = self.react_epoch
        self.state = "idle"

    def _stop_reflex(self) -> None:
        self._stop_flag = True
        t = self.reflex_thread
        if t and t.is_alive():
            t.join(timeout=2.0)


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
