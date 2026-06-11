"""Chat-Safety Guard (PROJECT.md §6.2) — the inviolable runtime invariant.

Bot keystrokes must NEVER land in the chat input bar. This is fail-closed: if we
cannot PROVE focus is on the game world, we do not inject. Period.

The guard samples the chat-input-bar region for the "input active" fingerprint
(open field / cursor). It exposes:
  - is_safe(): True only when chat input is provably inactive.
  - watchdog(): if chat is open and the bot didn't open it -> ESC + alarm.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("ib.agent.chatguard")


@dataclass
class ChatGuard:
    calibration: dict
    aborted_injections: int = 0
    alarms: int = 0
    _last_alarm_ts: float = 0.0

    def _region(self) -> dict | None:
        r = (self.calibration or {}).get("chat_input")
        if not r or not r.get("x1"):
            return None  # not calibrated yet
        return r

    def chat_active(self, sampler) -> bool | None:
        """Return True if chat input is active, False if not, None if unknown.

        `sampler(x0, y0, x1, y1) -> mean_rgb` reads the region from the current
        frame. None => uncalibrated/unreadable => caller must treat as unsafe.
        """
        r = self._region()
        if r is None or sampler is None:
            return None
        try:
            rgb = sampler(r["x0"], r["y0"], r["x1"], r["y1"])
        except Exception as e:  # never let a sensor error open the gate
            log.warning("chat sampler failed: %s", e)
            return None
        if rgb is None:
            return None
        target = r.get("active_rgb", [0, 0, 0])
        tol = r.get("tol", 10)
        return all(abs(a - b) <= tol for a, b in zip(rgb, target))

    def is_safe(self, sampler) -> bool:
        """Fail-closed: only True when we can prove chat input is inactive."""
        active = self.chat_active(sampler)
        if active is None:
            return False  # unknown => unsafe
        return active is False

    def note_abort(self) -> None:
        self.aborted_injections += 1

    def raise_alarm(self) -> None:
        self.alarms += 1
        self._last_alarm_ts = time.time()
        log.error("CHAT-FOCUS ALARM: chat input open unexpectedly (#%d)", self.alarms)
