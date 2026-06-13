"""Forge telemetry: per-bot crafting status + a shared event stream, published to
dashboard websockets. Mirrors brain/telemetry.py's pub/sub shape so the frontend
conventions (snapshot + /ws) carry straight over.

The snapshot is the single source of truth the dashboard renders:

    {
      "bots": { "A": <bot snapshot>, "B": <bot snapshot> },
      "order": ["A", "B"],
      "events": [ {ts, bot, kind, detail}, ... ],
      "trade_classes": [...],
    }

A <bot snapshot> is everything one bot panel needs (see DEFAULT_BOT)."""
from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any

# craft-loop states a bot moves through (drives the state pill + colors).
STATES = ["off", "idle", "launching", "selecting", "crafting",
          "waiting_power", "paused", "done", "error"]

# What a fresh bot panel shows before anything runs.
DEFAULT_BOT: dict[str, Any] = {
    "id": "", "label": "", "dom": "", "character": "", "spice_port": None,
    "enabled": False,
    "vm_running": False,
    "state": "off",
    "mode": "single",            # single | writ
    "trade_class": "",
    "recipe": "",                # current recipe name
    "count": {"done": 0, "total": 0},     # crafts of the current recipe
    "item": {"idx": 0, "total": 0},       # position in a writ queue
    "queue": [],                 # [{name, count, done}] writ/batch item list
    "durability_mode": None,     # "progress" | "durability" | None
    "power": 1.0,                # 0..1
    "power_gated": False,
    "reactions": 0,              # reaction events countered this session
    "crafts_done": 0,            # total crafts completed this session
    "crafts_per_hr": 0,
    "started_at": None,
    "last_event": "",
    "log": [],                   # per-bot console lines (most recent last)
}


class ForgeTelemetry:
    def __init__(self, trade_classes: list[str] | None = None,
                 class_chars: dict | None = None) -> None:
        self._bots: dict[str, dict] = {}
        self._order: list[str] = []
        self._events: list[dict] = []
        self._trade_classes = trade_classes or []
        self._class_chars = class_chars or {}     # tradeskill -> character
        self._subs: set[asyncio.Queue] = set()

    def set_class_chars(self, mapping: dict) -> None:
        self._class_chars = mapping or {}
        self._publish()

    # -- bot registry ------------------------------------------------------
    def add_bot(self, cfg: dict) -> None:
        b = deepcopy(DEFAULT_BOT)
        for k in ("id", "label", "dom", "character", "spice_port", "enabled"):
            if k in cfg:
                b[k] = cfg[k]
        b["state"] = "idle" if b["enabled"] else "off"
        self._bots[b["id"]] = b
        if b["id"] not in self._order:
            self._order.append(b["id"])

    def bot(self, bot_id: str) -> dict | None:
        return self._bots.get(bot_id)

    # -- mutation ----------------------------------------------------------
    def update_bot(self, bot_id: str, **fields) -> None:
        b = self._bots.get(bot_id)
        if b is None:
            return
        b.update(fields)
        self._publish()

    def push_log(self, bot_id: str, line: str, keep: int = 200) -> None:
        b = self._bots.get(bot_id)
        if b is None:
            return
        b["log"] = (b["log"] + [{"ts": time.time(), "text": line}])[-keep:]
        b["last_event"] = line

    def push_event(self, bot_id: str, kind: str, detail: str, keep: int = 200) -> None:
        self._events = (self._events + [{
            "ts": time.time(), "bot": bot_id, "kind": kind, "detail": detail,
        }])[-keep:]
        self._publish()

    # -- snapshot + pub/sub ------------------------------------------------
    @property
    def snapshot(self) -> dict:
        return {
            "bots": self._bots,
            "order": self._order,
            "events": self._events,
            "trade_classes": self._trade_classes,
            "class_chars": self._class_chars,
            "ts": time.time(),
        }

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _publish(self) -> None:
        snap = self.snapshot
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass

    # broadcast even when nothing structurally changed (sim ticks)
    def tick(self) -> None:
        self._publish()
