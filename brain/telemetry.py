"""Telemetry hub: holds the latest world/health snapshot and fans out updates
to dashboard websocket subscribers."""
from __future__ import annotations

import asyncio
import time
from typing import Any


class Telemetry:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self.snapshot: dict[str, Any] = {
            "state": "OOC",
            "override": None,
            "agent": {"connected": False, "latency_ms": None, "capture_hz": None,
                      "ocr_conf": None, "log_fresh_s": None},
            "chat_focus": {"safe": None, "aborted_injections": 0},
            "members": [],          # [{slot, hp, ward}]
            "own": {"power": None, "casting": None},
            "events": [],           # rolling cast/cure/rez event stream
            "updated": time.time(),
        }

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def update(self, **fields: Any) -> None:
        self.snapshot.update(fields)
        self.snapshot["updated"] = time.time()
        self._broadcast()

    def push_event(self, kind: str, detail: str) -> None:
        evs = self.snapshot["events"]
        evs.append({"ts": time.time(), "kind": kind, "detail": detail})
        del evs[:-100]  # keep last 100
        self._broadcast()

    def _broadcast(self) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(self.snapshot)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
