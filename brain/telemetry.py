"""Telemetry hub: holds the latest world/health snapshot and fans out updates
to dashboard websocket subscribers.

The data model is informed by the prior two-box healer (sensed per-member HP at
standard+critical thresholds, per-member detriments nox/ele/tra/arc/curse, self
mana-gate, group size, a recent-command history) and the Defiler ward loop.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

CURE_TYPES = ["noxious", "elemental", "trauma", "arcane", "curse"]
SLOT_ROLES = ["healer", "tank", "support", "support", "dps", "dps"]  # default labels


def _member(slot: int) -> dict[str, Any]:
    return {
        "slot": slot,
        "name": "",
        "present": False,
        "hp": 1.0,          # 0..1
        "critical": False,  # below the critical threshold (rare path)
        "ward": True,       # ward icon present
        "dead": False,
        "detriments": [],   # subset of CURE_TYPES currently afflicting this member
    }


class Telemetry:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self.snapshot: dict[str, Any] = {
            "state": "OOC",
            "override": None,
            "running": False,        # heal loop armed?
            "group_size": 0,
            "agent": {"connected": False, "latency_ms": None, "capture_hz": None,
                      "ocr_conf": None, "log_fresh_s": None},
            "chat_focus": {"safe": None, "aborted_injections": 0, "alarms": 0},
            "members": [_member(i) for i in range(6)],
            "own": {"power": 1.0, "casting": False, "mana_gated": False},
            "events": [],            # rolling cast/cure/rez/control event stream
            "notice": None,          # latest toast-worthy notification
            "vm": {"name": "iksar_buddy", "running": None, "ip": None},
            "host": {},              # host load + passed-through 4070 stats
            "updated": time.time(),
        }

    # -- pub/sub -----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def update(self, **fields: Any) -> None:
        self.snapshot.update(fields)
        self.snapshot["updated"] = time.time()
        self._broadcast()

    def set_members(self, members: list[dict[str, Any]]) -> None:
        self.snapshot["members"] = members
        self.snapshot["group_size"] = sum(1 for m in members if m.get("present"))
        self.snapshot["updated"] = time.time()
        self._broadcast()

    def push_event(self, kind: str, detail: str) -> None:
        evs = self.snapshot["events"]
        evs.append({"ts": time.time(), "kind": kind, "detail": detail})
        del evs[:-120]
        self._broadcast()

    def notify(self, title: str, detail: str = "", level: str = "warn",
               sys: bool = True) -> None:
        """Raise a toast-worthy notification: the dashboard pops a stacking toast and
        fires an OS notification (browser Notification API). Also logged to the event
        stream. level: info | good | warn | error."""
        self.snapshot["notice"] = {"ts": time.time(), "title": title,
                                   "detail": detail, "level": level, "sys": sys}
        self.push_event("notify", f"{title}{': ' + detail if detail else ''}")
        try:                                          # phone push (ntfy) — best-effort
            from shared import push as _push
            _push.push(title, detail, level)
        except Exception:
            pass

    def _broadcast(self) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(self.snapshot)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
