"""Keypress injection, gated by the Chat-Safety Guard.

Every press goes through `guarded_press`: prove chat is inactive -> press; else
abort, ESC, re-verify (PROJECT.md §6.2). The brain sends ROLE+key; the agent is
the only thing that ever touches the keyboard, and only through this gate.

Backends (native Windows input is the reliable path — see session log; QEMU-level
injection is not):
  - SendInputBackend : ctypes SendInput (production, in-guest).
  - NullBackend      : logs only (dev / non-Windows).
"""
from __future__ import annotations

import logging
import platform
import time

from .chat_guard import ChatGuard

log = logging.getLogger("ib.agent.inject")


class Backend:
    def tap(self, key: str) -> None: ...
    def esc(self) -> None: self.tap("Escape")


class NullBackend(Backend):
    def tap(self, key: str) -> None:
        log.info("[null-inject] tap %r", key)


class SendInputBackend(Backend):
    """Windows native SendInput via ctypes. Reliable in the interactive session.

    (The AHK route we used for bootstrap also works; SendInput keeps the runtime
    dependency-free. Filled in against config'd vk codes during agent bring-up.)
    """

    def tap(self, key: str) -> None:  # pragma: no cover - Windows only
        import ctypes

        vk = _VK.get(key.lower())
        if vk is None:
            log.warning("no vk mapping for key %r", key)
            return
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.015)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# Minimal vk map; extend as keybinds are assigned. F-keys + digits are the
# safe keyspace (avoid chat/reply triggers).
_VK = {f"f{i}": 0x6F + i for i in range(1, 13)}
_VK.update({str(d): 0x30 + d for d in range(10)})
_VK.update({"escape": 0x1B, "space": 0x20, "enter": 0x0D})


def make_backend() -> Backend:
    return SendInputBackend() if platform.system() == "Windows" else NullBackend()


class Injector:
    def __init__(self, guard: ChatGuard, backend: Backend | None = None) -> None:
        self.guard = guard
        self.backend = backend or make_backend()

    def guarded_press(self, key: str, sampler) -> bool:
        """Press `key` only if chat focus is provably safe. Return True if sent."""
        if not key:
            return False
        if self.guard.is_safe(sampler):
            self.backend.tap(key)
            return True
        # Not safe: abort, ESC to close any stray input, re-verify, do NOT retry blind.
        self.guard.note_abort()
        self.backend.esc()
        time.sleep(0.05)
        if self.guard.is_safe(sampler):
            self.backend.tap(key)
            return True
        self.guard.raise_alarm()
        return False
